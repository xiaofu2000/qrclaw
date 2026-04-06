"""
Token 计算工具模块。

使用 LiteLLM 的 token_counter，针对不同模型自动选择正确的分词器，
比 tiktoken 对非 OpenAI 模型（如 MiniMax）更准确。
"""
import litellm
from qrclaw.config import LITELLM_MODEL
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.token_utils")


def count_text_tokens(text: str) -> int:
    """
    计算文本的 token 数。

    Args:
        text: 文本内容
    Returns:
        int: token 数
    """
    return litellm.token_counter(model=LITELLM_MODEL, text=text)


def count_messages_tokens(messages: list[dict]) -> int:
    """
    计算消息列表的 token 数。

    Args:
        messages: OpenAI 格式的消息列表
    Returns:
        int: token 总数
    """
    return litellm.token_counter(model=LITELLM_MODEL, messages=messages)
