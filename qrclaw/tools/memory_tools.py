"""
记忆工具

提供 Agent 可调用的记忆操作工具，对齐 Claude Code 的 write_memory / read_memory / review_memory 工具。

支持：
- 按类型分类存储（user/feedback/project/reference）
- 结构化 frontmatter
- MEMORY.md 入口索引
- 延迟更新索引（Dirty Flag 模式）
"""
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.memory import LongTermMemory, MemoryType
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.memory")


class WriteMemoryArgs(BaseModel):
    """写入记忆的参数"""
    content: str = Field(description="要记录的重要信息，使用 Markdown 格式")
    title: str = Field(description="可选的标题/名称，用于分类和组织记忆", default="")
    memory_type: str = Field(
        description="记忆类型，可选值：user/feedback/project/reference",
        default="project"
    )
    description: str = Field(
        description="简短描述（1-2句话），用于在索引中显示",
        default=""
    )


class ReadMemoryArgs(BaseModel):
    """读取记忆的参数"""
    memory_type: str = Field(
        description="可选，按类型过滤：user/feedback/project/reference",
        default=""
    )


class ListMemoryArgs(BaseModel):
    """列出记忆的参数"""
    memory_type: str = Field(
        description="可选，按类型过滤：user/feedback/project/reference",
        default=""
    )


class DeleteMemoryArgs(BaseModel):
    """删除记忆的参数"""
    name: str = Field(description="要删除的记忆名称（支持模糊匹配）")


class SearchMemoryArgs(BaseModel):
    """搜索记忆的参数"""
    query: str = Field(description="搜索关键词")


def _get_workspace_and_memory():
    """获取 Workspace 和 LongTermMemory 实例"""
    from qrclaw.agent import get_workspace
    from qrclaw.workspace import Workspace
    ws = get_workspace() or Workspace("default")
    memory = LongTermMemory(ws.memory_file, ws.memory_dir)
    return ws, memory


@register(
    description="写入中期记忆，仅用于记录用户偏好、项目配置等需要跨会话复用的信息。任务结果、调研报告等不要写入。支持四种类型：user（用户角色/偏好）、feedback（行为指导）、project（项目上下文）、reference（外部引用）",
    args_model=WriteMemoryArgs
)
def write_memory(
    content: str,
    title: str = "",
    memory_type: str = "project",
    description: str = "",
) -> str:
    """
    写入中期记忆

    支持结构化存储：
    - user: 用户角色、知识、偏好
    - feedback: 行为指导、改进建议
    - project: 项目上下文、配置
    - reference: 外部系统指针

    Args:
        content: 要记录的内容（Markdown 格式）
        title: 记忆标题/名称
        memory_type: 记忆类型（默认 project）
        description: 简短描述

    Returns:
        str: 操作结果
    """
    logger.debug(f"写入中期记忆: {title or '无标题'}, 类型={memory_type}")
    
    try:
        # 解析记忆类型
        mtype = MemoryType.from_str(memory_type)
    except Exception as e:
        return f"错误：无效的记忆类型 '{memory_type}'，有效值：user/feedback/project/reference"
    
    try:
        _, memory = _get_workspace_and_memory()
        
        # 如果提供了标题，使用结构化存储
        if title:
            success = memory.save_entry(
                name=title,
                content=content,
                memory_type=mtype,
                description=description,
            )
            if success:
                # 标记索引需要更新（Dirty Flag）
                memory.manager.indexer.mark_dirty()
                result = f"✅ 已保存记忆「{title}」（{mtype.value}）"
                if description:
                    result += f"\n描述: {description}"
                logger.info(result)
                return result
        
        # 否则使用追加模式
        success = memory.append(content, title if title else None, mtype)
        
        if success:
            # 标记索引需要更新（Dirty Flag）
            memory.manager.indexer.mark_dirty()
            result = f"已写入中期记忆: {title or '无标题'}（{mtype.value}）"
            if description:
                result += f"\n描述: {description}"
            logger.info(result)
            return result
        else:
            error_msg = "写入中期记忆失败"
            logger.error(error_msg)
            return error_msg
            
    except Exception as e:
        error_msg = f"错误：写入中期记忆失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg


