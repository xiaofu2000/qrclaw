from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"] | str
    content: str | list[Any] | None = ""


class ChatCompletionRequest(BaseModel):
    model: str = "qrclaw-agent"
    session_id: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    stream: bool = False
    temperature: float | None = None
    edit_message_id: str | None = Field(default=None, min_length=1)


class SessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    updated_at: str
    run_status: Literal["idle", "queued", "running"] = "idle"
    active_subagents: bool = False


class SessionDetail(BaseModel):
    id: str
    title: str = "新对话"
    message_count: int
    updated_at: str
    history_version: int = 0
    run_status: Literal["idle", "queued", "running"] = "idle"
    active_subagents: bool = False
    run_error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    session_id: str | None = None


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)

