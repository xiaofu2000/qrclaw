"""当前工具调用的上下文：调用 ID 与工具主动上报的结构化结果。

工具结果正文是给模型看的文字；界面需要的结构化信息（例如写入了哪些记忆页）
通过 report_tool_detail 交给执行器，随「工具完成」事件一起保存，界面不再解析文字。
工具在调用它的同一线程内同步执行，因此使用 ContextVar 即可隔离并发会话。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_call_id: ContextVar[str | None] = ContextVar("qrclaw_tool_call_id", default=None)
_detail: ContextVar[dict[str, Any] | None] = ContextVar("qrclaw_tool_detail", default=None)


@contextmanager
def tool_call(call_id: str | None) -> Iterator[None]:
    """在一次工具调用期间公开模型给出的调用 ID。"""
    token = _call_id.set(call_id)
    try:
        yield
    finally:
        _call_id.reset(token)


def current_call_id() -> str | None:
    return _call_id.get()


@contextmanager
def collect_tool_detail() -> Iterator[dict[str, Any]]:
    """收集工具在执行期间上报的结构化结果，退出后不再接受上报。"""
    detail: dict[str, Any] = {}
    token = _detail.set(detail)
    try:
        yield detail
    finally:
        _detail.reset(token)


def report_tool_detail(**detail: Any) -> None:
    """工具上报给界面的结构化结果；不在收集期间（如命令行直接调用）时忽略。"""
    target = _detail.get()
    if target is not None:
        target.update(detail)