@register(
    description="读取中期记忆，查看之前记录的重要信息",
    args_model=ReadMemoryArgs
)
def read_memory(memory_type: str = "") -> str:
    """
    读取中期记忆

    可选按类型过滤：
    - user: 用户角色/偏好
    - feedback: 行为指导
    - project: 项目上下文
    - reference: 外部引用

    Args:
        memory_type: 可选的类型过滤器

    Returns:
        str: 记忆内容（Markdown 格式）
    """
    logger.debug(f"读取中期记忆, 类型过滤={memory_type or '无'}")
    
    try:
        _, memory = _get_workspace_and_memory()
        
        # 如果指定了类型
        if memory_type:
            try:
                mtype = MemoryType.from_str(memory_type)
                entries = memory.manager.get_memories_by_type(mtype)
                if entries:
                    lines = [f"## {mtype.value.capitalize()} 记忆", ""]
                    for entry in entries:
                        lines.append(f"### {entry.name}")
                        if entry.description:
                            lines.append(f"*{entry.description}*")
                        lines.append("")
                        lines.append(entry.content)
                        lines.append("")
                    result = "\n".join(lines)
                    logger.info(f"读取 {mtype.value} 记忆: {len(entries)} 条")
                    return result
                else:
                    return f"暂无 {mtype.value} 类型的记忆"
            except Exception as e:
                return f"错误：无效的记忆类型 '{memory_type}'"
        
        # 读取全部（使用索引器，按需重建）
        content = memory.load()
        
        if not content or content.strip() == "# QRClaw 中期记忆":
            logger.info("中期记忆为空")
            return "## 中期记忆\n\n（暂无记录任何信息）\n"
        
        logger.info(f"读取中期记忆成功: {len(content)} 字符")
        return content
        
    except Exception as e:
        error_msg = f"错误：读取中期记忆失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg


@register(
    description="列出所有记忆条目，按类型分组显示",
    args_model=ListMemoryArgs
)
def list_memory(memory_type: str = "") -> str:
    """
    列出记忆条目

    Args:
        memory_type: 可选的类型过滤器

    Returns:
        str: 记忆条目列表
    """
    logger.debug(f"列出记忆条目, 类型过滤={memory_type or '无'}")
    
    try:
        _, memory = _get_workspace_and_memory()
        
        # 如果指定了类型
        if memory_type:
            try:
                mtype = MemoryType.from_str(memory_type)
                entries = memory.manager.get_memories_by_type(mtype)
                count = len(entries)
                lines = [f"## {mtype.value.capitalize()} 记忆（共 {count} 条）", ""]
                if entries:
                    for entry in entries:
                        lines.append(f"- **{entry.name}**")
                        if entry.description:
                            lines.append(f"  {entry.description}")
                        lines.append(f"  文件: {entry.filename}")
                        lines.append("")
                result = "\n".join(lines)
                logger.info(f"列出 {mtype.value} 记忆: {count} 条")
                return result
            except Exception as e:
                return f"错误：无效的记忆类型 '{memory_type}'"
        
        # 列出全部（按类型分组）
        all_memories = memory.manager.scan_all_memories()
        total = len(all_memories)
        lines = [f"## 中期记忆（共 {total} 条）", ""]
        
        for mtype in MemoryType:
            entries = memory.manager.get_memories_by_type(mtype)
            if entries:
                lines.append(f"### {mtype.value.capitalize()} ({len(entries)})")
                lines.append("")
                for entry in entries:
                    desc = entry.description or "（无描述）"
                    lines.append(f"- **{entry.name}** — {desc}")
                lines.append("")
        
        result = "\n".join(lines)
        logger.info(f"列出所有记忆: {total} 条")
        return result
        
    except Exception as e:
        error_msg = f"错误：列出记忆失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg


@register(
    description="删除指定的记忆条目",
    args_model=DeleteMemoryArgs
)
def delete_memory(name: str) -> str:
    """
    删除记忆条目

    Args:
        name: 要删除的记忆名称（支持模糊匹配）

    Returns:
        str: 操作结果
    """
    logger.debug(f"删除记忆: {name}")
    
    try:
        _, memory = _get_workspace_and_memory()
        
        # 查找记忆
        entry = memory.manager.get_memory(name)
        if not entry:
            return f"未找到记忆「{name}」"
        
        # 删除
        success = memory.manager.delete_memory(name)
        if success:
            # 标记索引需要更新（Dirty Flag）
            memory.manager.indexer.mark_dirty()
            result = f"✅ 已删除记忆「{entry.name}」"
            logger.info(result)
            return result
        else:
            return "删除记忆失败"
            
    except Exception as e:
        error_msg = f"错误：删除记忆失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg


@register(
    description="搜索记忆，根据关键词查找相关记忆条目",
    args_model=SearchMemoryArgs
)
def search_memory(query: str) -> str:
    """
    搜索记忆

    Args:
        query: 搜索关键词

    Returns:
        str: 匹配的记忆列表
    """
    logger.debug(f"搜索记忆: {query}")
    
    try:
        _, memory = _get_workspace_and_memory()
        
        memories = memory.manager.search_memories(query)
        
        if not memories:
            return f"没有找到与 '{query}' 相关的记忆"
        
        results = [f"找到 {len(memories)} 条相关记忆：\n"]
        for mem in memories:
            results.append(f"### {mem.name} ({mem.type.value})")
            results.append(f"{mem.description}")
            results.append(f"---\n")
        
        return "\n".join(results)
        
    except Exception as e:
        error_msg = f"错误：搜索记忆失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg
