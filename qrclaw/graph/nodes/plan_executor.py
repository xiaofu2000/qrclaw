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
                    f"【要求】只完成当前步骤，完成后返回结果摘要。"
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
                # 并行：完全独立，只把最终结果追加一条消息
                task = (
                    f"【计划目标】{plan_obj.goal}\n"
                    f"【步骤】{step.description}\n\n"
                    f"【要求】完成后返回结果摘要。"
                )
                result, _ = run_sub_agent_fn(
                    task, workspace, f"step-{step.id}",
                    inherit_messages=None,
                    console=None,
                )
                logger.info(f"Step {step.id} 并行完成")

            return result

        return strategy.execute(plan, console, run_step, react_loop_fn, session)
