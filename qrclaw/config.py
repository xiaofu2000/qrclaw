import os
from qrclaw.config_manager import init_config, load_config

# 初始化并加载配置
init_config()
load_config()

# Agent 配置
AGENT_NAME = os.getenv("AGENT_NAME", "QRClaw")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "50"))

# 模型最大上下文窗口（token数）
_MODEL_MAX_TOKENS = int(os.getenv("MODEL_MAX_TOKENS", "128000"))

# 压缩配置
# 超过 60% 触发压缩
COMPRESS_THRESHOLD = int(_MODEL_MAX_TOKENS * 0.6)

# 压缩后目标范围：摘要 + 短期记忆 = 20%~25% 上下文窗口
COMPRESS_TARGET_MIN_RATIO = 0.20  # 最小 20%，避免压缩太短
COMPRESS_TARGET_MAX_RATIO = 0.25  # 最大 25%，避免压缩效果差

# 摘要目标占 12% 上下文窗口
COMPRESS_SUMMARY_TARGET_TOKENS = int(_MODEL_MAX_TOKENS * 0.12)
# 摘要最大输出 token 数（LLM 的 max_tokens 参数上限）
COMPRESS_SUMMARY_MAX_TOKENS = int(os.getenv("COMPRESS_SUMMARY_MAX_TOKENS", str(COMPRESS_SUMMARY_TARGET_TOKENS * 2)))

# 短期记忆目标占 10% 上下文窗口（摘要 12% + 短期 10% ≈ 22%，在 20%~25% 范围内）
COMPRESS_RECENT_TARGET_TOKENS = int(_MODEL_MAX_TOKENS * 0.10)
COMPRESS_RECENT_MAX_TOKENS = int(os.getenv("COMPRESS_RECENT_MAX_TOKENS", str(COMPRESS_RECENT_TARGET_TOKENS)))
# 短期记忆最多保留条数
COMPRESS_RECENT_MAX_MSGS = int(os.getenv("COMPRESS_RECENT_MAX_MSGS", "10"))

# Tavily API（网页搜索）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 心跳配置
HEARTBEAT_ENABLED = os.getenv("HEARTBEAT_ENABLED", "true").lower() == "true"
HEARTBEAT_INTERVAL = int(os.getenv("HEARTBEAT_INTERVAL", "3600"))  # 默认 1 小时

# 日志配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_MAX_DAYS = int(os.getenv("LOG_MAX_DAYS", "30"))
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"
LOG_TO_CONSOLE = os.getenv("LOG_TO_CONSOLE", "true").lower() == "true"
# 控制台默认只显示 WARNING 及以上级别的日志，避免输出太多
LOG_CONSOLE_LEVEL = os.getenv("LOG_CONSOLE_LEVEL", "WARNING")