"""配置加载：类型校验、环境变量覆盖规则、不再写回环境变量、数据目录隔离。"""

import logging
import os

import pytest

from qrclaw import config_manager
from qrclaw.config import load_settings
from qrclaw.home import qrclaw_home


def test_file_values_are_typed_and_defaults_fill_the_rest():
    settings = load_settings({"llm": {"model": "deepseek/chat", "max_tokens": 64000},
                              "agent": {"plan_enabled": True}, "unknown": {"x": 1}}, env={})
    assert settings.llm.model == "deepseek/chat"
    assert settings.llm.max_tokens == 64000
    assert settings.agent.plan_enabled is True
    assert settings.agent.max_iterations == 100
    assert settings.compress.threshold_ratio == 0.9


def test_env_overrides_file_and_warns_without_leaking_secrets(caplog):
    caplog.set_level(logging.WARNING, logger="qrclaw.config")
    settings = load_settings(
        {"llm": {"model": "file-model", "api_key": "file-secret"}, "log": {"to_file": True}},
        env={"LITELLM_MODEL": "env-model", "LITELLM_API_KEY": "env-secret", "LOG_TO_FILE": "false",
             "MODEL_MAX_TOKENS": "20000", "UNRELATED": "1"},
    )
    assert (settings.llm.model, settings.llm.api_key, settings.llm.max_tokens) == ("env-model", "env-secret", 20000)
    assert settings.log.to_file is False
    text = caplog.text
    assert "LITELLM_MODEL" in text and "file-model" in text and "env-model" in text
    assert "LITELLM_API_KEY" in text and "secret" not in text
    # 配置文件没写的项被环境变量设置时不算覆盖，不报警。
    assert "MODEL_MAX_TOKENS" not in text


def test_mcp_servers_from_file_or_env_json():
    servers = [{"label": "a", "transport": "stdio", "command": "x"}]
    assert load_settings({"mcp": {"servers": servers}}, env={}).mcp.active
    assert not load_settings({}, env={}).mcp.active
    assert not load_settings({"mcp": {"servers": servers}}, env={"MCP_ENABLED": "false"}).mcp.active
    from_env = load_settings({}, env={"MCP_SERVERS": '[{"label": "b", "command": "y"}]'})
    assert from_env.mcp.active and from_env.mcp.servers[0]["label"] == "b"


@pytest.mark.parametrize("data, env", [
    ({"agent": {"max_iterations": "很多"}}, {}),
    ({}, {"HEARTBEAT_INTERVAL": "一小时"}),
    ({"compress": {"threshold_ratio": 1.5}}, {}),
])
def test_invalid_values_are_rejected(data, env):
    with pytest.raises(ValueError):
        load_settings(data, env=env)


def test_loading_config_does_not_write_environment(monkeypatch):
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.delenv("MCP_SERVERS", raising=False)
    load_settings({"llm": {"api_key": "sk-test"}, "mcp": {"servers": [{"command": "x"}]}}, env={})
    assert "LITELLM_API_KEY" not in os.environ
    assert "MCP_SERVERS" not in os.environ


def test_tests_run_against_isolated_home():
    """测试进程的配置与数据目录在临时目录，不会读写本机 ~/.qrclaw。"""
    home = qrclaw_home()
    assert home != os.path.expanduser("~/.qrclaw")
    assert config_manager.CONFIG_FILE.parent == home


def test_broken_config_file_falls_back_to_defaults(temp_dir, monkeypatch):
    broken = temp_dir / "config.yaml"
    broken.write_text("llm: [这不是映射", encoding="utf-8")
    monkeypatch.setattr(config_manager, "CONFIG_FILE", broken)
    assert config_manager.read_config_file() == {}
    assert load_settings(env={}).llm.model == "gpt-4o"


