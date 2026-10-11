"""文件和沙箱的信任边界。"""

from unittest.mock import patch
from threading import Thread

import pytest

from qrclaw.agent import get_extractor, get_session, get_workspace, register_extractor, run_sub_agent, set_session, set_workspace
from qrclaw.memory.context.session import Session
from qrclaw.sandbox import ContainerError
from qrclaw.tools import filesystem, shell
from qrclaw.tools.agent_tools import delete_agent
from qrclaw.workspace import Workspace


def test_agent_id_cannot_escape_workspace(temp_dir):
    with pytest.raises(ValueError, match="无效的 Agent ID"):
        Workspace("../outside", _root=temp_dir)
    assert delete_agent("../outside").startswith("错误：Agent 名称不合法")


def test_wiki_extractor_is_thread_local():
    first, second = object(), object()
    register_extractor(first)
    seen = []

    def worker():
        register_extractor(second)
        seen.append(get_extractor())

    thread = Thread(target=worker)
    thread.start()
    thread.join()
    assert seen == [second]
    assert get_extractor() is first


def test_sub_agent_uses_its_own_sandbox_identity(temp_dir):
    workspace = Workspace("default", _root=temp_dir)
    session = Session(workspace.sessions_dir, resume=False)
    previous_session, previous_workspace = get_session(), get_workspace()
    set_session(session)
    set_workspace(workspace)

    def run_child(_task, child_session, _console, child_workspace, **_kwargs):
        set_session(child_session)
        set_workspace(child_workspace)
        return "ok"

    try:
        with patch("qrclaw.agent.run", side_effect=run_child) as run:
            result, _ = run_sub_agent("test", workspace, "worker")
        assert get_session() is session
        assert get_workspace() is workspace
    finally:
        set_session(previous_session)
        set_workspace(previous_workspace)
    assert result == "ok"
    assert run.call_args.args[3].agent_id == "worker"
    assert run.call_args.args[3].root == workspace.root


def test_file_read_limit_uses_config(monkeypatch):
    monkeypatch.setattr(filesystem, "_max_read_chars_cached", None)
    with patch("qrclaw.config_manager.get_config", return_value={"file_read_max_chars": 123}):
        assert filesystem._get_max_read_chars() == 123


def test_sandbox_failure_does_not_run_command_on_host(temp_dir):
    with patch("qrclaw.sandbox.sandbox_manager.has_sandbox", return_value=False), \
         patch("qrclaw.sandbox.sandbox_manager.create_sandbox", side_effect=ContainerError("Docker 不可用")), \
         patch("qrclaw.tools.shell._exec_direct") as direct:
        result = shell._exec_in_sandbox("touch marker", str(temp_dir))
    assert "命令未执行" in result
    direct.assert_not_called()
    assert not (temp_dir / "marker").exists()


def test_sandbox_file_access_stays_inside_workspace(temp_dir):
    root = temp_dir / "workspace"
    workspace = Workspace("worker", _root=root)
    outside = temp_dir / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)

    with patch("qrclaw.agent.get_workspace", return_value=workspace), \
         patch("qrclaw.sandbox.is_sandbox_enabled", return_value=True):
        assert filesystem._resolve_path("/workspace/note.txt") == (root / "note.txt").resolve()
        for path in ("../outside.txt", "/etc/passwd", "/workspace/link.txt"):
            with pytest.raises(PermissionError):
                filesystem._resolve_path(path)


def test_patch_checks_move_destination_before_other_writes(temp_dir, monkeypatch):
    safe = temp_dir / "safe"
    sensitive = temp_dir / "sensitive"
    safe.mkdir()
    sensitive.mkdir()
    source = safe / "source.txt"
    source.write_text("original", encoding="utf-8")
    monkeypatch.setattr(filesystem, "_SENSITIVE_PATH_PREFIXES", (f"{sensitive}/",))

    result = filesystem.patch(mode="patch", patch_content=(
        "*** Begin Patch\n"
        f"*** Add File: {safe / 'added.txt'}\n+"
        "+new\n"
        f"*** Move File: {source} -> {sensitive / 'moved.txt'}\n"
        "*** End Patch"
    ))

    assert "拒绝写入敏感系统路径" in result
    assert source.read_text(encoding="utf-8") == "original"
    assert not (safe / "added.txt").exists()
    assert not (sensitive / "moved.txt").exists()
