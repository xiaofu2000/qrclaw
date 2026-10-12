"""设置 QRCLAW_DOCKER_TEST_ROOT 后运行真实 Docker 沙箱回归。"""

import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import pytest

from qrclaw.agent import run_sub_agent, set_workspace
from qrclaw.sandbox import SandboxConfig, sandbox_manager
from qrclaw.sandbox.config import AgentConfig, config_manager
from qrclaw.tools.shell import run_shell
from qrclaw.workspace import Workspace

pytestmark = pytest.mark.skipif(not os.getenv("QRCLAW_DOCKER_TEST_ROOT"), reason="需要显式指定 Docker 可挂载的测试目录")


def inspect(container_id):
    return json.loads(subprocess.check_output(["docker", "inspect", container_id], text=True))[0]


def test_real_sandbox_replaces_workspace_and_policy():
    runtime_id = f"docker-test-{uuid4().hex[:12]}"
    with TemporaryDirectory(dir=os.environ["QRCLAW_DOCKER_TEST_ROOT"]) as root, \
         patch.object(sandbox_manager, "_containers", {}):
        first = Workspace(runtime_id, _root=Path(root) / "first")
        second = Workspace(runtime_id, _root=Path(root) / "second")
        (first.root / "marker").write_text("first\n")
        (second.root / "marker").write_text("second\n")
        sandbox_manager.create_sandbox(runtime_id, first.root, config=SandboxConfig(network="bridge"))
        old_id = sandbox_manager.get_sandbox(runtime_id).container_id
        set_workspace(second)
        try:
            assert run_shell("cat marker; pwd").splitlines() == ["second", "/workspace"]
            current = sandbox_manager.get_sandbox(runtime_id)
            assert current.container_id != old_id
            assert not sandbox_manager._get_container_mgr().exists(old_id)
            details = inspect(current.container_id)
            assert details["HostConfig"]["NetworkMode"] == "none"
            assert details["HostConfig"]["ReadonlyRootfs"] is True
            assert details["HostConfig"]["CapDrop"] == ["ALL"]
            assert "no-new-privileges" in details["HostConfig"]["SecurityOpt"]
            assert "Read-only file system" in run_shell("echo denied > /etc/qrclaw-test")
        finally:
            set_workspace(None)
            assert sandbox_manager.destroy_sandbox(runtime_id, force=True)


def test_real_subtasks_use_unique_containers_and_clean_failures(monkeypatch):
    monkeypatch.setitem(config_manager._config.agents, "docker-test-policy", AgentConfig(sandbox=SandboxConfig()))
    containers = []
    runtime_ids = []

    def execute(_task, _session, _console, workspace, **_kwargs):
        set_workspace(workspace)
        handle = sandbox_manager.get_sandbox(workspace.agent_id)
        containers.append(handle.container_id)
        runtime_ids.append(workspace.agent_id)
        assert inspect(handle.container_id)["HostConfig"]["NetworkMode"] == "none"
        output = run_shell("cat marker; pwd").splitlines()
        assert output[1] == "/workspace"
        if output[0] == "second":
            raise RuntimeError("child failed")
        return output[0]

    with TemporaryDirectory(dir=os.environ["QRCLAW_DOCKER_TEST_ROOT"]) as root, \
         patch("qrclaw.agent.run", side_effect=execute), \
         patch.object(sandbox_manager, "_containers", {}):
        for marker in ("first", "second"):
            parent = Workspace("docker-test-policy", _root=Path(root) / marker)
            (parent.root / "marker").write_text(marker + "\n")
            if marker == "first":
                result, _session = run_sub_agent("test", parent, "step-1", sandbox_policy_agent_id="docker-test-policy")
                assert result == "first"
            else:
                with pytest.raises(RuntimeError, match="child failed"):
                    run_sub_agent("test", parent, "step-1", sandbox_policy_agent_id="docker-test-policy")
        assert len(set(runtime_ids)) == 2
        assert not sandbox_manager.list_sandboxes()
        assert all(not sandbox_manager._get_container_mgr().exists(container) for container in containers)
