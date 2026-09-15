"""
Wiki 工具

工具列表：
- write_wiki_page：触发记忆提取节点，让节点决定如何拆分写入（主 Agent）
- read_wiki_page：读取指定页面完整内容（主 Agent）
- delete_wiki_page：删除页面（主 Agent）
"""

from pydantic import BaseModel, Field
from qrclaw.tools.registry import register, AgentType
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.wiki")


def _get_wiki_memory():
    from qrclaw.agent import get_workspace
    from qrclaw.workspace import Workspace
    from qrclaw.memory.wiki import WikiMemory

    ws = get_workspace() or Workspace("default")
    return WikiMemory.for_workspace(ws.memory_dir)


def _invalidate_context_cache():
    try:
        from qrclaw.memory.context.context_manager import get_context_manager
        get_context_manager().invalidate_cache()
    except RuntimeError:
        pass


# ── 工具参数模型 ──────────────────────────────────────────────────────────────

class WriteWikiPageArgs(BaseModel):
    content: str = Field(description="要记录的内容，用自然语言描述即可。记忆节点会自动分析、拆分成合适的 Wiki 页面。")


class ReadWikiPageArgs(BaseModel):
    name: str = Field(description="要读取的页面名称")


class DeleteWikiPageArgs(BaseModel):
    name: str = Field(description="要删除的页面名称")


# ── 工具实现 ──────────────────────────────────────────────────────────────────

@register(
    description=(
        "主动触发记忆写入。把需要长期记住的内容交给记忆节点，"
        "节点会自动分析、按主题拆分成多个 Wiki 页面并写入。"
        "用于记录用户偏好、项目配置、技术决策、行为反馈等跨会话知识。"
        "任务结果、调研报告等一次性内容不要写入。"
    ),
    args_model=WriteWikiPageArgs,
    agents=[AgentType.MAIN],
)
def write_wiki_page(content: str) -> str:
    """同步写入长期记忆，完成后返回实际结果。"""
    try:
        from qrclaw.memory.context.context_manager import get_context_manager
        ctx = get_context_manager()
        try:
            written = ctx.extractor.write(content)
        finally:
            ctx.invalidate_cache()
        return f"已写入 {written} 个 Wiki 页面。" if written else "记忆节点判断无需更新 Wiki。"

    except Exception as e:
        logger.error(f"write_wiki_page 失败: {e}", exc_info=True)
        return f"错误：触发记忆写入失败 — {e}"


@register(
    description="读取指定 Wiki 页面的完整内容。系统提示词中只有页面摘要，需要详细内容时调用此工具。",
    args_model=ReadWikiPageArgs,
    agents=[AgentType.MAIN],
)
def read_wiki_page(name: str) -> str:
    try:
        wiki = _get_wiki_memory()
        page = wiki.get_page(name)
        if not page:
            candidates = wiki.search_pages(name)
            if candidates:
                names = ", ".join(f"「{p.name}」" for p in candidates[:5])
                return f"页面「{name}」不存在。相似页面：{names}"
            return f"页面「{name}」不存在，Wiki 中暂无相关内容。"

        lines = [
            f"# {page.name}",
            f"> {page.description}" if page.description else "",
            f"> 标签: {', '.join(page.tags)}" if page.tags else "",
            f"> 关联: {', '.join(page.related)}" if page.related else "",
            "",
            page.content,
        ]
        return "\n".join(l for l in lines if l is not None)

    except Exception as e:
        logger.error(f"read_wiki_page 失败: {e}", exc_info=True)
        return f"错误：读取 Wiki 页面失败 — {e}"


@register(
    description="删除指定的 Wiki 页面",
    args_model=DeleteWikiPageArgs,
    agents=[AgentType.MAIN],
)
def delete_wiki_page(name: str) -> str:
    try:
        wiki = _get_wiki_memory()
        success = wiki.delete_page(name)
        if success:
            _invalidate_context_cache()
            return f"✅ 已删除 Wiki 页面「{name}」"
        return f"页面「{name}」不存在。"

    except Exception as e:
        logger.error(f"delete_wiki_page 失败: {e}", exc_info=True)
        return f"错误：删除 Wiki 页面失败 — {e}"
