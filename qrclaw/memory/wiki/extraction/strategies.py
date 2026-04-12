"""
Extraction Strategies - 阈值策略、Token 估算、消息格式化

从 memory_extraction.py 迁移：
- should_extract (行 159-186)
- _count_tool_calls_since (行 188-212)
- _messages_since_last_extraction (行 214-238)
- _format_messages_for_llm (行 267-290)
- _estimate_tokens (行 394-414)
"""

from qrclaw.memory.wiki.extraction.config import ExtractionConfig


def should_extract(
    current_tokens: int,
    last_extraction_tokens: int,
    tool_calls_since: int,
    config: ExtractionConfig,
) -> bool:
    """
    判断是否应该触发提取

    条件：
    1. Token 增长超过阈值
    2. 工具调用间隔超过配置值
    """
    token_growth = current_tokens - last_extraction_tokens
    return token_growth >= config.minimum_tokens_between_update and tool_calls_since >= config.tool_calls_between_updates


def _count_tool_calls_since(
    messages: list[dict],
    last_extraction_idx: int,
) -> int:
    """计算上次提取后的工具调用次数"""
    count = 0
    for msg in messages[last_extraction_idx:]:
        if msg.get("role") == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                count += len(tool_calls)
    return count


def _messages_since_last_extraction(
    messages: list[dict],
    last_extraction_idx: int,
    max_messages: int,
) -> list[dict]:
    """截取上次提取后的消息，限制数量"""
    recent = messages[last_extraction_idx:]
    return recent[-max_messages:] if len(recent) > max_messages else recent


def _format_messages_for_llm(messages: list[dict]) -> str:
    """格式化消息列表为 LLM 可读的文本"""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")

        if role == "tool":
            continue

        if role == "assistant":
            content = msg.get("content", "")
            if content:
                lines.append(f"Assistant: {content}")

            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "unknown")
                args = func.get("arguments", "")
                lines.append(f"Assistant [tool call: {name}]: {args}")

        elif role == "user":
            content = msg.get("content", "")
            if content:
                lines.append(f"User: {content}")

    return "\n\n".join(lines)


def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（中英文混合估算）"""
    return _count_text_tokens(text)


def _count_text_tokens(text: str) -> int:
    """计算文本 token 数（中英文按字数估算）"""
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.25)


# ═══════════════════════════════════════════════════════════════════════════════
# 以下是从 MemoryExtractionNode 迁移的完整实现（支持 dict/对象双格式 + uuid 定位）
# ═══════════════════════════════════════════════════════════════════════════════


def count_tool_calls_since(
    messages: list,
    since_uuid: str = None,
) -> int:
    """
    计算指定消息后的工具调用次数。

    兼容 dict（OpenAI格式）和对象两种结构。

    Args:
        messages: 消息列表
        since_uuid: 截断点的消息 uuid，为 None 时统计全部

    Returns:
        int: 工具调用次数
    """
    count = 0
    found_start = since_uuid is None

    for msg in messages:
        # uuid 定位起始点
        if not found_start:
            msg_uuid = msg.get('uuid') if isinstance(msg, dict) else getattr(msg, 'uuid', None)
            if msg_uuid == since_uuid:
                found_start = True
            continue

        # 兼容 dict（OpenAI格式）和对象两种结构
        role = msg.get('role') if isinstance(msg, dict) else getattr(msg, 'type', None)
        if role != 'assistant':
            continue

        if isinstance(msg, dict):
            # OpenAI 格式：tool_calls 是独立字段
            tool_calls = msg.get('tool_calls') or []
            count += len(tool_calls)
        else:
            # Anthropic 对象格式：content 里有 tool_use block
            content = getattr(msg, 'content', []) or []
            if isinstance(content, list):
                count += sum(1 for block in content if isinstance(block, dict) and block.get('type') == 'tool_use')

    return count


def get_messages_since_last_extraction(
    messages: list,
    last_message_uuid: str = None,
) -> list:
    """
    返回上次提取之后新增的消息，通过 uuid 截断。

    - last_message_uuid 为 None 时（首次提取），返回全部消息
    - 找到 uuid 匹配的消息后，返回其后的所有消息
    - 找不到对应 uuid 时（消息被压缩/清理），回退返回全量

    Args:
        messages: 消息列表
        last_message_uuid: 上次提取最后一条消息的 uuid

    Returns:
        list: 截断后的消息列表
    """
    if last_message_uuid is None:
        return messages

    found = False
    result = []
    for msg in messages:
        if not found:
            msg_uuid = msg.get('uuid') if isinstance(msg, dict) else getattr(msg, 'uuid', None)
            if msg_uuid == last_message_uuid:
                found = True
            continue
        result.append(msg)

    if not found:
        # 回退：消息被清理，返回全量
        return messages

    return result


def format_messages_for_llm(messages: list) -> str:
    """
    将消息列表格式化为 LLM 可读的文本。

    过滤规则：
    - 跳过 role=tool 的消息（工具返回结果）
    - 跳过含 tool_calls 的 assistant 消息（中间推理步骤）
    - 保留 role=user 和纯文字 role=assistant 消息（[SUMMARY] 摘要也保留）
    - 传全量，不截断

    Args:
        messages: 消息列表

    Returns:
        str: 格式化后的文本
    """
    lines = []
    for msg in messages:
        role = msg.get('role', '') if isinstance(msg, dict) else getattr(msg, 'role', '')

        # 跳过 tool 返回
        if role == 'tool':
            continue

        # 跳过含 tool_calls 的 assistant 消息
        if role == 'assistant':
            tool_calls = msg.get('tool_calls') if isinstance(msg, dict) else getattr(msg, 'tool_calls', None)
            if tool_calls:
                continue

        content = msg.get('content', '') if isinstance(msg, dict) else getattr(msg, 'content', '')
        if not content:
            continue

        if isinstance(content, list):
            content = '\n'.join(
                b.get('text', '') or b.get('content', '')
                for b in content if b.get('type') == 'text'
            )

        if content.strip():
            lines.append(f"[{role}] {content}")

    return '\n\n'.join(lines)
