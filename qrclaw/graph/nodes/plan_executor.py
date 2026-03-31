"""
PlanExecutor 节点

职责：接收计划，选择执行策略（串行/并行/混合），驱动子 agent 执行每个步骤。
执行完成后根据策略决定是直接返回还是交回 ReactLoop 做最终整合。
"""
import json
import threading
from rich.console import Console
from qrclaw.memory.session import Session
from qrclaw.memory.step_result import StepResult
from qrclaw.workspace import Workspace
from qrclaw.graph.executor import _topological_layers
from qrclaw.graph.strategies import SerialStrategy, ParallelStrategy, MixedStrategy
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.plan_executor")


def _extract_working_memory(sub_session: Session, step_id: int, output: str):
    """从子 session 消息历史提取工具调用，填充 working_memory"""
    wm = sub_session.working_memory
    for msg in sub_session.messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls", []):
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
            except Exception:
                continue
            path = args.get("path", "")
            if not path:
                continue
            if name == "read_file":
                wm.add_relevant_file(path)
            elif name == "write_file":
                if path in wm.relevant_files:
                    wm.add_modified_file(path)
                else:
                    wm.add_created_file(path)
    if output:
        wm.add_finding(f"Step {step_id}: {output[:200]}")


class PlanExecutorNode:

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
        # 打印计划概览
        console.print(f"\n[bold cyan]📋 执行计划：{plan.goal}[/bold cyan]")
        for step in plan.steps:
            dep = f"  [dim]依赖 Step {step.depends_on}[/dim]" if step.depends_on else "  [dim]可并行[/dim]"
            console.print(f"  [yellow]Step {step.id}[/yellow] {step.description}{dep}")
        console.print()

        # 选择执行策略
        layers = _topological_layers(plan.steps)
        has_parallel = any(len(layer) > 1 for layer in layers)
        all_parallel = all(len(layer) > 1 for layer in layers)

        if not has_parallel:
            strategy = SerialStrategy()
        elif all_parallel:
            strategy = ParallelStrategy()
        else:
            strategy = MixedStrategy()

        logger.info(f"选择执行策略: {strategy.__class__.__name__}")

        # 线程安全的 working_memory merge 队列
        _pending_merge_wms = []
        _merge_lock = threading.Lock()

        def run_step(step, plan_obj) -> str:
            is_serial = getattr(step, "_is_serial", False)

            # 构建前置上下文
            context = ""
            if is_serial:
                prior = [r.to_context_prompt() for _, r in sorted(session.step_results.items())]
                if prior:
                    context = "\n\n【已完成步骤结果】\n" + "\n---\n".join(prior)
            elif step.depends_on:
                prior = [
                    session.step_results[dep_id].to_context_prompt()
                    for dep_id in step.depends_on
                    if dep_id in session.step_results
                ]
                if prior:
                    context = "\n\n【前置步骤结果】\n" + "\n---\n".join(prior)

            if is_serial:
                task = (
                    f"【计划目标】{plan_obj.goal}\n"
                    f"【当前步骤】Step {step.id}: {step.description}"
                    f"{context}\n\n"
                    f"【要求】只完成当前步骤，完成后返回结果摘要。"
                )
            else:
                task = f"{step.description}{context}\n\n【要求】完成后返回结果摘要。"

            inherit_wm = session.working_memory if is_serial else None
            step_console = console if is_serial else None

            result, sub_session = run_sub_agent_fn(
                task, workspace, f"step-{step.id}",
                inherit_working_memory=inherit_wm,
                console=step_console,
            )

            _extract_working_memory(sub_session, step.id, result)

            session.step_results[step.id] = StepResult(
                step_id=step.id,
                description=step.description,
                status="success",
                output=result,
                summary=result[:300] + "..." if len(result) > 300 else result,
                messages=sub_session.messages,
            )

            if is_serial:
                logger.info(f"Step {step.id} 串行执行完毕")
            else:
                with _merge_lock:
                    _pending_merge_wms.append(sub_session.working_memory)
                logger.info(f"Step {step.id} 并行执行完毕，working_memory 加入 merge 队列")

            return result

        result = strategy.execute(plan, session, console, workspace, auto_confirm, run_step, react_loop_fn)

        # 并行步骤结束后 merge working_memory
        if _pending_merge_wms:
            for wm in _pending_merge_wms:
                session.working_memory.merge(wm)
            logger.info(f"合并 {len(_pending_merge_wms)} 个并行步骤的 working_memory")

        return result
