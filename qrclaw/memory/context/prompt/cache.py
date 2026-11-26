from __future__ import annotations

import weakref

_LIVE_CACHES: "weakref.WeakSet[SystemPromptCache]" = weakref.WeakSet()


class SystemPromptCache:
    """只负责 system prompt 的缓存、失效和重建标记。"""

    def __init__(self):
        self._cached: str | None = None
        self._dirty: bool = True
        _LIVE_CACHES.add(self)

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


def invalidate_all_prompt_caches() -> None:
    """配置或 MCP 工具目录变更后，标记所有存活会话的系统提示缓存失效。"""
    for cache in list(_LIVE_CACHES):
        cache.invalidate()

