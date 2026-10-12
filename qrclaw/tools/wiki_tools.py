"""主 Agent 的按需 Wiki 搜索、分页读取、主动记忆和质量检查工具。"""
import json
import threading

from pydantic import BaseModel, Field
from qrclaw.tools.registry import register, AgentType
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.wiki")
_reads = threading.local()


def reset_wiki_reads() -> None:
    """每次用户请求重置正文预算，主 Agent 的 Plan 和 ReAct 共用。"""
    _reads.used = 0
    _reads.ranges = set()


def _get_wiki_memory():
    """工具只访问当前工作空间，不能由模型指定目录。"""
    from qrclaw.agent import get_workspace
    from qrclaw.workspace import Workspace
    from qrclaw.memory.wiki import WikiMemory
    return WikiMemory.for_workspace((get_workspace() or Workspace("default")).memory_dir)


def read_wiki_content(wiki, name: str, offset: int = 0, limit: int = 2000) -> dict:
    """单次约 2000 token、单轮约 8000 token；按版本和正文范围去重。"""
    if offset < 0 or not 1 <= limit <= 2000:
        raise ValueError("读取位置不能小于零，单次读取预算必须在 1 到 2000 之间")
    with wiki._lock:
        page = wiki.get_page(name)
        if page is None:
            return {"error": "页面不存在", "candidates": [p.name for p in wiki.search_pages(name)[:5]]}
        if not wiki.retrievable(page):
            return {"error": "页面来源失效或正在整理，暂时不能使用旧正文", "name": name, "status": page.status}
        if not hasattr(_reads, "ranges"):
            reset_wiki_reads()
        remaining = max(0, 8000 - _reads.used)
        if not remaining:
            return {"error": "本轮 Wiki 正文读取预算已用完"}
        end = min(len(page.content), offset + min(limit, remaining) * 3)
        key = (str(wiki.memory_dir), name, page.revision, offset, end)
        if key in _reads.ranges:
            return {"name": name, "revision": page.revision, "duplicate": True, "message": "同版本的该范围已读取，请复用已有正文"}
        content = page.content[offset:end]
        _reads.ranges.add(key)
        _reads.used += max(1, (len(content) + 2) // 3)
        detail = wiki.provenance.detail(page)
        return {"name": name, "revision": page.revision, "description": page.description, "content": content,
                "source_status": "部分历史来源未追溯" if detail["legacy_baseline"] else "来源未追溯" if not page.source_ids else "有效", "status": page.status,
                "source_ids": page.source_ids, "links": detail["links"], "backlinks": detail["backlinks"],
                "offset": offset, "next_offset": end if end < len(page.content) else None,
                "remaining_tokens": max(0, 8000 - _reads.used)}


class WriteWikiPageArgs(BaseModel):
    """主动记忆请求，由共享提取节点分析而非直接追加文件。"""
    content: str = Field(description="需要长期记住的内容，用自然语言描述")


class ReadWikiPageArgs(BaseModel):
    """offset 是正文字符位置，next_offset 可直接用于续读。"""
    name: str = Field(description="页面名称")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=2000, ge=1, le=2000, description="本次最多读取的估算 token")


class DeleteWikiPageArgs(BaseModel):
    """主动删除的目标页面。"""
    name: str = Field(description="页面名称")


class ListWikiPagesArgs(BaseModel):
    """只返回摘要，分页查询不消耗正文读取预算。"""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=20)


class SearchWikiArgs(BaseModel):
    """按关键词查询有限摘要，命中后按需读取正文。"""
    query: str = Field(min_length=1, description="页面名、标签或正文关键词")
    limit: int = Field(default=5, ge=1, le=5)


class LintWikiArgs(BaseModel):
    """语义检查只用于用户明确要求检查记忆矛盾的场景。"""
    semantic: bool = Field(default=False, description="用户明确要求模型检查跨页语义矛盾时才启用")


@register(description="主动记住用户确认的偏好、项目配置、约束和可复用经验；一次性回复和未确认推测不要写入。", args_model=WriteWikiPageArgs, agents=[AgentType.MAIN])
def write_wiki_page(content: str) -> str:
    """主动记忆立即执行，并同步维护索引和来源。"""
    try:
        from qrclaw.agent import get_extractor
        extractor = get_extractor()
        if extractor is None:
            return "错误：记忆节点未初始化，无法写入记忆。"
        written = extractor.write_now(content)
        return f"已写入 {written} 个 Wiki 页面。" if written else "未发现需要写入的长期记忆。"
    except Exception as exc:
        logger.error("主动记忆写入失败：%s", exc, exc_info=True)
        return f"错误：记忆写入失败 — {exc}"


@register(description="按需读取 Wiki 正文；支持 next_offset 续读，单轮预算有限，请只追踪与当前问题有关的引用。", args_model=ReadWikiPageArgs, agents=[AgentType.MAIN])
def read_wiki_page(name: str, offset: int = 0, limit: int = 2000) -> str:
    """正文以结构化结果返回，失效页面不暴露旧内容。"""
    try:
        return json.dumps(read_wiki_content(_get_wiki_memory(), name, offset, limit), ensure_ascii=False)
    except Exception as exc:
        logger.error("读取记忆失败：%s", exc)
        return f"错误：读取记忆失败 — {exc}"


@register(description="列出可使用 Wiki 的页面名称、摘要和标签，需要正文再调用 read_wiki_page。", args_model=ListWikiPagesArgs, agents=[AgentType.MAIN])
def list_wiki_pages(offset: int = 0, limit: int = 20) -> str:
    """只列出有效页面，待整理旧摘要也不进入 Agent 上下文。"""
    pages = _get_wiki_memory().list_pages()
    return json.dumps({"items": [p.to_index_entry() for p in pages[offset:offset + min(limit, 20)]], "total": len(pages)}, ensure_ascii=False)


@register(description="按需搜索 Wiki 页面，先检索相关摘要再读取；不要把全部记忆逐页读入上下文。", args_model=SearchWikiArgs, agents=[AgentType.MAIN])
def search_wiki(query: str, limit: int = 5) -> str:
    """搜索本地文件，不额外调用检索模型。"""
    pages = _get_wiki_memory().search_pages(query)[:min(limit, 5)]
    return json.dumps({"items": [{**p.to_index_entry(), "snippet": p.content[:160]} for p in pages]}, ensure_ascii=False)


@register(description="检查 Wiki 断链、孤立页、索引和来源状态；仅用户明确要求时启用语义检查，报告疑似问题而不自动删除。", args_model=LintWikiArgs, agents=[AgentType.MAIN])
def lint_wiki(semantic: bool = False) -> str:
    """复用 Web 的检查业务，默认无需额外模型调用。"""
    from qrclaw.server.wiki_service import WikiService
    return json.dumps(WikiService(_get_wiki_memory().memory_dir).lint(semantic), ensure_ascii=False)


@register(description="主动删除 Wiki 页面，并取消其后台整理；已执行的旧任务不能恢复此页。", args_model=DeleteWikiPageArgs, agents=[AgentType.MAIN])
def delete_wiki_page(name: str) -> str:
    """删除正文和索引，保留最小撤销标记。"""
    try:
        return f"已删除 Wiki 页面「{name}」。" if _get_wiki_memory().delete_page(name) else f"页面「{name}」不存在。"
    except Exception as exc:
        logger.error("删除记忆失败：%s", exc)
        return f"错误：删除记忆失败 — {exc}"
