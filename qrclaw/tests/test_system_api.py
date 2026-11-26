"""测试 /v1/system 系统配置热更新、密钥脱敏、MCP 状态重连、Skills 与多工作空间隔离。"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import qrclaw.config as config_module
from qrclaw import config_manager
from qrclaw.agent import _cleanup_mcp
from qrclaw.config import reload_settings
from qrclaw.memory.context.prompt.cache import SystemPromptCache
from qrclaw.memory.wiki import WikiMemory
from qrclaw.providers import provider
from qrclaw.server.app import create_app
import qrclaw.workspace as workspace_module


@pytest.fixture
def isolated_system_env(temp_dir, monkeypatch):
    """隔离 config.yaml、agents 目录与权限文件，并在测试结束后还原全局配置与 MCP 连接。"""
    from qrclaw.sandbox import config as sandbox_config
    from qrclaw.tools import agent_tools

    cfg_file = temp_dir / "config.yaml"
    agents_dir = temp_dir / "agents"
    perms_file = temp_dir / "permissions.yaml"

    monkeypatch.setattr(config_manager, "CONFIG_DIR", temp_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(workspace_module, "AGENTS_ROOT", agents_dir)
    monkeypatch.setattr(agent_tools, "AGENTS_ROOT", agents_dir)
    monkeypatch.setattr(agent_tools, "PERMISSIONS_FILE", perms_file)
    monkeypatch.setattr(sandbox_config, "PERMISSIONS_FILE", perms_file)

    for env_key in list(config_manager.ENV_OVERRIDES.keys()):
        monkeypatch.delenv(env_key, raising=False)

    config_manager.init_config()
    reload_settings()
    try:
        yield temp_dir
    finally:
        _cleanup_mcp()
        reload_settings()


def test_config_get_redacts_secrets_and_reports_env_overrides(isolated_system_env, monkeypatch):
    """读取配置绝不泄露完整 API Key 或 Tavily Key，且准确报告环境变量覆盖项。"""
    config_manager.set_config("llm.api_key", "sk-top-secret-key-9876")
    config_manager.set_config("search.tavily_api_key", "tvly-private-token-4321")
    monkeypatch.setenv("LITELLM_MODEL", "anthropic/claude-3-7-sonnet")
    reload_settings()

    with TestClient(create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        res = client.get("/v1/system/config")
        assert res.status_code == 200
        body = res.json()
        raw_text = json.dumps(body, ensure_ascii=False)

        assert "sk-top-secret-key-9876" not in raw_text
        assert "tvly-private-token-4321" not in raw_text
        assert "api_key" not in body["llm"]
        assert "tavily_api_key" not in body["search"]
        assert body["has_api_key"] is True
        assert body["api_key_masked"] == "****9876"
        assert body["llm"]["api_key_masked"] == "****9876"
        assert body["search"]["has_tavily_api_key"] is True
        assert body["search"]["tavily_api_key_masked"] == "****4321"
        assert "llm.model" in body["env_overrides_active"]
        assert body["llm"]["model"] == "anthropic/claude-3-7-sonnet"

        # 短密钥同样不暴露任何明文字符
        config_manager.set_config("llm.api_key", "abcd")
        reload_settings()
        short_body = client.get("/v1/system/config").json()
        assert short_body["api_key_masked"] == "****"
        assert "abcd" not in json.dumps(short_body)


def test_config_patch_preserves_secret_hot_reloads_provider_and_rejects_invalid(isolated_system_env):
    """部分更新保留未传/留空密钥，热更新立即刷新 settings、Provider 与提示缓存，非法值返回 400。"""
    config_manager.set_config("llm.api_key", "sk-keep-me-5566")
    reload_settings()

    prompt_cache = SystemPromptCache()
    prompt_cache.set("cached-old-prompt")
    assert prompt_cache.get() == "cached-old-prompt"

    with TestClient(create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        # 1. 不传 api_key，修改模型、base_url、窗口大小和压缩比例
        res = client.patch("/v1/system/config", json={
            "llm": {
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com/v1",
                "max_tokens": 128000,
            },
            "agent": {"plan_enabled": True, "max_concurrent_runs": 6},
            "compress": {"threshold_ratio": 0.8},
        })
        assert res.status_code == 200
        data = res.json()
        assert data["api_key_masked"] == "****5566"
        assert config_module.settings.llm.api_key == "sk-keep-me-5566"
        assert config_module.settings.llm.model == "deepseek-chat"
        assert config_module.LITELLM_MODEL == "deepseek-chat"
        assert config_module._MODEL_MAX_TOKENS == 128000
        assert config_module.COMPRESS_THRESHOLD == int(128000 * 0.8)
        assert config_module.PLAN_ENABLED is True
        assert provider._model == "deepseek/deepseek-chat"
        assert provider._base_url == "https://api.deepseek.com/v1"
        assert provider._api_key == "sk-keep-me-5566"
        assert prompt_cache.get() is None  # 系统提示缓存已失效

        # 2. 传空串 api_key 但未设置 clear_api_key 时，依然保留旧密钥
        res2 = client.patch("/v1/system/config", json={"llm": {"api_key": "", "model": "openai/gpt-4.1"}})
        assert res2.status_code == 200
        assert config_module.settings.llm.api_key == "sk-keep-me-5566"
        assert provider._model == "openai/gpt-4.1"

        # 3. 显式 clear_api_key=True 才允许清空密钥
        res3 = client.patch("/v1/system/config", json={"clear_api_key": True, "llm": {"api_key": ""}})
        assert res3.status_code == 200
        assert res3.json()["has_api_key"] is False
        assert config_module.settings.llm.api_key == ""
        assert provider._api_key == ""

        # 4. 非法参数返回 400 中文错误，且不破坏已有配置
        for invalid_payload in (
            {"compress": {"threshold_ratio": 1.5}},
            {"llm": {"max_tokens": 0}},
            {"llm": {"model": "   "}},
            {"unknown_section": {"foo": "bar"}},
            {"mcp": {"servers": [{"label": "dup", "command": "x"}, {"label": "dup", "command": "y"}]}},
        ):
            bad = client.patch("/v1/system/config", json=invalid_payload)
            assert bad.status_code == 400
            assert isinstance(bad.json().get("detail"), str) and bad.json()["detail"]
        assert config_module.settings.llm.model == "openai/gpt-4.1"


def test_workspaces_lifecycle_and_strict_session_wiki_isolation(isolated_system_env):
    """新建、切换、请求头跨空间路由、会话与 Wiki 隔离，以及保护激活/默认/运行中工作空间不被误删。"""
    with TestClient(create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        # 初始只有 default
        initial = client.get("/v1/system/workspaces").json()
        assert initial["active_workspace"] == "default"
        assert [w["agent_id"] for w in initial["workspaces"]] == ["default"]

        # 在 default 创建会话与 Wiki 页面
        default_sess = client.post("/v1/sessions", json={"session_id": "default-chat"}).json()
        assert default_sess["id"] == "default-chat"
        default_ws_dir = workspace_module.AGENTS_ROOT / "default" / "memory"
        WikiMemory.for_workspace(default_ws_dir).save_page("默认项目笔记", content="这是 default 空间的专属记忆", tags=["default"])

        # 校验非法工作空间名拦截
        assert client.post("/v1/system/workspaces", json={"agent_id": "../escape"}).status_code == 400
        assert client.get("/v1/sessions", headers={"X-QRClaw-Workspace": "../escape"}).status_code == 400
        assert client.get("/v1/sessions", headers={"X-QRClaw-Workspace": "ghost-ws"}).status_code == 404

        # 新建工作空间 proj-a 并立即切换，同时新建闲置空间 proj-b
        created_a = client.post("/v1/system/workspaces", json={"agent_id": "proj-a", "switch": True})
        assert created_a.status_code == 200
        assert created_a.json()["active_workspace"] == "proj-a"
        assert client.post("/v1/system/workspaces", json={"agent_id": "proj-a"}).status_code == 409

        created_b = client.post("/v1/system/workspaces", json={"agent_id": "proj-b", "switch": False})
        assert created_b.status_code == 200
        assert created_b.json()["active_workspace"] == "proj-a"

        # 切换到 proj-a 后，默认不带头访问的是 proj-a：会话与 Wiki 完全为空，看不到 default 的数据
        assert client.get("/v1/sessions").json() == []
        assert client.get("/v1/wiki/pages").json()["total"] == 0

        # 在 proj-a 创建专属会话与记忆
        client.post("/v1/sessions", json={"session_id": "proj-a-chat"})
        proja_ws_dir = workspace_module.AGENTS_ROOT / "proj-a" / "memory"
        WikiMemory.for_workspace(proja_ws_dir).save_page("项目A架构", content="项目 A 的独立设计", tags=["proj-a"])

        assert [s["id"] for s in client.get("/v1/sessions").json()] == ["proj-a-chat"]
        assert [p["name"] for p in client.get("/v1/wiki/pages").json()["items"]] == ["项目A架构"]

        # 通过 X-QRClaw-Workspace 请求头仍可随时访问 default 的独立数据
        assert [s["id"] for s in client.get("/v1/sessions", headers={"X-QRClaw-Workspace": "default"}).json()] == ["default-chat"]
        assert [p["name"] for p in client.get("/v1/wiki/pages", headers={"X-QRClaw-Workspace": "default"}).json()["items"]] == ["默认项目笔记"]

        # 检查工作空间统计计数准确
        listed = {w["agent_id"]: w for w in client.get("/v1/system/workspaces").json()["workspaces"]}
        assert listed["default"]["session_count"] == 1
        assert listed["default"]["wiki_page_count"] == 1
        assert listed["proj-a"]["is_active"] is True
        assert listed["proj-a"]["session_count"] == 1
        assert listed["proj-a"]["wiki_page_count"] == 1
        assert listed["proj-b"]["is_active"] is False

        # 禁止删除 default 与当前激活的 proj-a
        assert client.delete("/v1/system/workspaces/default").status_code == 400
        assert client.delete("/v1/system/workspaces/proj-a").status_code == 409

        # 当 proj-a 有后台任务正在运行时，即便切回 default 也禁止删除运行中的 proj-a
        started, release = Event(), Event()

        def slow_run(**_kwargs):
            started.set()
            release.wait(3)
            return "完成"

        with patch("qrclaw.server.routes.run_agent", side_effect=slow_run), ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(
                client.post,
                "/v1/chat/completions",
                headers={"X-QRClaw-Workspace": "proj-a"},
                json={"session_id": "proj-a-chat", "messages": [{"role": "user", "content": "后台执行"}]},
            )
            try:
                assert started.wait(2)
                # 切回 default 不影响 proj-a 后台运行
                sw = client.post("/v1/system/workspaces/switch", json={"agent_id": "default"})
                assert sw.status_code == 200
                assert sw.json()["active_workspace"] == "default"
                # 尝试删除仍在运行任务的 proj-a，返回 409
                busy_del = client.delete("/v1/system/workspaces/proj-a")
                assert busy_del.status_code == 409
            finally:
                release.set()
            assert fut.result(timeout=5).status_code == 200

        # 任务结束后，闲置的 proj-a 和 proj-b 均可正常删除
        assert client.delete("/v1/system/workspaces/proj-a").status_code == 200
        assert client.delete("/v1/system/workspaces/proj-b").status_code == 200
        remaining = [w["agent_id"] for w in client.get("/v1/system/workspaces").json()["workspaces"]]
        assert remaining == ["default"]


def test_mcp_status_reconnect_and_workspace_skills(isolated_system_env):
    """验证 MCP 未连接状态、一键重连工具发现，以及按工作空间隔离的 Skills 列表查询。"""
    mcp_script = isolated_system_env / "demo_mcp.py"
    mcp_script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "mcp = FastMCP('demo', instructions='测试 MCP 服务说明')\n"
        "@mcp.tool()\n"
        "def ping_service(host: str) -> str:\n"
        "    '''探测目标主机连通性'''\n"
        "    return f'pong:{host}'\n"
        "mcp.run()\n",
        encoding="utf-8",
    )

    with TestClient(create_app(), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        # 初始无 MCP 服务，状态为 disabled
        assert client.get("/v1/system/mcp").json()["status"] == "disabled"

        # 通过 PATCH 配置写入 MCP 服务，尚未连接时返回 not_connected
        patch_res = client.patch("/v1/system/config", json={
            "mcp": {
                "enabled": True,
                "servers": [{
                    "label": "net-tools",
                    "transport": "stdio",
                    "command": sys.executable,
                    "args": [str(mcp_script)],
                    "description": "网络连通性检查工具集",
                }],
            }
        })
        assert patch_res.status_code == 200
        pre_status = client.get("/v1/system/mcp").json()
        assert pre_status["status"] == "not_connected"
        assert pre_status["servers"][0]["label"] == "net-tools"

        # 一键重连 MCP，立即建立常驻连接并发现工具
        reconnected = client.post("/v1/system/mcp/reconnect")
        assert reconnected.status_code == 200
        mcp_data = reconnected.json()
        assert mcp_data["status"] == "connected"
        assert mcp_data["connected_servers"] == 1
        assert mcp_data["total_tools"] == 1
        assert mcp_data["servers"][0]["status"] == "connected"
        assert mcp_data["servers"][0]["tools"] == [{"name": "ping_service", "description": "探测目标主机连通性"}]

        # 验证 Skills 按工作空间读取
        skill_dir = workspace_module.AGENTS_ROOT / "default" / "skills" / "deploy-helper"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: deploy-helper\ndescription: 自动化部署检查工作流\nversion: 2.1.0\n---\n\n# 步骤\n1. 运行测试\n",
            encoding="utf-8",
        )
        skills = client.get("/v1/system/skills").json()
        assert skills == [{"name": "deploy-helper", "description": "自动化部署检查工作流", "version": "2.1.0"}]

        client.post("/v1/system/workspaces", json={"agent_id": "clean-ws", "switch": True})
        assert client.get("/v1/system/skills").json() == []


def test_system_endpoints_enforce_local_access_security(isolated_system_env):
    """/v1/system/* 接口同样遵循本机回环、Host 与跨站 Origin 防护。"""
    app = create_app()
    with TestClient(app, client=("192.0.2.10", 50000)) as remote:
        assert remote.get("/v1/system/config").status_code == 403
        assert remote.post("/v1/system/workspaces", json={"agent_id": "evil"}).status_code == 403

    with TestClient(app, base_url="http://127.0.0.1:8765", client=("127.0.0.1", 50000)) as local:
        assert local.get("/v1/system/config", headers={"Host": "evil.example"}).status_code == 403
        assert local.patch("/v1/system/config", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
