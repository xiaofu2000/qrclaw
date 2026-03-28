"""
配置管理模块

统一管理用户配置，所有配置文件存放在 ~/.qrclaw/ 目录下：
- ~/.qrclaw/config.yaml      用户配置（API Key 等）
- ~/.qrclaw/permissions.yaml 权限配置
- ~/.qrclaw/MEMORY.md        中期记忆
- ~/.qrclaw/sessions/        会话历史
- ~/.qrclaw/logs/            日志文件
"""

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
        "max_iterations": 50,
    },
    "llm": {
        "provider": "openai",  # openai | vertex
        "openai": {
            "api_key": "",
            "model": "gpt-4o",
            "base_url": "",
            "max_tokens": 128000,
        },
        "vertex": {
            "api_key": "",
        },
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
    },
}

# 配置注释
CONFIG_HEADER = """# ═══════════════════════════════════════════════════════════════
# QRClaw 配置文件
# ═══════════════════════════════════════════════════════════════
# 文档：https://github.com/fu-qingrong/qrclaw
# 修改配置后无需重启，下次启动自动生效。
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
    os.environ.setdefault("AGENT_NAME", config["agent"]["name"])
    os.environ.setdefault("MAX_ITERATIONS", str(config["agent"]["max_iterations"]))

    os.environ.setdefault("LLM_PROVIDER", config["llm"]["provider"])
    os.environ.setdefault("OPENAI_API_KEY", config["llm"]["openai"]["api_key"])
    os.environ.setdefault("OPENAI_MODEL", config["llm"]["openai"]["model"])
    os.environ.setdefault("OPENAI_BASE_URL", config["llm"]["openai"]["base_url"])
    os.environ.setdefault("MODEL_MAX_TOKENS", str(config["llm"]["openai"]["max_tokens"]))
    os.environ.setdefault("VERTEX_API_KEY", config["llm"]["vertex"]["api_key"])

    os.environ.setdefault("TAVILY_API_KEY", config["search"]["tavily_api_key"])

    os.environ.setdefault("LOG_LEVEL", config["log"]["level"])
    os.environ.setdefault("LOG_MAX_DAYS", str(config["log"]["max_days"]))
    os.environ.setdefault("LOG_TO_FILE", str(config["log"]["to_file"]).lower())
    os.environ.setdefault("LOG_TO_CONSOLE", str(config["log"]["to_console"]).lower())
    os.environ.setdefault("LOG_CONSOLE_LEVEL", config["log"]["console_level"])

    os.environ.setdefault("HEARTBEAT_ENABLED", str(config["heartbeat"]["enabled"]).lower())
    os.environ.setdefault("HEARTBEAT_INTERVAL", str(config["heartbeat"]["interval"]))


def get_config() -> dict:
    """获取完整配置字典"""
    if not CONFIG_FILE.exists():
        return DEFAULT_CONFIG.copy()

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    return _deep_merge(DEFAULT_CONFIG, config)


def get(key: str, default: Any = None) -> Any:
    """获取配置项（支持点号路径，如 llm.openai.api_key）"""
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