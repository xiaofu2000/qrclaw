"""系统配置热更新、MCP 连接状态、多工作空间切换与已安装技能管理接口。"""
from __future__ import annotations

import asyncio
import copy
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

import qrclaw.config as config_module
from qrclaw import config_manager
from qrclaw.agent import get_mcp_status, reconnect_mcp
from qrclaw.config import Settings, _file_sections, active_env_overrides, load_settings, reload_settings
from qrclaw.logger import get_logger
from qrclaw.skills.registry import Skill, SkillRegistry
import qrclaw.workspace as workspace_module
from qrclaw.workspace import is_valid_agent_id, list_agents

from .wiki_service import WikiService
from .workspace_hub import WorkspaceHub

logger = get_logger("qrclaw.server.system_routes")

_ALLOWED_CONFIG_SECTIONS = frozenset({"llm", "agent", "compress", "memory", "search", "heartbeat", "log", "mcp"})


class CreateWorkspaceRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)
    switch: bool = False


class SwitchWorkspaceRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=128)


def _mask_secret(secret: str) -> str:
    """脱敏密钥：空值返回空串；短密钥仅返回掩码；较长密钥仅保留末 4 位。"""
    if not secret:
        return ""
    if len(secret) <= 4:
        return "****"
    return f"****{secret[-4:]}"


def _serialize_config() -> dict[str, Any]:
    """生成安全脱敏的系统配置视图，绝不返回完整明文 API Key。"""
    s = config_module.settings
    has_api_key = bool(s.llm.api_key)
    api_key_masked = _mask_secret(s.llm.api_key)
    has_tavily = bool(s.search.tavily_api_key)
    tavily_masked = _mask_secret(s.search.tavily_api_key)

    return {
        "has_api_key": has_api_key,
        "api_key_masked": api_key_masked,
        "env_overrides_active": active_env_overrides(),
        "llm": {
            "provider": s.llm.provider,
            "model": s.llm.model,
            "base_url": s.llm.base_url,
            "api_base": s.llm.api_base,
            "proxy_url": s.llm.proxy_url,
            "max_tokens": s.llm.max_tokens,
            "has_api_key": has_api_key,
            "api_key_masked": api_key_masked,
        },
        "agent": {
            "name": s.agent.name,
            "max_iterations": s.agent.max_iterations,
            "plan_enabled": s.agent.plan_enabled,
            "max_concurrent_runs": s.agent.max_concurrent_runs,
        },
        "compress": {
            "threshold_ratio": s.compress.threshold_ratio,
            "recent_max_tokens": s.compress.recent_max_tokens,
        },
        "memory": {
            "init_threshold": s.memory.init_threshold,
            "update_interval": s.memory.update_interval,
            "min_turns": s.memory.min_turns,
            "idle_seconds": s.memory.idle_seconds,
            "cooldown_seconds": s.memory.cooldown_seconds,
        },
        "search": {
            "has_tavily_api_key": has_tavily,
            "tavily_api_key_masked": tavily_masked,
        },
        "heartbeat": {
            "enabled": s.heartbeat.enabled,
            "interval": s.heartbeat.interval,
        },
        "log": {
            "level": s.log.level,
            "max_days": s.log.max_days,
            "to_file": s.log.to_file,
            "to_console": s.log.to_console,
            "console_level": s.log.console_level,
        },
        "mcp": {
            "enabled": s.mcp.enabled,
            "active": s.mcp.active,
            "servers": copy.deepcopy(s.mcp.servers),
        },
    }


def _validate_mcp_servers(servers: Any) -> list[dict[str, Any]]:
    """校验 MCP 服务列表的基础字段合法性与标签唯一性。"""
    if not isinstance(servers, list):
        raise ValueError("MCP 服务列表格式错误，必须为数组")
    cleaned: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    for idx, item in enumerate(servers):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx + 1} 个 MCP 服务配置必须为对象")
        entry = dict(item)
        label = str(entry.get("label") or f"server-{idx}").strip()
        if not label:
            raise ValueError(f"第 {idx + 1} 个 MCP 服务标签不能为空")
        if label in seen_labels:
            raise ValueError(f"MCP 服务标签重复：{label}")
        seen_labels.add(label)
        entry["label"] = label

        transport = str(entry.get("transport") or "stdio").strip().lower()
        if transport not in ("stdio", "sse"):
            raise ValueError(f"MCP 服务 '{label}' 的传输方式无效（仅支持 stdio 或 sse）")
        entry["transport"] = transport

        if transport == "stdio":
            command = str(entry.get("command") or "").strip()
            if not command:
                raise ValueError(f"MCP 服务 '{label}'（stdio 模式）必须填写启动命令 command")
            entry["command"] = command
            args = entry.get("args", [])
            if args is None:
                args = []
            if not isinstance(args, list) or not all(isinstance(a, (str, int, float)) for a in args):
                raise ValueError(f"MCP 服务 '{label}' 的启动参数 args 必须为字符串数组")
            entry["args"] = [str(a) for a in args]
        else:
            url = str(entry.get("url") or "").strip()
            if not url:
                raise ValueError(f"MCP 服务 '{label}'（sse 模式）必须填写服务地址 url")
            entry["url"] = url

        cleaned.append(entry)
    return cleaned


