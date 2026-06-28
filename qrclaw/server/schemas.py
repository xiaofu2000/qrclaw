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


class SessionSummary(BaseModel):
    id: str
    message_count: int
    updated_at: str


class SessionDetail(BaseModel):
    id: str
    message_count: int
    updated_at: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)


class CreateSessionRequest(BaseModel):
    session_id: str | None = None

