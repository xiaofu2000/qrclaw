from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SessionState:
    """会话级状态，只管消息与事件。"""

    messages: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
