from __future__ import annotations


class SystemPromptCache:
    """只负责 system prompt 的缓存、失效和重建标记。"""

    def __init__(self):
        self._cached: str | None = None
        self._dirty: bool = True

    def invalidate(self) -> None:
        self._dirty = True
        self._cached = None

    def get(self) -> str | None:
        if self._dirty:
            return None
        return self._cached

    def set(self, prompt: str) -> None:
        self._cached = prompt
        self._dirty = False
