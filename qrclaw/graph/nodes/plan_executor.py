"""
PlanExecutor 节点

职责：驱动 Plan-and-Execute with Replanning 循环。

执行流程：
  while 剩余步骤不为空：
    1. 取下一层（in_degree=0 的步骤）
    2. 单步串行 / 多步并行 执行
    3. 把执行结果追加到 past_steps
    4. Replanner 评估：目标已达成 → DONE，否则更新剩余步骤（可能调整方向）
  结束后交主 agent 整合

串行步骤：子 session 继承主 session 全部消息，执行完增量追加回主 session
并行步骤：子 session 独立，注入前置步骤结果摘要，执行完只追加最终结果到主 session
"""
import threading
import queue
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from qrclaw.memory.context.session import Session
from qrclaw.memory.context.step_result import StepResult
from qrclaw.memory.context.context_manager import get_context_manager
from qrclaw.workspace import Workspace
from qrclaw.graph.executor import get_next_layer
from qrclaw.graph.nodes.replanner import ReplannerNode
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.plan_executor")


class PlanExecutorNode:

    def __init__(self):
        self.replanner = ReplannerNode()

    def run(
        self,
        session: Session,
        console: Console,
        workspace: Workspace,
        auto_confirm: bool,
        run_sub_agent_fn,
        react_loop_fn,
    ) -> str:
        ctx = get_context_manager()
        ps = ctx.plan_state

        while ps.remaining:
            layer = get_next_layer(ps.remaining)
            if not layer:
                logger.error("剩余步骤存在循环依赖，中止执行")
                console.print("[red]错误：步骤存在循环依赖，中止执行[/red]")
                break

            is_serial = len(layer) == 1

            if is_serial:
                step = layer[0]
                console.print(f"[yellow]→ {step.description} (串行)[/yellow]")
                result = self._run_serial(step, ps.goal, ps.past_steps, workspace, run_sub_agent_fn, console)
                ctx.add_step_result(StepResult(
                    step_id=step.id,
                    description=step.description,
                    output=result,
                ))
            else:
                console.print(f"[yellow]→ 并行执行 {len(layer)} 个步骤[/yellow]")
                for step in layer:
                    console.print(f"  [dim]Step {step.id}:[/dim] {step.description}")
                results = self._run_parallel(layer, ps.goal, ps.past_steps, workspace, run_sub_agent_fn, console)
                for step in layer:
                    ctx.add_step_result(StepResult(
                        step_id=step.id,
                        description=step.description,
                        output=results.get(step.id, ""),
                    ))

            # 从剩余步骤中移除已完成的这一层
            done_ids = {s.id for s in layer}
            ps.remaining = [s for s in ps.remaining if s.id not in done_ids]

            # 不管 remaining 是否为空，都让 Replanner 评估一次
            # 避免最后一层并行完成后跳过 Replanner 直接交给主 agent
            console.print("\n[dim]🔄 重新评估剩余计划...[/dim]")
            new_remaining = self.replanner.run()

            if new_remaining is None:
                console.print("[bold green]✅ Replanner 判断目标已达成[/bold green]\n")
                ps.remaining = []
                break

            if new_remaining != ps.remaining:
                console.print(f"[cyan]📝 Replanner 调整了计划，剩余 {len(new_remaining)} 步[/cyan]")
                for s in new_remaining:
                    console.print(f"  [yellow]Step {s.id}[/yellow] {s.description}")
            ctx.update_remaining(new_remaining)

        # 所有步骤执行完毕，把 past_steps 汇总写入主 session，交主 agent 整合
        summary_lines = ["## 计划执行完成", f"**目标：** {ps.goal}", ""]
        for sr in ps.past_steps:
            summary_lines.append(f"### {sr.description}")
            summary_lines.append(sr.output)
            summary_lines.append("")
        summary = "\n".join(summary_lines)

        session.add({
            "role": "user",
            "content": f"以上是任务「{ps.goal}」的执行结果，请根据这些结果给出最终的整合回复。\n\n{summary}",
        })
        ctx.clear_plan()
        return react_loop_fn()

    def _run_serial(self, step, goal: str, past_steps: list[StepResult], workspace, run_sub_agent_fn, console) -> str:
        prior_context = ""
        if past_steps:
            prior_context = "\n\n【前置步骤结果】\n" + "\n---\n".join(
                sr.to_context_prompt() for sr in past_steps
            )

        ps = get_context_manager().plan_state
        project_path_hint = f"\n\n【项目根目录】{ps.project_path}" if ps and ps.project_path else ""

        task = (
            f"【计划目标】{goal}"
            f"{project_path_hint}"
            f"{prior_context}\n\n"
            f"【当前任务】{step.description}\n\n"
            f"【要求】只完成当前任务。完成后返回详细的结果摘要，如果有路径请使用绝对路径，禁止使用相对路径，包括：做了什么、发现了什么、产出了哪些文件。"
        )
        result, _ = run_sub_agent_fn(
            task, workspace, f"step-{step.id}",
            console=console,
        )
        logger.info(f"Step {step.id} 串行完成")
        return result

    def _run_parallel(self, layer, goal: str, past_steps: list[StepResult], workspace, run_sub_agent_fn, console) -> dict[int, str]:
        results: dict[int, str] = {}
        lock = threading.Lock()
        notify_queue: queue.Queue = queue.Queue()

        # 构建前置步骤上下文（用 past_steps 里的真实结果）
        prior_context = ""
        if past_steps:
            prior_context = "\n\n【前置步骤结果】\n" + "\n---\n".join(
                sr.to_context_prompt() for sr in past_steps
            )

        ps = get_context_manager().plan_state
        project_path_hint = f"\n【项目根目录】{ps.project_path}" if ps and ps.project_path else ""

        def _run(step):
            task = (
                f"【计划目标】{goal}\n"
                f"{project_path_hint}"
                f"【步骤】{step.description}"
                f"{prior_context}\n\n"
                f"【要求】完成上述步骤。完成后返回详细的结果摘要，包括：做了什么、发现了什么、产出了哪些文件。"
            )
            try:
                result, _ = run_sub_agent_fn(
                    task, workspace, f"step-{step.id}",
                    console=None,
                )
                with lock:
                    results[step.id] = result
                notify_queue.put(("ok", step.id, step.description, result))
            except Exception as e:
                logger.error(f"Step {step.id} 执行失败: {e}", exc_info=True)
                with lock:
                    results[step.id] = f"执行失败: {e}"
                notify_queue.put(("err", step.id, step.description, str(e)))

        threads = [
            threading.Thread(target=_run, args=(step,), name=f"plan-step-{step.id}", daemon=True)
            for step in layer
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 打印结果
        while not notify_queue.empty():
            item = notify_queue.get()
            if item[0] == "ok":
                _, sid, desc, result = item
                short_desc = desc[:40] + ("..." if len(desc) > 40 else "")
                console.print(f"[bold green]✅ 完成[/bold green] {short_desc}")
                console.print(Panel(Text(result), title=f"[bold green]汇报[/bold green]", border_style="green", expand=False))
            else:
                _, sid, desc, err = item
                console.print(f"[bold red]❌ 失败[/bold red]: {err} ({desc})")

        return results
