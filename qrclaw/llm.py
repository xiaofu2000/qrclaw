from qrclaw.providers import provider
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.llm")


def chat(messages: list[dict]) -> str:
    """
    发送消息给 LLM，返回回复文本。

    messages 格式：
    [
        {"role": "system", "content": "你是一个助手"},
        {"role": "user", "content": "你好"},
    ]
    """
    logger.debug(f"调用 chat，消息数: {len(messages)}")
    try:
        return provider.chat(messages)
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}", exc_info=True)
        raise
