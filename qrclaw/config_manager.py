"""
配置管理模块

负责配置文件本身的读写；解析、校验和环境变量覆盖在 qrclaw.config。
文件存放在 QRClaw 数据目录（默认 ~/.qrclaw，可用 QRCLAW_HOME 改）：
- config.yaml      用户配置（API Key 等）
- permissions.yaml 权限配置
- agents/          各 Agent 的会话、记忆与工作空间
- logs/            日志文件
"""

import yaml
from pathlib import Path
from typing import Any
from qrclaw.home import qrclaw_home
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.config_manager")

# 配置目录
CONFIG_DIR = qrclaw_home()
CONFIG_FILE = CONFIG_DIR / "config.yaml"

# 默认配置
DEFAULT_CONFIG = {
    "agent": {
        "name": "QRClaw",
        "max_iterations": 100,
        "plan_enabled": False,
        "max_concurrent_runs": 4,
    },
    "llm": {
        "api_key": "",
        "model": "gpt-4o",
        "base_url": "",
        "api_base": "",
        "proxy_url": "",
        "max_tokens": 485000,
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
        "threshold_ratio": 0.9,
        "target_min_ratio": 0.20,
        "target_max_ratio": 0.25,
        "summary_max_tokens": 2560,
        "recent_max_tokens": 1536,
    },
    "mcp": {
        "servers": [],
    },
}

# 可用环境变量覆盖的配置项（由 qrclaw.config 应用）：环境变量名 → (配置段, 键)。名字沿用旧版本，已有的部署脚本不用改。
ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "AGENT_NAME": ("agent", "name"),
    "MAX_ITERATIONS": ("agent", "max_iterations"),
    "PLAN_ENABLED": ("agent", "plan_enabled"),
    "MAX_CONCURRENT_RUNS": ("agent", "max_concurrent_runs"),
    "LLM_PROVIDER": ("llm", "provider"),
    "LITELLM_API_KEY": ("llm", "api_key"),
    "LITELLM_MODEL": ("llm", "model"),
    "LITELLM_BASE_URL": ("llm", "base_url"),
    "LITELLM_API_BASE": ("llm", "api_base"),
    "LITELLM_PROXY_URL": ("llm", "proxy_url"),
    "MODEL_MAX_TOKENS": ("llm", "max_tokens"),
    "TAVILY_API_KEY": ("search", "tavily_api_key"),
    "LOG_LEVEL": ("log", "level"),
    "LOG_MAX_DAYS": ("log", "max_days"),
    "LOG_TO_FILE": ("log", "to_file"),
    "LOG_TO_CONSOLE": ("log", "to_console"),
    "LOG_CONSOLE_LEVEL": ("log", "console_level"),
    "HEARTBEAT_ENABLED": ("heartbeat", "enabled"),
    "HEARTBEAT_INTERVAL": ("heartbeat", "interval"),
    "COMPRESS_THRESHOLD_RATIO": ("compress", "threshold_ratio"),
    "COMPRESS_SUMMARY_MAX_TOKENS": ("compress", "summary_max_tokens"),
    "COMPRESS_RECENT_MAX_TOKENS": ("compress", "recent_max_tokens"),
    "MCP_ENABLED": ("mcp", "enabled"),
    "MCP_SERVERS": ("mcp", "servers"),  # JSON 数组
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


def read_config_file() -> dict:
    """读取配置文件原始内容（不合并默认值）；文件不存在时先创建默认配置，内容损坏时记错误并按空配置处理。"""
    if not CONFIG_FILE.exists():
        init_config()
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.error(f"读取配置文件失败，使用默认配置: {e}", exc_info=True)
        return {}
    if not isinstance(data, dict):
        logger.error(f"配置文件顶层必须是映射，已忽略: {CONFIG_FILE}")
        return {}
    logger.debug(f"已读取配置: {CONFIG_FILE}")
    return data


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
