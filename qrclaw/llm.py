from openai import OpenAI
from qrclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.llm")

# base_url 不为空时传入，否则用 OpenAI 官方地址
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL or None,
)


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
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
        )
        
        usage = response.usage
        logger.info(f"LLM 响应成功，使用 {usage.total_tokens} tokens (prompt: {usage.prompt_tokens}, completion: {usage.completion_tokens})")
        
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}", exc_info=True)
        raise