def _format_validation_error(exc: Exception) -> str:
    """将 Pydantic / ValueError 转换为可读的中文错误提示。"""
    if isinstance(exc, ValidationError):
        parts: list[str] = []
        for err in exc.errors():
            loc = ".".join(str(x) for x in err.get("loc", ()))
            msg = err.get("msg", "参数值无效")
            if msg.startswith("Value error, "):
                msg = msg[len("Value error, "):]
            elif "greater than" in msg or "greater than or equal" in msg:
                msg = "数值超出允许范围（必须为正数）"
            elif "valid integer" in msg:
                msg = "必须为有效整数"
            elif "valid number" in msg:
                msg = "必须为有效数字"
            elif "valid boolean" in msg:
                msg = "必须为布尔值"
            parts.append(f"{loc}: {msg}" if loc else msg)
        return "配置校验失败：" + "；".join(parts)
    return f"配置校验失败：{exc}"


def _atomic_write_config(config_data: dict[str, Any]) -> None:
    """原子写回 config.yaml，防止并发或中断留下半截配置文件。"""
    target_file = config_manager.CONFIG_FILE
    target_file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".config.", suffix=".yaml.tmp", dir=str(target_file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(config_manager.CONFIG_HEADER)
            yaml.dump(config_data, handle, default_flow_style=False, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, target_file)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _scan_skills(skills_dir: Path) -> list[dict[str, str]]:
    """扫描指定 skills_dir 下的已安装技能，返回简要元数据列表。"""
    if not skills_dir.exists() or not skills_dir.is_dir():
        return []
    items: list[dict[str, str]] = []
    for sub in sorted(skills_dir.iterdir(), key=lambda p: p.name):
        if not sub.is_dir():
            continue
        skill_md = sub / "SKILL.md"
        if not skill_md.exists():
            skill_md = sub / "skill.md"
        if not skill_md.exists():
            continue
        try:
            skill = Skill.from_skill_md(skill_md)
            items.append({
                "name": skill.name,
                "description": skill.description,
                "version": str(skill.version),
            })
        except Exception as exc:
            logger.warning("读取技能失败 (%s): %s", sub, exc)
    return items


def _count_main_sessions(sessions_dir: Path) -> int:
    if not sessions_dir.exists() or not sessions_dir.is_dir():
        return 0
    return sum(1 for p in sessions_dir.glob("*.json") if not p.stem.startswith("sub-"))


def _count_wiki_pages(memory_dir: Path) -> int:
    pages_dir = memory_dir / "pages"
    if not pages_dir.exists() or not pages_dir.is_dir():
        return 0
    try:
        return int(WikiService(memory_dir).list_pages("", 0, 1)["total"])
    except Exception:
        return sum(1 for _ in pages_dir.glob("*.md"))


def _describe_workspace(hub: WorkspaceHub, agent_id: str) -> dict[str, Any]:
    """汇总单个工作空间的路径、激活状态、会话数、记忆页数、技能数与沙箱策略。"""
    from qrclaw.sandbox.config import config_manager as sandbox_cfg_mgr, is_sandbox_enabled

    known = hub.get_known_workspace(agent_id)
    root = known.root if known is not None else (workspace_module.AGENTS_ROOT / agent_id)
    sessions_dir = known.sessions_dir if known is not None else (root / "sessions")
    memory_dir = known.memory_dir if known is not None else (root / "memory")
    skills_dir = known.skills_dir if known is not None else (root / "skills")

    try:
        sandbox_cfg_mgr._load_config()
    except Exception:
        pass

    return {
        "agent_id": agent_id,
        "is_active": agent_id == hub.active_agent_id,
        "path": str(root),
        "session_count": _count_main_sessions(sessions_dir),
        "wiki_page_count": _count_wiki_pages(memory_dir),
        "skill_count": len(_scan_skills(skills_dir)),
        "sandbox_enabled": bool(is_sandbox_enabled(agent_id)),
        "has_active_runs": hub.has_active_runs(agent_id),
    }


def build_system_router(hub: WorkspaceHub) -> APIRouter:
    router = APIRouter(prefix="/v1/system")

    @router.get("/config")
    def get_system_config_api() -> dict[str, Any]:
        return _serialize_config()

    @router.patch("/config")
    def update_system_config_api(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="请求体必须为 JSON 对象")

        clear_api_key = bool(payload.get("clear_api_key", False))
        clear_tavily_key = bool(payload.get("clear_tavily_api_key", False))

        unknown_keys = set(payload.keys()) - _ALLOWED_CONFIG_SECTIONS - {"clear_api_key", "clear_tavily_api_key"}
        if unknown_keys:
            raise HTTPException(status_code=400, detail=f"包含不支持的配置段：{', '.join(sorted(unknown_keys))}")

        current_raw = config_manager.read_config_file()
        merged: dict[str, Any] = copy.deepcopy(config_manager.DEFAULT_CONFIG)
        for sec, val in current_raw.items():
            if isinstance(val, dict):
                merged.setdefault(sec, {}).update(copy.deepcopy(val))

        try:
            for section in _ALLOWED_CONFIG_SECTIONS:
                if section not in payload:
                    continue
                patch_sec = payload[section]
                if patch_sec is None:
                    continue
                if not isinstance(patch_sec, dict):
                    raise ValueError(f"配置段 '{section}' 必须为对象")

                patch_copy = copy.deepcopy(patch_sec)
                target = merged.setdefault(section, {})

                if section == "llm":
                    sec_clear = bool(patch_copy.pop("clear_api_key", False)) or clear_api_key
                    if "api_key" in patch_copy:
                        incoming_key = patch_copy.pop("api_key")
                        if incoming_key is None:
                            pass  # 保留原 api_key
                        elif isinstance(incoming_key, str) and incoming_key == "":
                            if sec_clear:
                                target["api_key"] = ""
                        elif isinstance(incoming_key, str):
                            target["api_key"] = incoming_key.strip()
                        else:
                            raise ValueError("llm.api_key 必须为字符串")
                    elif sec_clear:
                        target["api_key"] = ""

                elif section == "search":
                    sec_clear = bool(patch_copy.pop("clear_tavily_api_key", False)) or clear_tavily_key
                    if "tavily_api_key" in patch_copy:
                        incoming_tavily = patch_copy.pop("tavily_api_key")
                        if incoming_tavily is None:
                            pass
                        elif isinstance(incoming_tavily, str) and incoming_tavily == "":
                            if sec_clear:
                                target["tavily_api_key"] = ""
                        elif isinstance(incoming_tavily, str):
                            target["tavily_api_key"] = incoming_tavily.strip()
                        else:
                            raise ValueError("search.tavily_api_key 必须为字符串")
                    elif sec_clear:
                        target["tavily_api_key"] = ""

                elif section == "compress":
                    if "recent_max_tokens" in patch_copy and patch_copy["recent_max_tokens"] in (None, ""):
                        patch_copy.pop("recent_max_tokens")
                        target.pop("recent_max_tokens", None)

                elif section == "mcp":
                    if "servers" in patch_copy:
                        patch_copy["servers"] = _validate_mcp_servers(patch_copy["servers"])
                    if "enabled" in patch_copy and patch_copy["enabled"] is None:
                        patch_copy.pop("enabled")
                        target.pop("enabled", None)

                for k, v in patch_copy.items():
                    if v is None:
                        continue
                    target[k] = v

            # 先严格校验文件配置合法性（防止无效值被环境变量掩盖而写入磁盘），再校验叠加环境变量后的配置
            Settings.model_validate(_file_sections(merged))
            load_settings(file_data=merged)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=_format_validation_error(exc)) from exc

        _atomic_write_config(merged)
        reload_settings()
        return _serialize_config()

    @router.get("/mcp")
    def get_mcp_status_api() -> dict[str, Any]:
        return get_mcp_status()

    @router.post("/mcp/reconnect")
    async def reconnect_mcp_api() -> dict[str, Any]:
        return await asyncio.to_thread(reconnect_mcp)

    @router.get("/workspaces")
    def list_workspaces_api() -> dict[str, Any]:
        discovered = list_agents()
        ordered_ids: list[str] = ["default"]
        if hub.active_agent_id not in ordered_ids:
            ordered_ids.append(hub.active_agent_id)
        for aid in discovered:
            if aid not in ordered_ids and is_valid_agent_id(aid):
                ordered_ids.append(aid)

        return {
            "active_workspace": hub.active_agent_id,
            "workspaces": [_describe_workspace(hub, aid) for aid in ordered_ids],
        }

    @router.post("/workspaces")
    def create_workspace_api(req: CreateWorkspaceRequest) -> dict[str, Any]:
        from qrclaw.tools import agent_tools

        agent_id = req.agent_id.strip()
        if not is_valid_agent_id(agent_id):
            raise HTTPException(status_code=400, detail="工作空间 ID 不合法，只能包含字母、数字、中划线和下划线")

        if agent_id == "default" or hub.get_known_workspace(agent_id) is not None or (workspace_module.AGENTS_ROOT / agent_id).exists():
            raise HTTPException(status_code=409, detail=f"工作空间 '{agent_id}' 已存在")

        # 同步动态 AGENTS_ROOT（兼容测试环境 monkeypatch）
        agent_tools.AGENTS_ROOT = workspace_module.AGENTS_ROOT
        result = agent_tools.create_agent(agent_id)
        if result.startswith("错误") or result.startswith("❌"):
            raise HTTPException(status_code=400, detail=result)

        if req.switch:
            hub.switch(agent_id)

        summary = _describe_workspace(hub, agent_id)
        return {
            "active_workspace": hub.active_agent_id,
            "workspace": summary,
            **summary,
        }

    @router.post("/workspaces/switch")
    def switch_workspace_api(req: SwitchWorkspaceRequest) -> dict[str, Any]:
        agent_id = req.agent_id.strip()
        if not is_valid_agent_id(agent_id):
            raise HTTPException(status_code=400, detail="工作空间 ID 不合法，只能包含字母、数字、中划线和下划线")
        if not hub.has_workspace(agent_id):
            raise HTTPException(status_code=404, detail=f"工作空间 '{agent_id}' 不存在")

        hub.switch(agent_id)
        summary = _describe_workspace(hub, agent_id)
        return {
            "active_workspace": hub.active_agent_id,
            "workspace": summary,
            **summary,
        }

    @router.delete("/workspaces/{agent_id}")
    def delete_workspace_api(agent_id: str) -> dict[str, Any]:
        from qrclaw.tools import agent_tools

        cleaned = agent_id.strip()
        if not is_valid_agent_id(cleaned):
            raise HTTPException(status_code=400, detail="工作空间 ID 不合法")
        if cleaned == "default":
            raise HTTPException(status_code=400, detail="不能删除默认工作空间 default")
        if cleaned == hub.active_agent_id:
            raise HTTPException(status_code=409, detail="不能删除当前激活的工作空间，请先切换到其他工作空间")
        if not (workspace_module.AGENTS_ROOT / cleaned).exists() and hub.get_known_workspace(cleaned) is None:
            raise HTTPException(status_code=404, detail=f"工作空间 '{cleaned}' 不存在")
        if hub.has_active_runs(cleaned):
            raise HTTPException(status_code=409, detail=f"工作空间 '{cleaned}' 仍有正在执行的任务，请等待完成后再删除")

        hub.remove_workspace(cleaned)
        agent_tools.AGENTS_ROOT = workspace_module.AGENTS_ROOT
        result = agent_tools.delete_agent(cleaned)
        if result.startswith("错误") or result.startswith("❌"):
            raise HTTPException(status_code=400, detail=result)

        return {"deleted": True, "agent_id": cleaned, "active_workspace": hub.active_agent_id}

    @router.get("/skills")
    def list_skills_api(request: Request) -> list[dict[str, str]]:
        ws = hub.resolve_workspace(request)
        skills = _scan_skills(ws.skills_dir)
        # 若单例注册表正指向该目录，顺带刷新以识别新放入的技能文件
        registry = SkillRegistry.get_instance()
        if registry._loaded and registry._loaded_dir == ws.skills_dir:
            registry.skills.clear()
            registry._loaded = False
            registry.load_from_dir(ws.skills_dir)
        return skills

    return router
