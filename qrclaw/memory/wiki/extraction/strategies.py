"""记忆提取的消息过滤与格式化：只把用户和助手正文交给提取模型。"""

import hashlib


def filter_memory_messages(messages: list) -> list[dict]:
    """只接收用户及助手正文，排除工具、系统、执行报告和重复的压缩摘要。"""
    result = []
    for msg in messages:
        role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "role", "")
        content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
        calls = msg.get("tool_calls") if isinstance(msg, dict) else getattr(msg, "tool_calls", None)
        kind = msg.get("kind") if isinstance(msg, dict) else getattr(msg, "kind", None)
        if role not in ("user", "assistant") or calls or kind == "execution_report":
            continue
        if isinstance(content, list):
            if any(b.get("type") in ("tool_use", "tool_result") for b in content if isinstance(b, dict)):
                continue
            content = "\n".join(b.get("text", "") for b in content
                                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str))
        if not isinstance(content, str) or not content.strip():
            continue
        if role == "assistant" and content.startswith("[SUMMARY]"):
            continue
        message_id = msg.get("uuid") if isinstance(msg, dict) else getattr(msg, "uuid", None)
        # 旧会话可能没有 UUID，正文指纹可在重启和压缩后保持稳定。
        message_id = message_id or hashlib.sha256(f"{role}\0{content}".encode()).hexdigest()
        result.append({"uuid": message_id, "role": role, "content": content})
    return result


def format_messages_for_llm(messages: list) -> str:
    """
    将消息列表格式化为 LLM 可读的文本。

    过滤规则：
    - 跳过 role=tool 的消息（工具返回结果）
    - 跳过含 tool_calls 的 assistant 消息（中间推理步骤）
    - 保留 role=user 和纯文字 role=assistant 消息，跳过压缩摘要
    - 传全量，不截断

    Args:
        messages: 消息列表

    Returns:
        str: 格式化后的文本
    """
    return "\n\n".join(f"[{msg['role']}] {msg['content']}" for msg in filter_memory_messages(messages))
