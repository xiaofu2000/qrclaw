"""GraphRunner 与结构化运行上下文的集成测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

from qrclaw.execution.context import CancellationToken, ExecutionContext
from qrclaw.graph.nodes.router import RouteResult
from qrclaw.graph.runner import GraphRunner


def test_direct_route_publishes_route_event():
    """Direct 路由必须向运行服务报告，而不是依赖日志解析。"""

    events = []
    context = ExecutionContext(
        conversation_id="conv_test",
        run_id="run_test",
        agent_id="agent_test",
        agent_name="主 Agent",
        task="直接任务",
        event_sink=lambda event_type, data, _: events.append((event_type, data)),
        cancellation=CancellationToken(),
    )
    runner = GraphRunner()
    runner.router.run = MagicMock(return_value=RouteResult(route="direct"))
    runner.react_loop.run = MagicMock(return_value="完成")

    result = runner.run(
        user_input="直接任务",
        session=MagicMock(),
        console=MagicMock(),
        workspace=MagicMock(),
        execution_context=context,
    )

    assert result == "完成"
    assert ("route.decided", {"route": "direct"}) in events
    runner.react_loop.run.assert_called_once()
    assert runner.react_loop.run.call_args.kwargs["execution_context"] is context
