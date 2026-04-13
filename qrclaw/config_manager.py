"""
配置管理模块

统一管理用户配置，所有配置文件存放在 ~/.qrclaw/ 目录下：
- ~/.qrclaw/config.yaml      用户配置（API Key 等）
- ~/.qrclaw/permissions.yaml 权限配置
- ~/.qrclaw/MEMORY.md        中期记忆
- ~/.qrclaw/sessions/        会话历史
- ~/.qrclaw/logs/            日志文件
"""

import json
import os
import yaml
from pathlib import Path
from typing import Any
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.config_manager")

# 配置目录
CONFIG_DIR = Path.home() / ".qrclaw"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

# 默认配置
DEFAULT_CONFIG = {
    "agent": {
        "name": "QRClaw",
        "max_iterations": 100,
    },
    "llm": {
        "api_key": "",
        "model": "gpt-4o",
        "base_url": "",
        "api_base": "",
        "proxy_url": "",
        "max_tokens": 128000,
    },
    "search": {
        "tavily_api_key": "",
    },
    "log": {
        "level": "INFO",
        "max_days": 30,
        "to_file": True,
        "to_console": True,
        "console_level": "WARNING",
    },
    "heartbeat": {
        "enabled": True,
        "interval": 3600,
    },
    "compress": {
        "threshold_ratio": 0.6,
        "target_min_ratio": 0.20,
        "target_max_ratio": 0.25,
        "summary_max_tokens": 2560,
        "recent_max_tokens": 1536,
    },
    "mcp": {
        "servers": [],
    },
}

# 配置注释
CONFIG_HEADER = """# ═══════════════════════════════════════════════════════════════
# QRClaw 配置文件
# ═══════════════════════════════════════════════════════════════
# 文档：https://github.com/fu-qingrong/qrclaw
# 使用 LiteLLM 统一调用，支持 100+ 提供商（OpenAI/Claude/Gemini/DeepSeek 等）
# 模型格式：provider/model，如 minimax/MiniMax-M2.7-highspeed
# ═══════════════════════════════════════════════════════════════

"""


def ensure_config_dir():
    """确保配置目录存在"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def _write_config(config: dict):
    """写入配置文件"""
    ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        f.write(CONFIG_HEADER)
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def init_config():
    """初始化配置文件（不存在则创建默认配置）"""
    ensure_config_dir()

    if CONFIG_FILE.exists():
        logger.debug(f"使用配置文件: {CONFIG_FILE}")
        return

    _write_config(DEFAULT_CONFIG)
    logger.info(f"创建默认配置文件: {CONFIG_FILE}")
    logger.info("请编辑配置文件，填入你的 API Key")


def load_config():
    """加载配置并注入到环境变量"""
    if not CONFIG_FILE.exists():
        init_config()

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        config = _deep_merge(DEFAULT_CONFIG, config)
        _inject_to_env(config)
        logger.debug(f"已加载配置: {CONFIG_FILE}")

    except Exception as e:
        logger.error(f"加载配置失败: {e}", exc_info=True)


def _inject_to_env(config: dict):
    """将配置注入到环境变量"""
    # Agent 配置
    agent_config = config.get("agent", {})
    os.environ.setdefault("AGENT_NAME", agent_config.get("name", "QRClaw"))
    os.environ.setdefault("MAX_ITERATIONS", str(agent_config.get("max_iterations", 100)))

    # LLM 配置
    llm_config = config.get("llm", {})
    os.environ.setdefault("LITELLM_API_KEY", llm_config.get("api_key", ""))
    os.environ.setdefault("LITELLM_MODEL", llm_config.get("model", "gpt-4o"))
    os.environ.setdefault("LITELLM_BASE_URL", llm_config.get("base_url", ""))
    os.environ.setdefault("LITELLM_API_BASE", llm_config.get("api_base", ""))
    os.environ.setdefault("LITELLM_PROXY_URL", llm_config.get("proxy_url", ""))
    os.environ.setdefault("MODEL_MAX_TOKENS", str(llm_config.get("max_tokens", 128000)))

    # 搜索配置
    search_config = config.get("search", {})
    os.environ.setdefault("TAVILY_API_KEY", search_config.get("tavily_api_key", ""))

    # 日志配置
    log_config = config.get("log", {})
    os.environ.setdefault("LOG_LEVEL", log_config.get("level", "INFO"))
    os.environ.setdefault("LOG_MAX_DAYS", str(log_config.get("max_days", 30)))
    os.environ.setdefault("LOG_TO_FILE", str(log_config.get("to_file", True)).lower())
    os.environ.setdefault("LOG_TO_CONSOLE", str(log_config.get("to_console", True)).lower())
    os.environ.setdefault("LOG_CONSOLE_LEVEL", log_config.get("console_level", "WARNING"))

    # 心跳配置
    heartbeat_config = config.get("heartbeat", {})
    os.environ.setdefault("HEARTBEAT_ENABLED", str(heartbeat_config.get("enabled", True)).lower())
    os.environ.setdefault("HEARTBEAT_INTERVAL", str(heartbeat_config.get("interval", 3600)))

    # 压缩配置
    compress_config = config.get("compress", {})
    os.environ.setdefault("COMPRESS_SUMMARY_MAX_TOKENS", str(compress_config.get("summary_max_tokens", 2560)))
    os.environ.setdefault("COMPRESS_RECENT_MAX_TOKENS", str(compress_config.get("recent_max_tokens", 1536)))

    # MCP 配置
    mcp_config = config.get("mcp", {})
    servers = mcp_config.get("servers", [])
    os.environ.setdefault("MCP_SERVERS", json.dumps(servers, ensure_ascii=False))
    if servers:
        os.environ.setdefault("MCP_ENABLED", "true")


def get_config() -> dict:
    """获取完整配置字典"""
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    return _deep_merge(DEFAULT_CONFIG, config)


def get(key: str, default: Any = None) -> Any:
    """获取配置项（支持点号路径，如 llm.model）"""
    config = get_config()
    keys = key.split(".")
    value = config

    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return default

    return value


def set_config(key: str, value: Any):
    """设置配置项（支持点号路径）"""
    config = get_config()
    keys = key.split(".")
    target = config

    for k in keys[:-1]:
        if k not in target:
            target[k] = {}
        target = target[k]

    target[keys[-1]] = value
    _write_config(config)


def get_config_path() -> Path:
    """获取配置文件路径"""
    return CONFIG_FILE


def get_config_dir() -> Path:
    """获取配置目录路径"""
    return CONFIG_DIR
