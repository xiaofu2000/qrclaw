from __future__ import annotations

from qrclaw.memory.context.session.events import SessionEvent
from qrclaw.memory.context.session.session import Session


class SessionStore:
    """会话状态门面，只包一层 Session，方便 Context 层解耦。"""

    def __init__(self, session: Session):
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    @property
    def messages(self) -> list[dict]:
        return self._session.messages

    @property
    def prompt_tokens(self) -> int:
        return self._session.prompt_tokens

    def add(self, message: dict) -> None:
        self._session.add(message)

    def add_event(self, event: SessionEvent | dict) -> None:
        self._session.add_event(event)

    def clear(self) -> None:
        self._session.clear()