def test_yaml_nulls_numbers_and_bad_sections_do_not_break_startup():
    """留空的配置项、纯数字密钥、写错类型的配置段不应让程序起不来。"""
    settings = load_settings({"mcp": None, "search": {"tavily_api_key": None}, "agent": {"name": None},
                              "llm": {"api_key": 12345}, "log": ["错的"]}, env={})
    assert settings.llm.api_key == "12345"
    assert settings.mcp.servers == [] and not settings.mcp.active
    assert settings.search.tavily_api_key == "" and settings.agent.name == "QRClaw"
    assert settings.log.level == "INFO"
    assert load_settings({"mcp": {"servers": None}}, env={}).mcp.servers == []


def test_override_warning_compares_validated_values_and_hides_urls(caplog):
    caplog.set_level(logging.WARNING, logger="qrclaw.config")
    load_settings({"agent": {"max_iterations": 100}, "log": {"to_file": True}}, env={"MAX_ITERATIONS": "100", "LOG_TO_FILE": "true"})
    assert caplog.text == ""
    load_settings({"llm": {"proxy_url": "http://user:old@proxy"}}, env={"LITELLM_PROXY_URL": "http://user:new@proxy"})
    assert "LITELLM_PROXY_URL" in caplog.text and "user:" not in caplog.text
    # 文件里的值无效、由环境变量纠正时照常启动
    assert load_settings({"compress": {"threshold_ratio": 1.5}}, env={"COMPRESS_THRESHOLD_RATIO": "0.8"}).compress.threshold_ratio == 0.8


def test_memory_extraction_settings_from_file_and_env():
    """记忆提取阈值走统一配置：文件段生效，旧的 MEMORY_* 环境变量仍可覆盖。"""
    settings = load_settings({"memory": {"min_turns": 5, "idle_seconds": 30}}, env={"MEMORY_COOLDOWN_SECONDS": "60"})
    assert (settings.memory.min_turns, settings.memory.idle_seconds, settings.memory.cooldown_seconds) == (5, 30, 60)
    assert settings.memory.init_threshold == 5000
    for bad in ({"min_turns": 0}, {"idle_seconds": -1}, {"cooldown_seconds": "inf"}):
        with pytest.raises(ValueError):
            load_settings({"memory": bad}, env={})


def test_recent_budget_defaults_to_window_ratio_and_explicit_values_win():
    """近期原文预算未配置时按窗口 12% 算；配置文件或环境变量写了固定值就用固定值。"""
    default = load_settings({}, env={}).compress
    assert default.recent_max_tokens is None
    assert default.recent_budget(485000) == 58200
    assert load_settings({"compress": {"recent_max_tokens": None}}, env={}).compress.recent_budget(10000) == 1200
    assert load_settings({"compress": {"recent_max_tokens": 4096}}, env={}).compress.recent_budget(485000) == 4096
    from_env = load_settings({"compress": {"recent_max_tokens": 4096}}, env={"COMPRESS_RECENT_MAX_TOKENS": "8000"})
    assert from_env.compress.recent_budget(485000) == 8000
    with pytest.raises(ValueError):
        load_settings({"compress": {"recent_max_tokens": 0}}, env={})


def test_removed_compress_keys_are_ignored_and_not_written_by_default():
    """旧配置文件里的 summary_max_tokens、target_*_ratio 不影响启动；新生成的默认配置也不再写死近期预算。"""
    old_file = {"compress": {"threshold_ratio": 0.9, "target_min_ratio": 0.2, "target_max_ratio": 0.25,
                             "summary_max_tokens": 2560}}
    settings = load_settings(old_file, env={"COMPRESS_SUMMARY_MAX_TOKENS": "100"})
    assert settings.compress.recent_max_tokens is None
    assert not hasattr(settings.compress, "summary_max_tokens")
    assert "COMPRESS_SUMMARY_MAX_TOKENS" not in config_manager.ENV_OVERRIDES
    assert config_manager.DEFAULT_CONFIG["compress"] == {"threshold_ratio": 0.9}
    assert load_settings(config_manager.DEFAULT_CONFIG, env={}).compress.recent_max_tokens is None
