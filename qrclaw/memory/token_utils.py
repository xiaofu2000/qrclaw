"""
tiktoken 编码器单例模块。

所有模块统一从本模块导入 _encoding，避免重复初始化，提升启动性能。
"""
import tiktoken
from qrclaw.config import LITELLM_MODEL
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.token_utils")

# 初始化 tiktoken encoder（单例，全局只初始化一次）
try:
    _encoding = tiktoken.encoding_for_model(LITELLM_MODEL)
except KeyError:
    _encoding = tiktoken.get_encoding("cl100k_base")
    logger.debug(f"模型 {LITELLM_MODEL} 无对应 encoder，使用 cl100k_base")


def count_text_tokens(text: str) -> int:
    """
    精确计算文本的 token 数。

    Args:
        text: 文本内容
    Returns:
        int: token 数
    """
    return len(_encoding.encode(text))
