"""工作空间级 Wiki 管理接口，沿用本地服务的访问控制。"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .wiki_service import WikiService


class EditWikiPageRequest(BaseModel):
    """提交完整页面字段及读取时的版本，防止后台任务覆盖用户草稿。"""
    revision: int = Field(ge=0, strict=True)
    content: str = Field(min_length=1, max_length=500000)
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    related: list[str] = Field(default_factory=list, max_length=100)


def build_wiki_router(workspace) -> APIRouter:
    """接口目录由启动工作空间确定，客户端只能传入受校验的页面名称。"""
    router = APIRouter(prefix="/v1/wiki")
    service = WikiService(workspace.memory_dir)

    @router.get("/pages")
    def list_pages(q: str = Query(default="", max_length=500), offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
        """返回管理页所需的分页摘要。"""
        return service.list_pages(q, offset, limit)

    @router.get("/status")
    def status():
        """查询进度不会启动或重复提交后台任务。"""
        return service.status()

    @router.post("/lint")
    def lint():
        """Web 检查只运行确定性规则，语义检查通过用户明确要求 Agent 执行。"""
        return service.lint()

    @router.get("/pages/{name}")
    def get_page(name: str):
        """读取正文、来源和版本，已删除页面返回 404。"""
        try:
            return service.get_page(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.patch("/pages/{name}")
    def edit_page(name: str, request: EditWikiPageRequest):
        """用户确认的新正文独立保存为来源，版本不符时拒绝覆盖。"""
        service.wiki._page_path(name)
        for related in request.related:
            service.wiki._page_path(related)
        tags = list(dict.fromkeys(tag.strip() for tag in request.tags if tag.strip()))
        try:
            page = service.wiki.provenance.manual_edit(name, request.revision, request.content,
                                                      request.description, tags, request.related)
            return service.wiki.provenance.detail(page)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.delete("/pages/{name}")
    def delete_page(name: str, revision: int = Query(ge=0)):
        """删除前确认页面版本，取消整理且禁止旧结果将页面恢复。"""
        service.wiki._page_path(name)
        with service.wiki._lock:
            page = service.wiki.get_page(name)
            if not page:
                raise HTTPException(status_code=404, detail="记忆页面不存在")
            if page.revision != revision:
                raise HTTPException(status_code=409, detail="记忆已更新，请重新载入后删除")
            service.wiki.delete_page(name)
        return {"deleted": True}

    @router.post("/pages/{name}/repair")
    def repair(name: str):
        """将页面加入整理队列，遵守自动整理的闲置和冷却策略。"""
        service.wiki._page_path(name)
        try:
            service.wiki.provenance.queue(name)
            return service.get_page(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
