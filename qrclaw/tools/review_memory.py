"""
review_memory 工具

让 LLM 审查中期记忆，智能判断哪些该保留、删除、合并。
"""
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.review_memory")


class ReviewMemoryArgs(BaseModel):
    action: str = Field(
        default="analyze",
        description="操作类型：analyze（分析并返回报告）、cleanup（执行清理）、archive（归档旧记忆）"
    )
    keep_recent: int = Field(
        default=10,
        description="保留最近 N 条记忆（用于 archive 操作）"
    )


def _call_llm(prompt: str) -> str:
    """调用 LLM"""
    from qrclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
    from openai import OpenAI
    
    client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)
    
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    
    return response.choices[0].message.content


@register(
    description="审查中期记忆，由 LLM 智能判断哪些该保留、删除、合并。建议定期在 heartbeat 中调用。",
    args_model=ReviewMemoryArgs,
)
def review_memory(action: str = "analyze", keep_recent: int = 10) -> str:
    """
    审查中期记忆。

    Args:
        action: 操作类型
            - analyze: 分析记忆状态，返回报告（默认）
            - cleanup: 让 LLM 智能清理记忆
            - archive: 归档旧记忆（保留最近 N 条）
        keep_recent: 保留最近 N 条记忆（仅 archive 操作使用）

    Returns:
        str: 分析报告或操作结果
    """
    from qrclaw.agent import get_workspace
    from qrclaw.memory import LongTermMemory
    from qrclaw.workspace import Workspace
    from datetime import datetime
    import re
    
    logger.info(f"审查中期记忆: action={action}, keep_recent={keep_recent}")
    
    # 获取工作空间
    ws = get_workspace() or Workspace("default")
    memory = LongTermMemory(ws.memory_file)
    
    # 读取记忆
    content = memory.load()
    if not content or content.strip() == "# QRClaw 中期记忆":
        return "中期记忆为空，无需审查"
    
    if action == "analyze":
        # 让 LLM 分析记忆状态
        prompt = f"""请分析以下中期记忆，回答：

1. 总体评估：记忆质量如何？是否需要清理？
2. 过时信息：哪些内容已经过时（已完成的任务、旧的项目状态）？
3. 重复信息：是否有重复或相似的内容？
4. 重要信息：哪些内容必须保留（用户偏好、重要知识、长期配置）？
5. 清理建议：建议执行什么操作？

---
{content}
---"""
        
        logger.info("调用 LLM 分析记忆")
        result = _call_llm(prompt)
        logger.info("记忆分析完成")
        return result
    
    elif action == "cleanup":
        # 让 LLM 清理记忆
        prompt = f"""你是记忆管理助手。请审查并清理以下中期记忆。

规则：
1. 删除过时信息：已完成的任务、旧的项目状态、不再相关的内容
2. 合并重复信息：相似的内容合并为一条
3. 保留重要信息：用户偏好、工作习惯、重要知识、长期有效的配置
4. 保持格式：输出仍为 Markdown 格式，以 "# QRClaw 中期记忆" 开头

直接输出清理后的记忆内容，不要解释。

---
{content}
---"""
        
        logger.info("调用 LLM 清理记忆")
        new_content = _call_llm(prompt)
        
        # 确保格式正确
        if not new_content.strip().startswith("# QRClaw 中期记忆"):
            new_content = "# QRClaw 中期记忆\n\n" + new_content
        
        # 计算变化
        old_len = len(content)
        new_len = len(new_content)
        saved = old_len - new_len
        
        # 保存
        memory.save(new_content)
        logger.info(f"记忆清理完成: {old_len} -> {new_len} 字符, 节省 {saved} 字符")
        
        return f"记忆清理完成：\n- 原大小：{old_len} 字符\n- 新大小：{new_len} 字符\n- 节省：{saved} 字符\n\n清理后的记忆已保存。"
    
    elif action == "archive":
        # 归档：按条目分割，保留最近 N 条
        parts = content.split("---\n\n")
        
        # 过滤掉标题
        entries = [p.strip() for p in parts if p.strip() and not p.strip().startswith("# QRClaw")]
        
        if len(entries) <= keep_recent:
            return f"条目数（{len(entries)}）未超过保留数量（{keep_recent}），无需归档"
        
        to_archive = entries[:-keep_recent]
        to_keep = entries[-keep_recent:]
        
        # 写入归档文件
        archive_file = ws.root / "ARCHIVE.md"
        archive_lines = ["# 归档记忆\n", f"\n归档时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
        for e in to_archive:
            archive_lines.append("\n---\n\n")
            archive_lines.append(e)
        archive_file.write_text("".join(archive_lines), encoding="utf-8")
        
        # 更新 MEMORY.md
        memory_lines = ["# QRClaw 中期记忆\n"]
        for e in to_keep:
            memory_lines.append("\n---\n\n")
            memory_lines.append(e)
        memory.save("".join(memory_lines))
        
        logger.info(f"记忆归档完成: 归档 {len(to_archive)} 条, 保留 {len(to_keep)} 条")
        
        return f"归档完成：\n- 归档 {len(to_archive)} 条旧记忆到 ARCHIVE.md\n- 保留 {len(to_keep)} 条最近记忆"
    
    else:
        return f"未知操作: {action}。可用操作: analyze, cleanup, archive"