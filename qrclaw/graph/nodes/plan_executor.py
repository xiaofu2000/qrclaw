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

串行和并行步骤均使用独立会话，共用目标、Wiki 正文和前置步骤结果。
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
        execution_context=None,
    ) -> str:
        ctx = get_context_manager()
        ps = ctx.plan_state
        known_steps = {str(step.id): step for step in ps.remaining}
        revision = 1

        while ps.remaining:
            if execution_context is not None:
                execution_context.check_cancelled()
            layer = get_next_layer(ps.remaining)
            if not layer:
                logger.error("剩余步骤存在循环依赖，中止执行")
                console.print("[red]错误：步骤存在循环依赖，中止执行[/red]")
                break

            is_serial = len(layer) == 1

            if is_serial:
                step = layer[0]
                console.print(f"[yellow]→ {step.description} (串行)[/yellow]")
                if execution_context is not None:
                    execution_context.publish(
                        "step.started",
                        {"step_id": str(step.id), "description": step.description},
                    )
                try:
                    result = self._run_step(
                        step,
                        self._build_task(step, ps.goal, ps.past_steps, ps),
                        workspace,
                        run_sub_agent_fn,
                        console,
                        execution_context,
                    )
                except Exception as exc:
                    if execution_context is not None:
                        execution_context.publish(
                            "step.failed",
                            {"step_id": str(step.id), "error": str(exc)},
                        )
                    raise
                ctx.add_step_result(StepResult(
                    step_id=step.id,
                    description=step.description,
                    output=result,
                ))
                if execution_context is not None:
                    execution_context.publish(
                        "step.completed",
                        {"step_id": str(step.id), "output": result},
                    )
            else:
                console.print(f"[yellow]→ 并行执行 {len(layer)} 个步骤[/yellow]")
                for step in layer:
                    console.print(f"  [dim]Step {step.id}:[/dim] {step.description}")
                    if execution_context is not None:
                        execution_context.publish(
                            "step.started",
                            {"step_id": str(step.id), "description": step.description},
                        )
                results = self._run_parallel(
                    layer,
                    ps.goal,
                    ps.past_steps,
                    workspace,
                    run_sub_agent_fn,
                    console,
                    execution_context,
                )
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
                console.print("[cyan]📝 Replanner 调整了计划")
                for step in new_remaining:
                    known_steps[str(step.id)] = step
                revision += 1
                if execution_context is not None:
                    execution_context.publish(
                        "plan.updated",
                        {
                            "plan_id": execution_context.plan_id or execution_context.new_id("plan"),
                            "goal": ps.goal,
                            "project_path": ps.project_path,
                            "steps": [
                                {
                                    "step_id": step_id,
                                    "description": item.description,
                                    "depends_on": [str(value) for value in item.depends_on],
                                    "status": "pending",
                                }
                                for step_id, item in known_steps.items()
                            ],
                            "revision": revision,
                        },
                    )
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
        try:
            return react_loop_fn()
        finally:
            ctx.clear_plan()

    @staticmethod
    def _build_task(step, goal: str, past_steps: list[StepResult], plan_state) -> str:
        """串行、并行共用任务上下文，完整传递 Wiki 和已完成步骤结果。"""
        sections = [f"【计划目标】{goal}"]
        if plan_state:
            if plan_state.project_path:
                sections.append(f"【项目根目录】{plan_state.project_path}")
            if plan_state.wiki_context:
                sections.append(plan_state.wiki_context)
        if past_steps:
            sections.append("【前置步骤结果】\n" + "\n---\n".join(step.to_context_prompt() for step in past_steps))
        sections.extend([
            f"【当前任务】{step.description}",
            "【要求】只完成当前任务。完成后汇报结果摘要，不准写 MD 文档；路径必须使用绝对路径。包括做了什么、发现了什么、产出了哪些文件。",
        ])
        return "\n\n".join(sections)

    def _run_step(
        self,
        step,
        task: str,
        workspace,
        run_sub_agent_fn,
        console,
        execution_context=None,
    ) -> str:
        """执行一个已组装上下文的子任务，供串行与并行调度复用。"""
        child_context = None
        if execution_context is not None:
            child_context = execution_context.child_agent(
                name=f"步骤 {step.id}",
                task=task,
                step_id=str(step.id),
            )
        result, _ = run_sub_agent_fn(
            task, workspace, f"step-{step.id}",
            console=console,
            execution_context=child_context,
        )
        logger.info(f"步骤 {step.id} 执行完成")
        return result

    def _run_parallel(
        self,
        layer,
        goal: str,
        past_steps: list[StepResult],
        workspace,
        run_sub_agent_fn,
        console,
        execution_context=None,
    ) -> dict[int, str]:
        results: dict[int, str] = {}
        lock = threading.Lock()
        notify_queue: queue.Queue = queue.Queue()

        ps = get_context_manager().plan_state
        tasks = {step.id: self._build_task(step, goal, past_steps, ps) for step in layer}

        def _run(step):
            """在独立线程执行已组装好的步骤，不读取父线程局部上下文。"""
            task = tasks[step.id]
            try:
                result = self._run_step(step, task, workspace, run_sub_agent_fn, None, execution_context)
                with lock:
                    results[step.id] = result
                notify_queue.put(("ok", step.id, step.description, result))
                if execution_context is not None:
                    execution_context.publish(
                        "step.completed",
                        {"step_id": str(step.id), "output": result},
                    )
            except Exception as e:
                logger.error(f"Step {step.id} 执行失败: {e}", exc_info=True)
                with lock:
                    results[step.id] = f"执行失败: {e}"
                notify_queue.put(("err", step.id, step.description, str(e)))
                if execution_context is not None:
                    execution_context.publish(
                        "step.failed",
                        {"step_id": str(step.id), "error": str(e)},
                    )

        threads = [
            threading.Thread(target=_run, args=(step,), name=f"plan-step-{step.id}", daemon=True)
            for step in layer
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if execution_context is not None:
            execution_context.check_cancelled()

        # 打印结果
        while not notify_queue.empty():
            item = notify_queue.get()
            if item[0] == "ok":
                _, sid, desc, result = item
                short_desc = desc[:40] + ("..." if len(desc) > 40 else "")
                console.print(f"[bold green]✅ 完成[/bold green] {short_desc}")
                console.print(Panel(Text(result), title="[bold green]汇报[/bold green]", border_style="green", expand=False))
            else:
                _, sid, desc, err = item
                console.print(f"[bold red]❌ 失败[/bold red]: {err} ({desc})")

        return results
