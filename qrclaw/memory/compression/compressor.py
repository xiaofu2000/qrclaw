"""
上下文压缩模块。

压缩策略：递归摘要 + 目标范围控制
- 目标范围：压缩后（摘要 + 短期记忆）占上下文窗口的 20%~25%
- 保留窗口：从最新消息往前选，只限制 tokens（不限制条数）
- 递归摘要：检测旧摘要，有则合并一起压缩，保证信息不丢失

Token 计算：使用 tiktoken 精确计算，支持 GPT-4o 等模型
"""
from collections import deque
from qrclaw.memory.token_utils import count_text_tokens
from qrclaw.config import (
    COMPRESS_SUMMARY_MAX_TOKENS, COMPRESS_SUMMARY_TARGET_TOKENS,
    COMPRESS_RECENT_MAX_TOKENS,
    COMPRESS_TARGET_MIN_RATIO, COMPRESS_TARGET_MAX_RATIO,
    _MODEL_MAX_TOKENS,
)
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.compressor")


def count_tokens(messages: list[dict]) -> int:
    """
    精确计算消息列表的 token 数。

    Args:
        messages: OpenAI 格式的消息列表

    Returns:
        int: token 总数
    """
    tokens = 0
    for msg in messages:
        # 每条消息有固定开销
        tokens += 4  # {"role": "...", "content": "..."} 格式开销
        for key, value in msg.items():
            if value is not None:
                tokens += len(_encoding.encode(str(value)))
    tokens += 2  # 对话开销
    return tokens


def _msg_token_count(msg: dict) -> int:
    """计算单条消息的 token 数（不含格式开销，用于快速比较）"""
    tokens = 4  # 格式开销
    for key, value in msg.items():
        if value is not None:
            tokens += len(_encoding.encode(str(value)))
    return tokens


SUMMARIZE_PROMPT = """请把下面的对话内容整理成结构化摘要，要求：
1. 按以下分类输出，没有内容的分类可以省略
2. 保留所有关键信息，不要遗漏重要细节
3. 语言简洁，不要废话
4. 控制摘要长度，目标约 {target_tokens} tokens

格式：
【用户信息】用户的基本信息、身份、偏好等
【已完成任务】已经完成的操作和结果
【关键结论】重要的决策、发现、约定
【待处理事项】还没完成的任务

对话内容：
{history}
"""


def _pick_recent(messages: list[dict], max_tokens: int = None) -> tuple[list[dict], list[dict]]:
    """
    从最新消息往前选，只限制 tokens（不限制条数）。
    保证 tool_calls(assistant) 和对应的 tool 消息不被拆分。

    返回 (recent, old)：recent 是保留的，old 是要压缩的。
    """
    if max_tokens is None:
        max_tokens = COMPRESS_RECENT_MAX_TOKENS

    # 使用 deque 高效地在头部插入
    recent = deque()
    token_count = 0

    for msg in reversed(messages):
        t = _msg_token_count(msg)
        if token_count + t > max_tokens:
            break
        recent.appendleft(msg)  # deque.appendleft 是 O(1)
        token_count += t

    # 确保不在 tool_calls 组中间截断：
    # 如果 recent 的第一条是 role=tool，说明对应的 assistant(tool_calls) 被切到 old 里了
    # 需要把这些孤立的 tool 消息也移到 old
    while recent and recent[0].get("role") == "tool":
        recent.popleft()

    # 转换为列表
    recent_list = list(recent)
    old = messages[:len(messages) - len(recent_list)]
    logger.debug(f"保留窗口: {len(recent_list)} 条, {token_count} tokens; 待压缩: {len(old)} 条")
    return recent_list, old


def summarize(session) -> None:
    """
    递归摘要压缩：
    1. 从最新消息往前选，只限制 tokens
    2. 检测是否有旧摘要，有则合并进待压缩内容（递归摘要）
    3. 调用 LLM 生成新摘要
    4. 检查总长度是否在 20%~25% 范围内，必要时调整
    """
    logger.info("开始压缩历史消息")

    # 计算目标范围
    target_min = int(_MODEL_MAX_TOKENS * COMPRESS_TARGET_MIN_RATIO)
    target_max = int(_MODEL_MAX_TOKENS * COMPRESS_TARGET_MAX_RATIO)
    logger.debug(f"目标范围: {target_min} ~ {target_max} tokens ({COMPRESS_TARGET_MIN_RATIO*100:.0f}% ~ {COMPRESS_TARGET_MAX_RATIO*100:.0f}%)")

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
        from qrclaw.providers import provider
        logger.debug(f"调用 LLM 生成摘要，目标: {COMPRESS_SUMMARY_TARGET_TOKENS} tokens")
        resp = provider.chat([
            {"role": "user", "content": SUMMARIZE_PROMPT.format(
                history=history_text,
                target_tokens=COMPRESS_SUMMARY_TARGET_TOKENS,
            )}
        ])
        summary = resp.content
        summary_tokens = count_text_tokens(summary)

        logger.info(f"摘要生成成功，长度: {len(summary)} 字符，{summary_tokens} tokens")
        logger.debug(f"摘要内容预览: {summary[:200]}...")

        # 计算短期记忆的 token 数
        recent_tokens = count_tokens(recent)
        total_tokens = summary_tokens + recent_tokens

        logger.info(f"压缩后总长度: {total_tokens} tokens (摘要 {summary_tokens} + 短期记忆 {recent_tokens})")

        # 检查是否在目标范围内
        if total_tokens > target_max:
            logger.warning(f"压缩后 {total_tokens} tokens 超过上限 {target_max}，减少短期记忆")
            # 减少短期记忆的 token 预算
            available_for_recent = target_max - summary_tokens
            if available_for_recent > 0:
                recent, old = _pick_recent(session.messages, max_tokens=available_for_recent)
                recent_tokens = count_tokens(recent)
                total_tokens = summary_tokens + recent_tokens
                logger.info(f"调整后: {total_tokens} tokens (摘要 {summary_tokens} + 短期记忆 {recent_tokens})")
            else:
                logger.warning("摘要本身已超过上限，保持现状")

        elif total_tokens < target_min:
            logger.info(f"压缩后 {total_tokens} tokens 低于下限 {target_min}，但这是好事，保持现状")

        # 重建 session.messages
        session.messages = [
            {"role": "assistant", "content": f"[SUMMARY] 以下是之前对话的结构化摘要：\n{summary}"},
            *recent,
        ]
        session._save()

        ratio = total_tokens / _MODEL_MAX_TOKENS * 100
        logger.info(f"压缩完成，消息数: {len(session.messages)}，占比: {ratio:.1f}%")
        print(f"  [系统] 上下文压缩完成，占比 {ratio:.1f}%")

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
