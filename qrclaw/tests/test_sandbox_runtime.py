"""沙箱接口、缓存和子任务生命周期的回归测试。"""

from unittest.mock import create_autospec, patch
from pathlib import Path

import pytest

from qrclaw.agent import run_sub_agent
from qrclaw.sandbox import MountConfig, SandboxConfig, sandbox_manager
from qrclaw.sandbox.container import ContainerConfig, ContainerError, ContainerManager, ExecResult
from qrclaw.sandbox.manager import SandboxHandle
from qrclaw.tools import shell
from qrclaw.workspace import Workspace


def test_sandbox_manager_calls_real_container_signature(temp_dir):
    container = create_autospec(ContainerManager, instance=True)
    container.create.return_value = "test-container"
    config = SandboxConfig(network="none", memory="256m", pids_limit=32)
    with patch.object(sandbox_manager, "_containers", {}), \
         patch.object(sandbox_manager, "_get_container_mgr", return_value=container):
        handle = sandbox_manager.create_sandbox("worker", workspace=temp_dir, config=config)

    passed = container.create.call_args.kwargs["config"]
    assert (passed.network, passed.memory, passed.pids_limit) == ("none", "256m", 32)
    assert passed.cap_drop == ["ALL"]
    assert handle.container_id == "test-container"


def test_container_mount_uses_expanded_host_path(temp_dir):
    with patch.object(ContainerManager, "_check_docker"), \
         patch("qrclaw.sandbox.container.Path.exists", return_value=True), \
         patch("qrclaw.sandbox.container.subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = "test-container"
        ContainerManager().create("test", temp_dir, [MountConfig(host="~/qrclaw-test-mount", container="/extra")], ContainerConfig())

    assert f"{Path('~/qrclaw-test-mount').expanduser().resolve()}:/extra:ro" in run.call_args.args[0]
    assert not any("~/" in arg for arg in run.call_args.args[0])
    assert "--init" in run.call_args.args[0]


def test_failed_start_removes_created_container(temp_dir):
    container = create_autospec(ContainerManager, instance=True)
    container.create.return_value = "failed-container"
    container.start.side_effect = ContainerError("start failed")
    with patch.object(sandbox_manager, "_containers", {}), \
         patch.object(sandbox_manager, "_get_container_mgr", return_value=container):
        with pytest.raises(ContainerError, match="start failed"):
            sandbox_manager.create_sandbox("worker", workspace=temp_dir, config=SandboxConfig())
        assert not sandbox_manager.has_sandbox("worker")
    container.remove.assert_called_once_with("failed-container", force=True)


def test_failed_removal_keeps_handle_and_blocks_replacement(temp_dir):
    handle = SandboxHandle("worker", "existing", "worker", temp_dir, [], SandboxConfig())
    container = create_autospec(ContainerManager, instance=True)
    container.remove.return_value = False
    container.exists.return_value = True
    with patch.object(sandbox_manager, "_containers", {"worker": handle}), \
         patch.object(sandbox_manager, "_get_container_mgr", return_value=container):
        assert not sandbox_manager.destroy_sandbox("worker")
        assert sandbox_manager.get_sandbox("worker") is handle
        with pytest.raises(ContainerError, match="销毁"):
            sandbox_manager.create_sandbox("worker", workspace=temp_dir, config=SandboxConfig())
    container.create.assert_not_called()


def test_unreachable_docker_does_not_mean_container_is_absent():
    with patch.object(ContainerManager, "_check_docker"), \
         patch("qrclaw.sandbox.container.subprocess.run") as run:
        run.return_value.returncode = 1
        with pytest.raises(ContainerError, match="无法确认"):
            ContainerManager().exists("existing-container")


def test_shell_replaces_sandbox_with_wrong_workspace_or_policy(temp_dir):
    old_config = SandboxConfig(network="bridge")
    config = SandboxConfig(network="none")
    workspace = Workspace("step-1", _root=temp_dir / "new", _sandbox_policy_agent_id="restricted")
    old_handle = SandboxHandle("step-1", "old-container", "old", temp_dir / "old", [], old_config)
    container = create_autospec(ContainerManager, instance=True)
    container.create.return_value = "new-container"
    container.is_running.return_value = True
    container.exec.return_value = ExecResult(0, "new workspace", "", 0)
    with patch.object(sandbox_manager, "_containers", {"step-1": old_handle}), \
         patch.object(sandbox_manager, "_get_container_mgr", return_value=container), \
         patch("qrclaw.agent.get_workspace", return_value=workspace), \
         patch("qrclaw.sandbox.get_sandbox_config", return_value=config):
        assert shell._exec_in_sandbox("pwd", str(workspace.root)) == "new workspace"

    assert container.create.call_args.kwargs["workspace"] == workspace.root.resolve()
    assert container.exec.call_args.kwargs["container_id"] == "new-container"
    container.remove.assert_called_once_with("old-container", force=False)


@pytest.mark.parametrize("fails", [False, True])
def test_sub_agent_runtime_is_unique_and_always_cleans_sandbox(temp_dir, fails):
    workspaces = [Workspace("parent", _root=temp_dir / str(i)) for i in range(2)]
    config = SandboxConfig(network="none")
    with patch("qrclaw.sandbox.is_sandbox_enabled", return_value=True), \
         patch("qrclaw.sandbox.get_sandbox_config", return_value=config), \
         patch("qrclaw.sandbox.create_sandbox") as create, \
         patch.object(sandbox_manager, "has_sandbox", return_value=True), \
         patch("qrclaw.sandbox.destroy_sandbox") as destroy, \
         patch("qrclaw.agent.run", side_effect=RuntimeError("child failed") if fails else None, return_value="ok") as run:
        for workspace in workspaces:
            if fails:
                with pytest.raises(RuntimeError, match="child failed"):
                    run_sub_agent("test", workspace, "step-1", sandbox_policy_agent_id="parent")
            else:
                run_sub_agent("test", workspace, "step-1", sandbox_policy_agent_id="parent")

    runtime_ids = [call.args[3].agent_id for call in run.call_args_list]
    assert len(set(runtime_ids)) == 2
    assert all(runtime_id.startswith("step-1-") for runtime_id in runtime_ids)
    assert [call.kwargs["agent_id"] for call in create.call_args_list] == runtime_ids
    assert [call.args[0] for call in destroy.call_args_list] == runtime_ids
    assert all(call.args[3].sandbox_policy_agent_id == "parent" for call in run.call_args_list)
