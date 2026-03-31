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
from qrclaw.memory.session import Session
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
        plan,
        session: Session,
        console: Console,
        workspace: Workspace,
        auto_confirm: bool,
        run_sub_agent_fn,
        react_loop_fn,
    ) -> str:
        console.print(f"\n[bold cyan]📋 执行计划：{plan.goal}[/bold cyan]")
        for step in plan.steps:
            dep = f"  [dim]依赖 Step {step.depends_on}[/dim]" if step.depends_on else "  [dim]可并行[/dim]"
            console.print(f"  [yellow]Step {step.id}[/yellow] {step.description}{dep}")
        console.print()

        remaining = list(plan.steps)
        past_steps: list[tuple[str, str]] = []  # (步骤描述, 执行结果)

        while remaining:
            layer = get_next_layer(remaining)
            if not layer:
                logger.error("剩余步骤存在循环依赖，中止执行")
                console.print("[red]错误：步骤存在循环依赖，中止执行[/red]")
                break

            is_serial = len(layer) == 1

            if is_serial:
                step = layer[0]
                console.print(f"[yellow]→ Step {step.id}[/yellow] {step.description} [dim](串行)[/dim]")
                result = self._run_serial(step, plan, past_steps, workspace, run_sub_agent_fn, console)
                plan.mark_done(step.id)
                past_steps.append((step.description, result))
            else:
                console.print(f"[yellow]→ 并行执行 {len(layer)} 个步骤[/yellow]")
                for step in layer:
                    console.print(f"  [dim]Step {step.id}:[/dim] {step.description}")
                results = self._run_parallel(layer, plan, past_steps, workspace, run_sub_agent_fn, console)
                for step in layer:
                    plan.mark_done(step.id)
                    r = results.get(step.id, "")
                    past_steps.append((step.description, r))

            # 从剩余步骤中移除已完成的这一层
            done_ids = {s.id for s in layer}
            remaining = [s for s in remaining if s.id not in done_ids]

            if not remaining:
                break

            # Replanner 评估：基于真实结果决定是否调整剩余计划
            console.print("\n[dim]🔄 重新评估剩余计划...[/dim]")
            new_remaining = self.replanner.run(plan.goal, past_steps, remaining)

            if new_remaining is None:
                console.print("[bold green]✅ Replanner 判断目标已达成[/bold green]\n")
                remaining = []
                break

            if new_remaining != remaining:
                console.print(f"[cyan]📝 Replanner 调整了计划，剩余 {len(new_remaining)} 步[/cyan]")
                for s in new_remaining:
                    console.print(f"  [yellow]Step {s.id}[/yellow] {s.description}")
            remaining = new_remaining

        # 交主 agent 整合最终结果
        summary_lines = ["## 计划执行完成", f"**目标：** {plan.goal}", ""]
        for desc, result in past_steps:
            summary_lines.append(f"### {desc}")
            summary_lines.append(result)
            summary_lines.append("")
        summary = "\n".join(summary_lines)

        session.add({
            "role": "user",
            "content": f"以上是任务「{plan.goal}」的执行结果，请根据这些结果给出最终的整合回复。\n\n{summary}",
        })
        return react_loop_fn()

    def _run_serial(self, step, plan, past_steps, workspace, run_sub_agent_fn, console) -> str:
        prior_context = ""
        if past_steps:
            prior_context = "\n\n【前置步骤结果】\n" + "\n---\n".join(
                f"{desc}\n结果：{result}" for desc, result in past_steps
            )

        task = (
            f"【计划目标】{plan.goal}"
            f"{prior_context}\n\n"
            f"【当前任务】{step.description}\n\n"
            f"【要求】只完成当前任务。完成后返回详细的结果摘要，包括：做了什么、发现了什么、产出了哪些文件。"
        )
        result, _ = run_sub_agent_fn(
            task, workspace, f"step-{step.id}",
            inherit_messages=None,
            console=console,
        )
        logger.info(f"Step {step.id} 串行完成")
        return result

    def _run_parallel(self, layer, plan, past_steps, workspace, run_sub_agent_fn, console) -> dict[int, str]:
        results: dict[int, str] = {}
        lock = threading.Lock()
        notify_queue: queue.Queue = queue.Queue()

        # 构建前置步骤上下文（用 past_steps 里的真实结果）
        prior_context = ""
        if past_steps:
            prior_context = "\n\n【前置步骤结果】\n" + "\n---\n".join(
                f"{desc}\n结果：{result}" for desc, result in past_steps
            )

        def _run(step):
            task = (
                f"【计划目标】{plan.goal}\n"
                f"【步骤】{step.description}"
                f"{prior_context}\n\n"
                f"【要求】完成上述步骤。完成后返回详细的结果摘要，包括：做了什么、发现了什么、产出了哪些文件。"
            )
            try:
                result, _ = run_sub_agent_fn(
                    task, workspace, f"step-{step.id}",
                    inherit_messages=None,
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
                console.print(f"[bold green]✅ Step {sid} 完成[/bold green] {short_desc}")
                console.print(Panel(result, title=f"[bold green]Step {sid} 汇报[/bold green]", border_style="green", expand=False))
            else:
                _, sid, desc, err = item
                console.print(f"[bold red]❌ Step {sid} 失败[/bold red]: {err}")

        return results
