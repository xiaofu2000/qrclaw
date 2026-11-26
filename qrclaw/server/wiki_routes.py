"""工作空间级 Wiki 管理、单页版本、整库版本与记忆做梦接口，沿用本地服务的访问控制。"""
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from .wiki_service import WikiService


class EditWikiPageRequest(BaseModel):
    """提交完整页面字段及读取时的版本，防止后台任务覆盖用户草稿。"""
    revision: int = Field(ge=0, strict=True)
    content: str = Field(min_length=1, max_length=500000)
    description: str = Field(default="", max_length=1000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    related: list[str] = Field(default_factory=list, max_length=100)


class RestoreWikiPageRequest(BaseModel):
    """回滚到指定单页历史版本，携带当前页面版本号防止覆盖并发更新。"""
    revision: int = Field(ge=1, strict=True)
    current_revision: int | None = Field(default=None, ge=0, strict=True)


class CreateDreamRequest(BaseModel):
    """发起一次记忆做梦整理任务。"""
    session_ids: list[str] | None = Field(default=None, max_length=100)
    instructions: str = Field(default="", max_length=4000)


class ApplyDreamRequest(BaseModel):
    """确认切换到做梦新记忆库。"""
    carry_over: bool = True


def build_wiki_router(workspace) -> APIRouter:
    """接口目录由启动工作空间确定，客户端只能传入受校验的页面或任务标识。"""
    router = APIRouter(prefix="/v1/wiki")
    service = WikiService(workspace.memory_dir, workspace.sessions_dir)

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

    # ── 单页历史版本接口 ──────────────────────────────────────────────────────

    @router.get("/pages/{name}/history")
    def list_page_history(name: str):
        """列出单页不可变历史版本列表。"""
        return service.list_page_history(name)

    @router.get("/pages/{name}/history/{revision}")
    def get_page_history(name: str, revision: int):
        """读取单页指定历史版本的完整正文与元数据。"""
        try:
            return service.get_page_history_version(name, revision)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/pages/{name}/restore")
    def restore_page(name: str, request: RestoreWikiPageRequest):
        """将单页回滚到指定历史版本（按用户整页确认生成 manual 来源，版本号单调递增）。"""
        try:
            return service.restore_page_history(name, request.revision, request.current_revision)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ── 整库版本与回退接口 ────────────────────────────────────────────────────

    @router.get("/store-versions")
    def list_store_versions():
        """列出已归档的整库版本快照。"""
        return service.list_store_versions()

    @router.post("/store-versions/{version_id}/restore")
    def restore_store_version(version_id: str):
        """回退到指定的整库版本（回退前自动归档当前库）。"""
        try:
            return service.restore_store_version(version_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ── 记忆做梦（整理）接口 ──────────────────────────────────────────────────

    @router.post("/dreams")
    def create_dream(request: CreateDreamRequest | None = Body(default=None)):
        """创建并后台启动记忆做梦任务。"""
        req = request or CreateDreamRequest()
        try:
            return service.create_dream(req.session_ids, req.instructions)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/dreams")
    def list_dreams(limit: int = Query(default=20, ge=1, le=100), include_archived: bool = Query(default=False)):
        """列出做梦任务历史，新的在前。"""
        return service.list_dreams(limit=limit, include_archived=include_archived)

    @router.get("/dreams/{dream_id}")
    def get_dream(dream_id: str):
        """读取做梦任务状态、用量、步数与新旧库对比统计。"""
        try:
            return service.get_dream(dream_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/dreams/{dream_id}/pages")
    def list_dream_pages(dream_id: str, q: str = Query(default="", max_length=500)):
        """浏览或搜索做梦新库中的页面。"""
        try:
            return service.list_dream_pages(dream_id, query=q)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/dreams/{dream_id}/pages/{name}")
    def get_dream_page(dream_id: str, name: str):
        """读取做梦新库单页正文及与原库的前后对比。"""
        try:
            return service.get_dream_page(dream_id, name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/dreams/{dream_id}/transcript")
    def get_dream_transcript(dream_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100)):
        """分页查看做梦过程的模型响应与工具调用记录。"""
        try:
            return service.get_dream_transcript(dream_id, offset=offset, limit=limit)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/dreams/{dream_id}/cancel")
    def cancel_dream(dream_id: str):
        """取消排队中或运行中的做梦任务。"""
        try:
            return service.cancel_dream(dream_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/dreams/{dream_id}/apply-preview")
    def preview_apply_dream(dream_id: str):
        """切换前检查整理期间原库发生的改动与删除。"""
        try:
            return service.preview_apply_dream(dream_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/dreams/{dream_id}/apply")
    def apply_dream(dream_id: str, request: ApplyDreamRequest | None = Body(default=None)):
        """切换到做梦新记忆库并归档原库。"""
        req = request or ApplyDreamRequest()
        try:
            return service.apply_dream(dream_id, carry_over=req.carry_over)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/dreams/{dream_id}/archive")
    def archive_dream(dream_id: str):
        """放弃做梦结果并清理副本。"""
        try:
            return service.archive_dream(dream_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
