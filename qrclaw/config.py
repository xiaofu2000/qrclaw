"""QRClaw 运行配置。

启动时从 ~/.qrclaw/config.yaml（或 $QRCLAW_HOME/config.yaml）读取一次，校验类型后生成 ``settings``。
优先级：环境变量 > 配置文件 > 默认值。环境变量只认 ``config_manager.ENV_OVERRIDES`` 列出的名字，
覆盖配置文件里写过的值时记一条警告。配置不再写回环境变量，API Key 不会随环境传给 Shell 或沙箱子进程。

模块级常量（``LITELLM_MODEL``、``COMPRESS_THRESHOLD`` 等）是 ``settings`` 的别名，保留给现有调用方。
修改配置后需要重启进程。
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from qrclaw import config_manager
from qrclaw.config_manager import ENV_OVERRIDES
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.config")


class _Section(BaseModel):
    # 配置文件里多写的键不报错，便于新旧版本共用同一份配置；纯数字的密钥或名称按字符串处理。
    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True)


class AgentSettings(_Section):
    name: str = "QRClaw"
    max_iterations: int = 100
    plan_enabled: bool = False
    # 本地服务同时执行的对话数上限，超出的请求排队显示「等待执行」。
    max_concurrent_runs: int = Field(default=4, ge=1)


class LLMSettings(_Section):
    provider: str = "litellm"
    api_key: str = ""
    model: str = "gpt-4o"
    base_url: str = ""
    api_base: str = ""
    proxy_url: str = ""
    max_tokens: int = 485000


class SearchSettings(_Section):
    tavily_api_key: str = ""


class LogSettings(_Section):
    level: str = "INFO"
    max_days: int = 30
    to_file: bool = True
    to_console: bool = True
    # 控制台默认只显示 WARNING 及以上级别的日志，避免输出太多
    console_level: str = "WARNING"


class HeartbeatSettings(_Section):
    enabled: bool = True
    interval: int = 3600


class CompressSettings(_Section):
    # 默认超过 90% 触发压缩，预留 10% 给后续消息和回复。
    threshold_ratio: float = 0.9
    summary_max_tokens: int = 2560
    recent_max_tokens: int = 1536

    @field_validator("threshold_ratio")
    @classmethod
    def _ratio_in_range(cls, value: float) -> float:
        if not (math.isfinite(value) and 0 < value < 1):
            raise ValueError("压缩触发比例必须大于 0 且小于 1")
        return value


class MCPSettings(_Section):
    servers: list[dict[str, Any]] = []
    # 未显式设置时，配置了服务即启用。
    enabled: bool | None = None

    @property
    def active(self) -> bool:
        return bool(self.servers) if self.enabled is None else self.enabled and bool(self.servers)


class Settings(_Section):
    agent: AgentSettings = AgentSettings()
    llm: LLMSettings = LLMSettings()
    search: SearchSettings = SearchSettings()
    log: LogSettings = LogSettings()
    heartbeat: HeartbeatSettings = HeartbeatSettings()
    compress: CompressSettings = CompressSettings()
    mcp: MCPSettings = MCPSettings()


# 警告里不打印取值：密钥、可能带账号密码的地址、可能带令牌的 MCP 配置。
_SECRET_KEYS = frozenset({"api_key", "tavily_api_key", "servers", "base_url", "api_base", "proxy_url"})


def _file_sections(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """整理配置文件内容：空值（YAML 里留空或注释掉全部子项）视为未设置，非映射的配置段忽略并记错误。"""
    sections: dict[str, dict[str, Any]] = {}
    for section, values in data.items():
        if values is None:
            continue
        if not isinstance(values, dict):
            logger.error("配置段 %s 应为映射，已忽略: %r", section, values)
            continue
        sections[section] = {key: value for key, value in values.items() if value is not None}
    return sections


def load_settings(file_data: Mapping[str, Any] | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """合并配置文件与环境变量并校验；参数用于测试注入，省略时读取真实文件和环境。"""
    file_sections = _file_sections(config_manager.read_config_file() if file_data is None else file_data)
    env = os.environ if env is None else env
    data = {section: dict(values) for section, values in file_sections.items()}
    applied: list[tuple[str, str, str]] = []
    for name, (section, key) in ENV_OVERRIDES.items():
        if name in env:
            data.setdefault(section, {})[key] = json.loads(env[name]) if key == "servers" else env[name]
            applied.append((name, section, key))
    settings = Settings.model_validate(data)
    if applied:
        # 按校验后的值比较，环境变量 "100" 与文件里的 100 相同就不算覆盖。
        try:
            from_file: Settings | None = Settings.model_validate(file_sections)
        except ValueError:
            from_file = None  # 文件里的值本身无效、被环境变量纠正时，只提示覆盖，不比较取值
        for name, section, key in applied:
            if key not in file_sections.get(section, {}):
                continue
            before = getattr(getattr(from_file, section), key) if from_file else None
            after = getattr(getattr(settings, section), key)
            if from_file is None or before != after:
                change = "" if key in _SECRET_KEYS or from_file is None else f"（{before!r} → {after!r}）"
                logger.warning("环境变量 %s 覆盖了配置文件中的 %s.%s%s", name, section, key, change)
    return settings


settings = load_settings()

# ── 兼容别名：现有模块按名字导入这些常量 ─────────────────────────────────────
AGENT_NAME = settings.agent.name
MAX_ITERATIONS = settings.agent.max_iterations
PLAN_ENABLED = settings.agent.plan_enabled
MAX_CONCURRENT_RUNS = settings.agent.max_concurrent_runs

LLM_PROVIDER = settings.llm.provider
LITELLM_API_KEY = settings.llm.api_key
LITELLM_MODEL = settings.llm.model
LITELLM_BASE_URL = settings.llm.base_url
LITELLM_API_BASE = settings.llm.api_base
LITELLM_PROXY_URL = settings.llm.proxy_url

# 模型最大上下文窗口（token数）
_MODEL_MAX_TOKENS = settings.llm.max_tokens

COMPRESS_THRESHOLD_RATIO = settings.compress.threshold_ratio
COMPRESS_THRESHOLD = int(_MODEL_MAX_TOKENS * COMPRESS_THRESHOLD_RATIO)
# 压缩后目标范围：摘要 + 短期记忆 = 20%~25% 上下文窗口
COMPRESS_TARGET_MIN_RATIO = 0.20  # 最小 20%，避免压缩太短
COMPRESS_TARGET_MAX_RATIO = 0.25  # 最大 25%，避免压缩效果差
# 摘要目标占 10% 上下文窗口，短期记忆占 12%
COMPRESS_SUMMARY_TARGET_TOKENS = int(_MODEL_MAX_TOKENS * 0.10)
COMPRESS_SUMMARY_MAX_TOKENS = settings.compress.summary_max_tokens
COMPRESS_RECENT_TARGET_TOKENS = int(_MODEL_MAX_TOKENS * 0.12)
COMPRESS_RECENT_MAX_TOKENS = settings.compress.recent_max_tokens

TAVILY_API_KEY = settings.search.tavily_api_key

HEARTBEAT_ENABLED = settings.heartbeat.enabled
HEARTBEAT_INTERVAL = settings.heartbeat.interval

LOG_LEVEL = settings.log.level
LOG_MAX_DAYS = settings.log.max_days
LOG_TO_FILE = settings.log.to_file
LOG_TO_CONSOLE = settings.log.to_console
LOG_CONSOLE_LEVEL = settings.log.console_level

MCP_ENABLED = settings.mcp.active
MCP_SERVERS = settings.mcp.servers
