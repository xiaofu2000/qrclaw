"""
PlanExecutor 节点

职责：选择执行策略，驱动子 agent 执行每个步骤。

run_step 两种模式：
- 串行：子 session 继承主 session 的全部 messages，执行完把新增 messages 追加回主 session
- 并行：子 session 完全独立，执行完只把最终结果追加一条消息到主 session
"""
from rich.console import Console
from qrclaw.memory.session import Session
from qrclaw.workspace import Workspace
from qrclaw.graph.executor import _topological_layers
from qrclaw.graph.strategies import SerialStrategy, ParallelStrategy, MixedStrategy
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.graph.nodes.plan_executor")


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

        def run_step(step, plan_obj, is_serial: bool) -> str:
            if is_serial:
                # 串行：继承主 session 全部消息，子 agent 能看到完整上下文
                task = (
                    f"【计划目标】{plan_obj.goal}\n"
                    f"【当前步骤】Step {step.id}: {step.description}\n\n"
                    f"【要求】只完成当前步骤。完成后返回详细的结果摘要，包括：做了什么、发现了什么、产出了哪些文件。"
                )
                # 记录当前主 session 长度，用于后续切出子 session 新增的消息
                inherited_count = len(session.messages)
                result, sub_session = run_sub_agent_fn(
                    task, workspace, f"step-{step.id}",
                    inherit_messages=list(session.messages),
                    console=console,
                )
                new_messages = sub_session.messages[inherited_count:]
                for msg in new_messages:
                    session.messages.append(msg)
                session._save()
                logger.info(f"Step {step.id} 串行完成，追加 {len(new_messages)} 条消息回主 session")
            else:
                # 并行：子 session 独立，但注入前置步骤的结果摘要
                prior_context = ""
                if step.depends_on:
                    # 从主 session 尾部找前置步骤的 assistant 摘要消息
                    prior_results = []
                    for msg in reversed(session.messages):
                        if msg.get("role") == "assistant" and msg.get("content"):
                            prior_results.insert(0, msg["content"])
                        if len(prior_results) >= len(step.depends_on):
                            break
                    if prior_results:
                        prior_context = "\n\n【前置步骤结果】\n" + "\n---\n".join(prior_results)

                task = (
                    f"【计划目标】{plan_obj.goal}\n"
                    f"【步骤】{step.description}"
                    f"{prior_context}\n\n"
                    f"【要求】完成上述步骤。完成后返回详细的结果摘要，包括：做了什么、发现了什么、产出了哪些文件。"
                )
                result, _ = run_sub_agent_fn(
                    task, workspace, f"step-{step.id}",
                    inherit_messages=None,
                    console=None,
                )
                logger.info(f"Step {step.id} 并行完成")

            return result

        return strategy.execute(plan, console, run_step, react_loop_fn, session)
