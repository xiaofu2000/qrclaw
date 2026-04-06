"""
review_memory 工具

让 LLM 审查中期记忆，智能判断哪些该保留、删除、合并。
增强版支持按类型管理记忆。
"""
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger
from qrclaw.memory import MemoryType

logger = get_logger("qrclaw.tools.review_memory")


class ReviewMemoryArgs(BaseModel):
    action: str = Field(
        default="analyze",
        description="操作类型：analyze（分析并返回报告）、cleanup（执行清理）、archive（归档旧记忆）、rebuild（重建索引）"
    )
    keep_recent: int = Field(
        default=10,
        description="保留最近 N 条记忆（用于 archive 操作）"
    )
    memory_type: str = Field(
        default="",
        description="记忆类型过滤：user/feedback/project/reference（空表示全部）"
    )


def _call_llm(prompt: str) -> str:
    """调用 LLM（统一接口）"""
    from qrclaw.providers import provider

    response = provider.chat(
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content


@register(
    description="审查中期记忆，由 LLM 智能判断哪些该保留、删除、合并。建议定期在 heartbeat 中调用。",
    args_model=ReviewMemoryArgs,
)
def review_memory(
    action: str = "analyze",
    keep_recent: int = 10,
    memory_type: str = "",
) -> str:
    """
    审查中期记忆。

    Args:
        action: 操作类型
            - analyze: 分析记忆状态，返回报告（默认）
            - cleanup: 让 LLM 智能清理记忆
            - archive: 归档旧记忆（保留最近 N 条）
            - rebuild: 重建 MEMORY.md 索引
        keep_recent: 保留最近 N 条记忆（仅 archive 操作使用）
        memory_type: 记忆类型过滤（空表示全部）

    Returns:
        str: 分析报告或操作结果
    """
    from qrclaw.agent import get_workspace
    from qrclaw.memory import MemoryManager, LongTermMemory
    from qrclaw.workspace import Workspace
    from datetime import datetime
    
    logger.info(f"审查中期记忆: action={action}, keep_recent={keep_recent}, type={memory_type}")
    
    # 获取工作空间
    ws = get_workspace() or Workspace("default")
    memory_mgr = ws.get_memory_manager()
    
    # 解析记忆类型
    filter_type = None
    if memory_type:
        try:
            filter_type = MemoryType(memory_type.lower())
        except ValueError:
            logger.warning(f"未知的记忆类型 '{memory_type}'，将搜索全部类型")
    
    if action == "analyze":
        # 分析记忆状态
        if filter_type:
            memories = memory_mgr.get_memories_by_type(filter_type)
        else:
            memories = memory_mgr.scan_all_memories()
        
        if not memories:
            return f"没有找到 {'类型为 ' + filter_type.value + ' 的' if filter_type else ''}记忆"
        
        # 统计
        stats = {mt.value: 0 for mt in MemoryType}
        for m in memories:
            stats[m.type.value] += 1
        
        stats_text = "\n".join([f"- {k}: {v} 条" for k, v in stats.items() if v > 0])
        
        # 构建分析内容
        content_parts = [f"# 记忆分析报告\n"]
        content_parts.append(f"\n## 总体统计\n\n共 {len(memories)} 条记忆：\n{stats_text}\n")
        
        # 按类型展示
        for mt in MemoryType:
            type_memories = [m for m in memories if m.type == mt]
            if type_memories:
                content_parts.append(f"\n## {mt.value.capitalize()} 记忆 ({len(type_memories)} 条)\n")
                for m in type_memories:
                    content_parts.append(f"\n### {m.name}\n")
                    content_parts.append(f"描述：{m.description}\n")
                    content_parts.append(f"更新时间：{m.updated_at.strftime('%Y-%m-%d %H:%M')}\n")
                    content_parts.append(f"内容预览：\n{m.content[:200]}...\n" if len(m.content) > 200 else f"内容：\n{m.content}\n")
        
        content = "".join(content_parts)
        
        # 让 LLM 分析
        prompt = f"""请分析以下记忆状态，回答：

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
        # 获取全部记忆
        memories = memory_mgr.scan_all_memories()
        if not memories:
            return "没有记忆需要清理"
        
        # 格式化为可读内容
        content_parts = []
        for m in memories:
            content_parts.append(f"\n## {m.name} [{m.type.value}]\n")
            content_parts.append(f"描述：{m.description}\n")
            content_parts.append(f"内容：\n{m.content}\n")
        
        content = "".join(content_parts)
        
        # 让 LLM 清理
        prompt = f"""你是记忆管理助手。请审查并清理以下记忆。

规则：
1. 删除过时信息：已完成的任务、旧的项目状态、不再相关的内容
2. 合并重复信息：相似的内容合并为一条
3. 保留重要信息：用户偏好、工作习惯、重要知识、长期有效的配置
4. 输出格式：JSON 数组，每项包含 name, description, type, content

直接输出 JSON 数组，不要解释。

---
{content}
---"""
        
        logger.info("调用 LLM 清理记忆")
        result = _call_llm(prompt)
        
        # 解析 LLM 返回的 JSON
        import json
        try:
            new_memories = json.loads(result)
            
            # 清除旧记忆并保存新记忆
            for m in memories:
                memory_mgr.delete_memory(m.name, m.type)
            
            for m in new_memories:
                memory_mgr.save_memory(
                    name=m["name"],
                    description=m.get("description", ""),
                    content=m.get("content", ""),
                    memory_type=MemoryType.from_str(m.get("type", "project")),
                )
            
            logger.info(f"记忆清理完成: {len(memories)} -> {len(new_memories)} 条")
            return f"记忆清理完成：\n- 原记忆：{len(memories)} 条\n- 新记忆：{len(new_memories)} 条\n\n清理后的记忆已保存。"
        except Exception as e:
            logger.error(f"解析清理结果失败: {e}")
            return f"清理结果解析失败: {e}\n\nLLM 返回：\n{result}"
    
    elif action == "archive":
        # 归档：保留最近 N 条
        memories = memory_mgr.scan_all_memories()
        
        if len(memories) <= keep_recent:
            return f"记忆数量（{len(memories)}）未超过保留数量（{keep_recent}），无需归档"
        
        # 按更新时间排序
        sorted_memories = sorted(memories, key=lambda m: m.updated_at)
        
        to_archive = sorted_memories[:-keep_recent]
        to_keep = sorted_memories[-keep_recent:]
        
        # 写入归档文件
        archive_dir = ws.root / "archive"
        archive_dir.mkdir(exist_ok=True)
        archive_file = archive_dir / f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        
        archive_lines = ["# 归档记忆\n", f"\n归档时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"]
        archive_lines.append(f"\n共归档 {len(to_archive)} 条记忆：\n")
        
        for m in to_archive:
            archive_lines.append(f"\n## {m.name} [{m.type.value}]\n")
            archive_lines.append(f"描述：{m.description}\n")
            archive_lines.append(f"原始更新时间：{m.updated_at.strftime('%Y-%m-%d %H:%M')}\n")
            archive_lines.append(f"\n内容：\n{m.content}\n")
        
        archive_file.write_text("".join(archive_lines), encoding="utf-8")
        
        # 删除归档的记忆
        for m in to_archive:
            memory_mgr.delete_memory(m.name, m.type)
        
        logger.info(f"记忆归档完成: 归档 {len(to_archive)} 条, 保留 {len(to_keep)} 条")
        
        return f"归档完成：\n- 归档 {len(to_archive)} 条旧记忆到 {archive_file.name}\n- 保留 {len(to_keep)} 条最近记忆"
    
    elif action == "rebuild":
        # 重建索引
        memory_mgr.rebuild_entrypoint()
        count = len(memory_mgr.scan_all_memories())
        
        logger.info(f"记忆索引重建完成: {count} 条记忆")
        return f"记忆索引已重建：\n- 共扫描 {count} 条记忆\n- MEMORY.md 已更新"
    
    else:
        return f"未知操作: {action}。可用操作: analyze, cleanup, archive, rebuild"
