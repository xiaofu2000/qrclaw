"""浏览器端到端测试使用的隔离后端服务。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import uvicorn

from qrclaw.execution.service import RunService
from qrclaw.server.app import create_app
from qrclaw.workspace import Workspace


def browser_test_runner(**kwargs) -> str:
    """根据测试输入发布 Direct 或 Plan 模式事件。"""

    content = kwargs["user_input"]
    context = kwargs["execution_context"]
    if "计划" not in content:
        context.publish("route.decided", {"route": "direct"})
        context.publish(
            "agent.progress",
            {
                "current_action": "正在回答简单问题",
                "decision_summary": "任务无需拆分步骤，直接由主 Agent 完成",
            },
        )
        return "Direct 简单 Agent 测试完成"

    context.publish("route.decided", {"route": "plan"})
    context.publish(
        "plan.created",
        {
            "plan_id": "plan_browser_e2e",
            "goal": "验证计划路由和子 Agent 展示",
            "project_path": context.workspace_path,
            "revision": 1,
            "steps": [
                {
                    "step_id": "step_analyze",
                    "description": "分析测试目标",
                    "depends_on": [],
                },
                {
                    "step_id": "step_verify",
                    "description": "验证页面结果",
                    "depends_on": ["step_analyze"],
                },
            ],
        },
    )
    context.publish("step.started", {"step_id": "step_analyze"})
    child = context.child_agent("浏览器子 Agent", "检查计划步骤", "step_analyze")
    child.publish(
        "agent.started",
        {"name": child.agent_name, "task": child.task, "step_id": child.step_id},
    )
    child.publish(
        "agent.progress",
        {
            "current_action": "正在核对计划路由",
            "decision_summary": "先确认计划步骤，再验证子 Agent 归属关系",
        },
    )
    child.publish("agent.completed", {"result": "计划路由检查完成"})
    context.publish(
        "step.completed",
        {"step_id": "step_analyze", "output": "分析测试目标完成"},
    )
    context.publish("step.started", {"step_id": "step_verify"})
    context.publish(
        "step.completed",
        {"step_id": "step_verify", "output": "页面结果验证完成"},
    )
    return "Plan 路由与子 Agent 测试完成"


def main() -> None:
    """使用临时数据库启动只服务于浏览器测试的后端。"""

    access_token = os.environ.get("QRCLAW_E2E_ACCESS_TOKEN", "browser-e2e-token")
    with tempfile.TemporaryDirectory(prefix="qrclaw-browser-e2e-") as directory:
        root = Path(directory)
        service = RunService(
            workspace=Workspace(agent_id="browser-e2e", _root=root / "agent"),
            database_path=root / "runtime.sqlite3",
            agent_runner=browser_test_runner,
            access_token=access_token,
        )
        uvicorn.run(create_app(service), host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
