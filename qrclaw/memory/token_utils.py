"""
Token 计算工具模块。

使用 LiteLLM 的 token_counter，针对不同模型自动选择正确的分词器，
比 tiktoken 对非 OpenAI 模型（如 MiniMax）更准确。
"""
import hashlib
import json
import threading
from collections import OrderedDict

import litellm
from qrclaw.config import LITELLM_MODEL
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.token_utils")

# 消息列表的 token 数等于空列表的固定开销加上每条消息各自的增量（对真实会话逐条核对过）。
# 会话每次读写都要重算上下文用量，长会话全量分词要上百毫秒；按消息内容缓存后只需计算新增消息。
_CACHE_LIMIT = 20000
_message_cache: "OrderedDict[tuple[str, str], int]" = OrderedDict()
_base_cache: dict[str, int] = {}
_cache_lock = threading.Lock()


def _message_key(message: dict) -> str:
    """按消息完整内容生成缓存键；内容任意字段变化都会重新计算。"""
    text = json.dumps(message, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()


def _cached_messages_tokens(messages: list[dict]) -> int:
    model = LITELLM_MODEL
    with _cache_lock:
        base = _base_cache.get(model)
    if base is None:
        base = litellm.token_counter(model=model, messages=[])
        with _cache_lock:
            _base_cache[model] = base
    total = base
    for message in messages:
        key = (model, _message_key(message))
        with _cache_lock:
            delta = _message_cache.get(key)
            if delta is not None:
                _message_cache.move_to_end(key)
        if delta is None:
            # 分词在锁外进行，多个会话并发时互不阻塞；重复计算同一条消息结果相同。
            delta = litellm.token_counter(model=model, messages=[message]) - base
            with _cache_lock:
                _message_cache[key] = delta
                if len(_message_cache) > _CACHE_LIMIT:
                    _message_cache.popitem(last=False)
        total += delta
    return total


def count_text_tokens(text: str) -> int:
    """
    计算文本的 token 数。

    Args:
        text: 文本内容
    Returns:
        int: token 数
    """
    return litellm.token_counter(model=LITELLM_MODEL, text=text)


def count_messages_tokens(messages: list[dict], tools: list[dict] | None = None) -> int:
    """
    计算消息列表的 token 数。

    Args:
        messages: OpenAI 格式的消息列表
        tools: 本次调用使用的工具定义，可选
    Returns:
        int: token 总数
    """
    if tools:
        return litellm.token_counter(model=LITELLM_MODEL, messages=messages, tools=tools)
    return _cached_messages_tokens(messages)
