"""Tool execution boundary for graph nodes."""
from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console

from qrclaw.logger import get_logger
from qrclaw.tools.registry import execute, need_confirm

logger = get_logger("qrclaw.graph.nodes.tool_runner")


@dataclass
class ToolRunner:
    """Execute registered tools with permission handling."""

    console: Console | None = None
    auto_confirm: bool = False
    max_permission_denied: int = 2

    def __post_init__(self) -> None:
        self._permission_denied_count = 0

    def run(self, name: str, arguments: str) -> str:
        if need_confirm(name) and not self.auto_confirm:
            if self.console:
                self.console.print("[bold red]⚠ 需要确认[/bold red] 是否允许执行？(y/n) ", end="")
            choice = input().strip().lower()
            if choice != "y":
                return "用户拒绝执行此操作"

        try:
            result = execute(name, arguments)
            self._permission_denied_count = 0
            return result
        except PermissionError as exc:
            self._permission_denied_count += 1
            if self._permission_denied_count >= self.max_permission_denied:
                raise RuntimeError(f"连续 {self.max_permission_denied} 次权限拒绝，任务终止") from exc
            return str(exc)
        except Exception as exc:
            logger.error("工具执行失败: %s, 错误: %s", name, exc, exc_info=True)
            return f"工具执行失败: {str(exc)}"

