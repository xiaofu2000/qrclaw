from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from qrclaw.graph.nodes.plan_executor import PlanExecutorNode
from qrclaw.graph.nodes.router import PlanStep
from qrclaw.memory.context.context_manager import init_context_manager
from qrclaw.memory.context.session import Session
from qrclaw.workspace import Workspace


def test_plan_passes_previous_result_to_next_step_and_replanner(temp_dir):
    session = MagicMock(spec=Session)
    workspace = Workspace(_root=temp_dir)
    ctx = init_context_manager(session, workspace)
    ctx.set_plan("目标", [PlanStep(1, "第一步"), PlanStep(2, "第二步", [1])])
    tasks = []

    def run_sub_agent(task, workspace, agent_id, console=None):
        tasks.append(task)
        return f"结果{len(tasks)}", None

    def replan():
        prompt = ctx.build_messages("replanner")[0]["content"]
        assert f"结果{len(tasks)}" in prompt
        return ctx.plan_state.remaining or None

    executor = PlanExecutorNode()
    executor.replanner.run = replan
    result = executor.run(session, Console(file=StringIO()), workspace, False, run_sub_agent, lambda: "完成")

    assert result == "完成"
    assert len(tasks) == 2
    assert "结果1" in tasks[1]
    assert "结果1" in session.add.call_args.args[0]["content"]
    assert "结果2" in session.add.call_args.args[0]["content"]
