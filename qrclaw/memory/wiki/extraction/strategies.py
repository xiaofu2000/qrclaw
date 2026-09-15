"""为路由、记忆提取和 Wiki 选择复用对话过滤与格式化。"""


def conversation_messages(messages: list[dict]) -> list[dict]:
    """保留用户输入和最终助手回复，排除系统及工具交互。"""
    return [message for message in messages if message.get("role") == "user"
            or (message.get("role") == "assistant" and not message.get("tool_calls"))]


def format_messages_for_llm(messages: list[dict]) -> str:
    """格式化有效对话；支持纯文本和文本内容块。"""
    lines = []
    for message in conversation_messages(messages):
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(block.get("text", "") for block in content if block.get("type") == "text")
        if content.strip():
            label = "用户" if message["role"] == "user" else "助手"
            lines.append(f"**{label}**: {content.strip()}")
    return "\n\n".join(lines)
