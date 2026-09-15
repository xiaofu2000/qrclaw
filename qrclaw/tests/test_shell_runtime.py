"""Shell 工作目录、沙箱失败和取消行为测试。"""

from __future__ import annotations

import threading
import time

import pytest

from qrclaw.agent import set_execution_context
from qrclaw.execution.context import CancellationToken, ExecutionContext, RunCancelled
from qrclaw.tools import shell as shell_module
from qrclaw.tools.shell import _exec_direct, _get_workspace_root


def _context(tmp_path) -> ExecutionContext:
    return ExecutionContext(
        conversation_id="conv_test",
        run_id="run_test",
        agent_id="agent_test",
        agent_name="测试 Agent",
        task="测试 Shell",
        event_sink=lambda *_: None,
        cancellation=CancellationToken(),
        workspace_path=str(tmp_path),
    )


def test_shell_uses_conversation_workspace(tmp_path):
    """Web 任务必须使用会话配置的项目目录。"""

    context = _context(tmp_path)
    set_execution_context(context)
    try:
        assert _get_workspace_root() == str(tmp_path)
    finally:
        set_execution_context(None)


def test_shell_process_is_terminated_after_cancel(tmp_path):
    """取消运行时应终止正在执行的 Shell 进程组。"""

    context = _context(tmp_path)
    set_execution_context(context)
    timer = threading.Timer(0.2, context.cancellation.cancel)
    timer.start()
    started_at = time.monotonic()
    try:
        with pytest.raises(RunCancelled):
            _exec_direct(
                "python -c 'import time; time.sleep(10)'",
                str(tmp_path),
                timeout=30,
            )
    finally:
        timer.cancel()
        set_execution_context(None)

    assert time.monotonic() - started_at < 3


def test_sandbox_failure_never_falls_back_to_host(tmp_path, monkeypatch):
    """沙箱启动或执行失败时必须直接失败，不能改在主机执行。"""

    host_executions = []
    context = _context(tmp_path)

    def fail_in_sandbox(*_):
        """模拟沙箱创建失败。"""

        raise RuntimeError("沙箱创建失败")

    monkeypatch.setattr(shell_module, "_is_sandbox_enabled", lambda: True)
    monkeypatch.setattr(shell_module, "_exec_in_sandbox", fail_in_sandbox)
    monkeypatch.setattr(
        shell_module,
        "_exec_direct",
        lambda *_: host_executions.append(True) or "不应执行",
    )

    set_execution_context(context)
    try:
        with pytest.raises(RuntimeError, match="沙箱创建失败"):
            shell_module.run_shell("touch unsafe.txt")
    finally:
        set_execution_context(None)

    assert host_executions == []
