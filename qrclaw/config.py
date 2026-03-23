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
# 超过 60% 触发压缩
COMPRESS_THRESHOLD = int(_MODEL_MAX_TOKENS * 0.6)
# 摘要最大输出 token 数（固定上限，给 LLM 的 max_tokens 参数）
COMPRESS_SUMMARY_MAX_TOKENS = int(os.getenv("COMPRESS_SUMMARY_MAX_TOKENS", "8000"))
# 压缩后短期记忆保留的 token 预算（估算值，用字符数换算）
COMPRESS_RECENT_MAX_TOKENS = int(os.getenv("COMPRESS_RECENT_MAX_TOKENS", str(int(_MODEL_MAX_TOKENS * 0.25))))
# 压缩后短期记忆最多保留条数
COMPRESS_RECENT_MAX_MSGS = int(os.getenv("COMPRESS_RECENT_MAX_MSGS", "10"))

# Tavily API（网页搜索）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 日志配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_MAX_DAYS = int(os.getenv("LOG_MAX_DAYS", "30"))
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"
LOG_TO_CONSOLE = os.getenv("LOG_TO_CONSOLE", "true").lower() == "true"
# 控制台默认只显示 WARNING 及以上级别的日志，避免输出太多
LOG_CONSOLE_LEVEL = os.getenv("LOG_CONSOLE_LEVEL", "WARNING")