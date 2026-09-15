"""统一使用当前模型的分词器估算请求大小。"""
import litellm
from qrclaw.llm_service import get_llm_service


def count_text_tokens(text: str) -> int:
    """按当前正在使用的模型计算文本 Token 数。"""
    return litellm.token_counter(model=get_llm_service().model_name, text=text)


def count_messages_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """估算实际请求，包含系统提示、消息和工具定义。"""
    if not messages and not tools:
        return 0
    kwargs = {"model": get_llm_service().model_name, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    return litellm.token_counter(**kwargs)


def check_input_budget(messages: list[dict], tools: list[dict] | None = None) -> None:
    """发送前检查输入上限，为输出预留窗口，超限时保留数据并明确报错。"""
    from qrclaw.config import _MODEL_MAX_TOKENS
    if count_messages_tokens(messages, tools) > int(_MODEL_MAX_TOKENS * 0.9):
        raise ValueError("模型输入超过上下文窗口的 90%，请缩短输入或先压缩历史")
