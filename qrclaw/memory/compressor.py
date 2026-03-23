"""
上下文压缩模块。

压缩策略：递归摘要 + 双重限制保留窗口
- 保留窗口：从最新消息往前数，同时满足「不超过N个token」和「最多M条」
- 递归摘要：检测旧摘要，有则合并一起压缩，保证信息不丢失
"""
from openai import OpenAI
from qrclaw.config import (
    OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL,
    COMPRESS_SUMMARY_MAX_TOKENS, COMPRESS_RECENT_MAX_TOKENS, COMPRESS_RECENT_MAX_MSGS,
)
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.compressor")

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)

SUMMARIZE_PROMPT = """请把下面的对话内容整理成结构化摘要，要求：
1. 按以下分类输出，没有内容的分类可以省略
2. 保留所有关键信息，不要遗漏重要细节
3. 语言简洁，不要废话

格式：
【用户信息】用户的基本信息、身份、偏好等
【已完成任务】已经完成的操作和结果
【关键结论】重要的决策、发现、约定
【待处理事项】还没完成的任务

对话内容：
{history}
"""


def _estimate_tokens(message: dict) -> int:
    """
    估算单条消息的 token 数。
    粗略公式：中文 1字≈1token，英文 4字≈1token，统一用字符数/2 保守估算。
    """
    content = message.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    return max(1, len(content) // 2)


def _pick_recent(messages: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    从最新消息往前选，同时满足：
      1. 累计 token 不超过 COMPRESS_RECENT_MAX_TOKENS
      2. 条数不超过 COMPRESS_RECENT_MAX_MSGS

    返回 (recent, old)：recent 是保留的，old 是要压缩的。
    """
    recent = []
    token_count = 0

    for msg in reversed(messages):
        if len(recent) >= COMPRESS_RECENT_MAX_MSGS:
            break
        t = _estimate_tokens(msg)
        if token_count + t > COMPRESS_RECENT_MAX_TOKENS:
            break
        recent.insert(0, msg)
        token_count += t

    old = messages[:len(messages) - len(recent)]
    logger.debug(f"保留窗口: {len(recent)} 条, 约 {token_count} tokens; 待压缩: {len(old)} 条")
    return recent, old


def summarize(session) -> None:
    """
    递归摘要压缩：
    1. 按双重限制（token数+条数）选出保留的短期记忆
    2. 检测是否有旧摘要，有则合并进待压缩内容（递归摘要）
    3. 调用 LLM 生成新摘要，重建 session.messages
    """
    logger.info("开始压缩历史消息")

    recent, old = _pick_recent(session.messages)

    if not old:
        logger.warning("没有旧消息可压缩，跳过")
        return

    # 检测 old 里是否有旧摘要（递归摘要：旧摘要一起参与本次压缩）
    has_old_summary = any(
        msg.get("role") == "assistant" and str(msg.get("content", "")).startswith("[SUMMARY]")
        for msg in old
    )
    if has_old_summary:
        logger.info("检测到旧摘要，合并进行递归压缩")

    history_text = "\n\n".join(
        f"[{msg.get('role', 'unknown')}]: {msg.get('content', '')}"
        for msg in old
        if msg.get("content")
    )

    try:
        logger.debug("调用 LLM 生成摘要")
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=COMPRESS_SUMMARY_MAX_TOKENS,
            messages=[
                {"role": "user", "content": SUMMARIZE_PROMPT.format(history=history_text)}
            ],
        )
        summary = response.choices[0].message.content

        logger.info(f"摘要生成成功，长度: {len(summary)} 字符，使用 {response.usage.total_tokens} tokens")
        logger.debug(f"摘要内容预览: {summary[:200]}...")

        session.messages = [
            {"role": "assistant", "content": f"[SUMMARY] 以下是之前对话的结构化摘要：\n{summary}"},
            *recent,
        ]
        session._save()

        logger.info(f"压缩完成，消息数: {len(session.messages)} (摘要1条 + 短期记忆{len(recent)}条)")
        print("  [系统] 上下文压缩完成")

    except Exception as e:
        logger.error(f"压缩失败: {e}", exc_info=True)
        raise


def truncate(session, keep: int = 20) -> None:
    """
    滚动截断策略：直接丢弃旧消息，只保留最近 keep 条。
    最简单，但会丢失早期信息。
    """
    if len(session.messages) > keep:
        old_count = len(session.messages)
        session.messages = session.messages[-keep:]
        session._save()
        logger.info(f"滚动截断完成，保留最近 {keep} 条消息 (丢弃 {old_count - keep} 条)")
    else:
        logger.debug(f"消息数未超过阈值 ({len(session.messages)}/{keep})，无需截断")
