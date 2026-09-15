"""Tool execution boundary for graph nodes."""
from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from qrclaw.logger import get_logger
from qrclaw.execution.context import RunCancelled
from qrclaw.tools.registry import execute, need_confirm

logger = get_logger("qrclaw.graph.nodes.tool_runner")


@dataclass
class ToolRunner:
    """Execute registered tools with permission handling."""

    console: Console | None = None
    auto_confirm: bool = False
    max_permission_denied: int = 2
    execution_context: object | None = None

    def __post_init__(self) -> None:
        self._permission_denied_count = 0

    def run(self, name: str, arguments: str) -> str:
        parsed_arguments = self._parse_arguments(arguments)
        tool_call_id = None
        if self.execution_context is not None:
            self.execution_context.check_cancelled()
            tool_call_id = self.execution_context.new_id("tool")

        if need_confirm(name) and not self.auto_confirm:
            allowed = self._request_approval(
                name=name,
                arguments=parsed_arguments,
                tool_call_id=tool_call_id,
            )
            if not allowed:
                if self.execution_context is not None:
                    self.execution_context.publish(
                        "tool.denied",
                        {
                            "tool_call_id": tool_call_id,
                            "name": name,
                            "arguments": parsed_arguments,
                            "result": "用户拒绝执行此操作",
                        },
                    )
                return "用户拒绝执行此操作"

        if self.execution_context is not None:
            self.execution_context.check_cancelled()
            self.execution_context.publish(
                "tool.started",
                {
                    "tool_call_id": tool_call_id,
                    "name": name,
                    "arguments": parsed_arguments,
                },
            )

        tracked_before = self._capture_files(parsed_arguments)
        try:
            result = execute(name, arguments)
            self._permission_denied_count = 0
            if self.execution_context is not None:
                self.execution_context.check_cancelled()
                self.execution_context.publish(
                    "tool.completed",
                    {"tool_call_id": tool_call_id, "result": result},
                )
                self._publish_file_changes(tracked_before)
            return result
        except RunCancelled:
            if self.execution_context is not None:
                self.execution_context.publish(
                    "tool.failed",
                    {"tool_call_id": tool_call_id, "error": "用户取消任务"},
                )
            raise
        except PermissionError as exc:
            self._permission_denied_count += 1
            if self.execution_context is not None:
                self.execution_context.publish(
                    "tool.failed",
                    {"tool_call_id": tool_call_id, "error": str(exc)},
                )
            if self._permission_denied_count >= self.max_permission_denied:
                raise RuntimeError(f"连续 {self.max_permission_denied} 次权限拒绝，任务终止") from exc
            return str(exc)
        except Exception as exc:
            logger.error("工具执行失败: %s, 错误: %s", name, exc, exc_info=True)
            if self.execution_context is not None:
                self.execution_context.publish(
                    "tool.failed",
                    {"tool_call_id": tool_call_id, "error": str(exc)},
                )
            return f"工具执行失败: {str(exc)}"

    @staticmethod
    def _parse_arguments(arguments: str) -> dict:
        """将工具参数转为事件可直接使用的字典。"""

        try:
            value = json.loads(arguments)
            return value if isinstance(value, dict) else {"value": value}
        except (TypeError, json.JSONDecodeError):
            return {"raw": arguments}

    def _request_approval(
        self,
        *,
        name: str,
        arguments: dict,
        tool_call_id: str | None,
    ) -> bool:
        """根据运行模式从 Web 或终端取得一次授权。"""

        if self.execution_context is not None:
            provider = self.execution_context.approval_provider
            if provider is None:
                logger.warning("运行上下文未提供授权器，拒绝危险工具：%s", name)
                return False
            return provider.request(
                self.execution_context,
                approval_id=self.execution_context.new_id("approval"),
                tool_call_id=tool_call_id or self.execution_context.new_id("tool"),
                tool_name=name,
                arguments=arguments,
            )

        if self.console:
            self.console.print("[bold red]⚠ 需要确认[/bold red] 是否允许执行？(y/n) ", end="")
        return input().strip().lower() == "y"

    def _capture_files(self, arguments: dict) -> dict[str, str | None]:
        """记录显式文件参数的执行前指纹。"""

        candidates = []
        for key in ("path", "file_path", "target_path", "destination"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                candidates.append(value)
        return {value: self._fingerprint(value) for value in candidates}

    def _resolve_path(self, path_value: str) -> Path:
        """按当前会话工作区规范化文件路径。"""

        path = Path(path_value).expanduser()
        if not path.is_absolute() and self.execution_context is not None:
            workspace_path = getattr(self.execution_context, "workspace_path", "")
            if workspace_path:
                path = Path(workspace_path) / path
        return path.resolve()

    def _fingerprint(self, path_value: str) -> str | None:
        """返回小成本文件指纹；不存在或不是普通文件时返回 None。"""

        try:
            path = self._resolve_path(path_value)
            if not path.is_file():
                return None
            digest = hashlib.sha256()
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(65536), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return None

    def _publish_file_changes(self, before: dict[str, str | None]) -> None:
        """比较执行前后指纹并发布文件变化事件。"""

        if self.execution_context is None:
            return
        for path_value, previous in before.items():
            current = self._fingerprint(path_value)
            if previous == current:
                continue
            if previous is None:
                change_type = "created"
            elif current is None:
                change_type = "deleted"
            else:
                change_type = "modified"
            self.execution_context.publish(
                "file.changed",
                {
                    "path": str(self._resolve_path(path_value)),
                    "change_type": change_type,
                },
            )
