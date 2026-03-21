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
# 摘要目标：压缩后控制在 10% 以内
COMPRESS_TARGET_TOKENS = int(_MODEL_MAX_TOKENS * 0.1)

# Tavily API（网页搜索）
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 日志配置
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = os.getenv("LOG_DIR", "logs")
LOG_MAX_DAYS = int(os.getenv("LOG_MAX_DAYS", "30"))
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "true").lower() == "true"
LOG_TO_CONSOLE = os.getenv("LOG_TO_CONSOLE", "true").lower() == "true"
# 控制台默认只显示 WARNING 及以上级别的日志，避免输出太多
LOG_CONSOLE_LEVEL = os.getenv("LOG_CONSOLE_LEVEL", "WARNING")