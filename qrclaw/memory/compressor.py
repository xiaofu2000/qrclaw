"""
上下文压缩模块。
后续可扩展更多压缩策略，比如滚动截断、RAG检索等。
"""
from openai import OpenAI
from qrclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, COMPRESS_TARGET_TOKENS
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.memory.compressor")

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)

SUMMARIZE_PROMPT = """请把下面的对话历史整理成结构化摘要，要求：
1. 按以下分类输出，没有内容的分类可以省略
2. 保留所有关键信息，不要遗漏重要细节
3. 语言简洁，不要废话

格式：
【用户信息】用户的基本信息、身份、偏好等
【已完成任务】已经完成的操作和结果
【关键结论】重要的决策、发现、约定
【待处理事项】还没完成的任务

对话历史：
{history}
"""


def summarize(session) -> None:
    """
    摘要压缩策略：
    把旧消息喂给 LLM 生成结构化摘要，用摘要+最近10条重建 messages。
    通过 max_tokens 控制摘要长度，压缩后控制在目标 token 数以内。
    """
    logger.info("开始压缩历史消息")
    
    recent = session.messages[-10:]
    old = session.messages[:-10]
    
    logger.debug(f"最近消息数: {len(recent)}, 旧消息数: {len(old)}")

    if not old:
        logger.warning("没有旧消息可压缩，跳过")
        return

    try:
        logger.debug("调用 LLM 生成摘要")
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            max_tokens=COMPRESS_TARGET_TOKENS,  # 限制摘要最多输出多少 token
            messages=[
                {"role": "user", "content": SUMMARIZE_PROMPT.format(history=str(old))}
            ],
        )
        summary = response.choices[0].message.content
        
        logger.info(f"摘要生成成功，长度: {len(summary)} 字符, 使用 {response.usage.total_tokens} tokens")
        logger.debug(f"摘要内容预览: {summary[:200]}...")

        session.messages = [
            {"role": "system", "content": f"以下是之前对话的结构化摘要：\n{summary}"},
            *recent,
        ]
        session._save()
        
        logger.info(f"压缩完成，消息数: {len(session.messages)} (摘要 + 最近{len(recent)}条)")
        print("  [系统] 压缩完成")
        
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