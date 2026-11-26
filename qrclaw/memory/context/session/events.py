from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# 状态事件的固定代码与展示标题。界面和推送按 meta.code 判断，标题只用于显示，改文案不影响逻辑。
STATUS_TITLES = {
    "plan_started": "规划执行开始",
    "plan_failed": "规划执行失败",
    "step_running": "执行中",
    "step_completed": "步骤完成",
    "step_failed": "步骤失败",
    "replanning": "重规划中",
    "replanned": "重规划完成",
    "run_failed": "执行失败",
    "run_cancelled": "任务已停止",
}
_CODES_BY_TITLE = {title: code for code, title in STATUS_TITLES.items()}
# 推送给页面的步骤进度；规划失败原因可能含内部细节，不推送。
STREAMED_STATUS_CODES = frozenset({"step_running", "step_completed", "step_failed", "run_failed", "run_cancelled"})


def status_code(event: dict) -> str | None:
    """返回状态事件的代码；旧会话没有代码时按原标题识别。"""
    if event.get("kind") != "status":
        return None
    return (event.get("meta") or {}).get("code") or _CODES_BY_TITLE.get(event.get("title", ""))


def with_status_code(event: dict) -> dict:
    """读取旧会话时补齐状态代码，接口输出的状态事件都带 meta.code。"""
    code = status_code(event)
    if code and (event.get("meta") or {}).get("code") != code:
        event = {**event, "meta": {**(event.get("meta") or {}), "code": code}}
    return event


def status_event(code: str, content: str = "") -> "SessionEvent":
    """按代码生成状态事件，标题统一来自 STATUS_TITLES。"""
    return SessionEvent(kind="status", title=STATUS_TITLES[code], content=content, meta={"code": code})


@dataclass
class SessionEvent:
    kind: str
    title: str
    content: str = ""
    collapsed: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
