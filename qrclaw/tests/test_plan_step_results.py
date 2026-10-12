from io import StringIO
from unittest.mock import MagicMock

from rich.console import Console

from qrclaw.graph.nodes.plan_executor import PlanExecutorNode
from qrclaw.graph.nodes.router import Plan, PlanStep, RouteResult
from qrclaw.graph.runner import GraphRunner
from qrclaw.memory.context.prompt.builder import build_react_messages, build_router_messages
from qrclaw.memory.context.context_manager import init_context_manager
from qrclaw.memory.context.session import Session
from qrclaw.workspace import Workspace


def test_plan_passes_previous_result_to_next_step_and_replanner(temp_dir):
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    ctx = init_context_manager(session, workspace)
    ctx.set_plan("目标", [PlanStep(1, "第一步"), PlanStep(2, "第二步", [1])])
    tasks = []

    def run_sub_agent(task, workspace, agent_id, console=None, sandbox_policy_agent_id=None, **_kwargs):
        assert sandbox_policy_agent_id == workspace.agent_id
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
    assert "结果1" in session.messages[-1]["content"]
    assert "结果2" in session.messages[-1]["content"]
    assert [event["title"] for event in session.events].count("步骤完成") == 2


def test_graph_runner_keeps_router_wiki_context(temp_dir):
    session = MagicMock(spec=Session)
    workspace = Workspace(_root=temp_dir)
    ctx = init_context_manager(session, workspace)
    plan = Plan("目标", [PlanStep(1, "步骤")])
    runner = GraphRunner()

    def route(_):
        ctx.set_plan(plan.goal, plan.steps)
        ctx.set_wiki_context("已选中的 Wiki 正文")
        return RouteResult("plan", plan)

    runner.router.run = route
    runner.plan_executor.run = lambda **_: ctx.plan_state.wiki_context
    assert runner.run("任务", session, Console(file=StringIO()), workspace) == "已选中的 Wiki 正文"


def test_internal_execution_report_is_context_not_assistant_reply():
    history = [
        {"role": "user", "content": "任务"},
        {"role": "assistant", "kind": "execution_report", "content": "执行汇报：完成"},
    ]
    react = build_react_messages("系统提示", history)
    router = build_router_messages(history, "判断路由")

    assert react[-1] == {"role": "user", "content": "执行汇报：完成"}
    assert router[-2] == {"role": "user", "content": "执行汇报：完成"}
    assert history[-1]["role"] == "assistant"


def test_cyclic_plan_does_not_report_success(temp_dir):
    session = MagicMock(spec=Session)
    workspace = Workspace(_root=temp_dir)
    ctx = init_context_manager(session, workspace)
    ctx.set_plan("目标", [PlanStep(1, "步骤一", [2]), PlanStep(2, "步骤二", [1])])
    executor = PlanExecutorNode()
    react = MagicMock(return_value="不应执行")

    result = executor.run(session, Console(file=StringIO()), workspace, False, MagicMock(), react)

    assert "循环依赖" in result
    react.assert_not_called()
    assert not any(call.args[0].get("kind") == "execution_report" for call in session.add.call_args_list)


def test_replanner_continue_without_steps_does_not_report_success(temp_dir):
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    ctx = init_context_manager(session, workspace)
    ctx.set_plan("目标", [PlanStep(1, "步骤")])
    executor = PlanExecutorNode()
    executor.replanner.run = lambda: []
    react = MagicMock()

    result = executor.run(session, Console(file=StringIO()), workspace, False,
                          lambda *_args, **_kwargs: ("已执行", None), react)

    assert "失败" in result
    react.assert_not_called()
    assert not any(message.get("kind") == "execution_report" for message in session.messages)


def test_parallel_step_error_does_not_report_success(temp_dir):
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    ctx = init_context_manager(session, workspace)
    ctx.set_plan("目标", [PlanStep(1, "步骤一"), PlanStep(2, "步骤二")])
    executor = PlanExecutorNode()
    executor.replanner.run = lambda: None
    react = MagicMock()

    def run_sub_agent(_task, _workspace, agent_id, **_kwargs):
        if agent_id == "step-2":
            raise RuntimeError("子任务故障")
        return "已执行", None

    result = executor.run(session, Console(file=StringIO()), workspace, False, run_sub_agent, react)

    assert "子任务故障" in result
    assert any(event["title"] == "步骤失败" and event["content"] == "步骤二" for event in session.events)
    react.assert_not_called()
    assert not any(message.get("kind") == "execution_report" for message in session.messages)


def test_replanning_has_iteration_limit(temp_dir, monkeypatch):
    from qrclaw.graph.nodes import plan_executor

    monkeypatch.setattr(plan_executor, "MAX_ITERATIONS", 3)
    workspace = Workspace(_root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    ctx = init_context_manager(session, workspace)
    ctx.set_plan("目标", [PlanStep(1, "步骤")])
    executor = PlanExecutorNode()
    executor.replanner.run = lambda: [PlanStep(1, "步骤")]
    react = MagicMock()
    calls = []

    def run_sub_agent(*_args, **_kwargs):
        calls.append(1)
        return "未解决", None

    result = executor.run(session, Console(file=StringIO()), workspace, False, run_sub_agent, react)

    assert len(calls) == 3
    assert "上限" in result
    react.assert_not_called()
