import os
from dotenv import load_dotenv

load_dotenv()

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
