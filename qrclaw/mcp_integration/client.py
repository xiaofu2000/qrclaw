"""MCP Client Manager — connects to MCP servers, discovers tools, and routes tool calls.

Supports stdio/SSE connections and FastMCP on-demand tool discovery.

Usage::

    manager = MCPManager()
    await manager.connect_all([
        {"transport": "stdio", "command": "python", "args": ["-m", "my_mcp_server"]},
        {"transport": "sse", "url": "http://localhost:8080/sse"},
    ])

    schemas = manager.get_tool_schemas()  # Only search_tools and call_tool
    result = await manager.call_tool("search_tools", {"query": "my_tool"})
    result = await manager.call_tool("call_tool", {
        "name": "server-0__my_tool", "arguments": {"key": "value"},
    })
    await manager.disconnect_all()
"""

from __future__ import annotations

import json
import logging
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.transforms.search import BM25SearchTransform
from fastmcp.tools import Tool, ToolResult
from jsonschema import validate
from jsonschema.exceptions import ValidationError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from pydantic import PrivateAttr

logger = logging.getLogger(__name__)


@dataclass
class _MCPTool:
    """Internal representation of a discovered MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    session: ClientSession
    server_label: str

    @property
    def qualified_name(self) -> str:
        """给工具加服务前缀，防止不同 MCP 服务的同名工具串用。"""
        return f"{self.server_label}__{self.name}"


class _MCPProxyTool(Tool):
    """把已连接的 MCP 工具交给 FastMCP，复用原有会话。"""

    _remote_tool: _MCPTool = PrivateAttr()

    def __init__(self, remote_tool: _MCPTool) -> None:
        """保留原始 schema 供搜索展示和完整参数校验。"""
        super().__init__(
            name=remote_tool.qualified_name,
            description=remote_tool.description,
            parameters=remote_tool.input_schema,
        )
        self._remote_tool = remote_tool

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """校验参数后调用原始工具，保留服务端返回的错误状态。"""
        try:
            validate(arguments, self.parameters)
        except ValidationError as exc:
            # 不把完整 schema 随参数错误再次送入模型上下文。
            raise ValueError(f"MCP 工具参数无效：{exc.message}") from exc
        tool = self._remote_tool
        result = await tool.session.call_tool(tool.name, arguments)
        return ToolResult(
            content=result.content,
            structured_content=result.structuredContent,
            meta=result.meta,
            is_error=result.isError,
        )


@dataclass
class _MCPConnection:
    """Holds resources for a single MCP server connection."""

    label: str
    exit_stack: AsyncExitStack
    session: ClientSession
    tools: list[_MCPTool] = field(default_factory=list)
    instructions: str = ""


class MCPManager:
    """Manages connections to multiple MCP servers and provides unified tool access.

    Attributes:
        _servers_config: Raw server configuration list.
        _connections: Active connections keyed by server label.
        _tools: Discovered tools keyed by server-qualified name.
    """

    def __init__(self) -> None:
        self._servers_config: list[dict[str, Any]] = []
        self._connections: dict[str, _MCPConnection] = {}
        self._tools: dict[str, _MCPTool] = {}
        self._search_server = FastMCP("QRClaw MCP")
        self._tool_schemas: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect_all(self, servers_config: list[dict[str, Any]]) -> None:
        """Connect to every MCP server described in *servers_config*.

        Each entry must have a ``"transport"`` key (``"stdio"`` or ``"sse"``)
        plus transport-specific parameters:

        * **stdio** — ``"command"``, optional ``"args"``, ``"env"``
        * **sse** — ``"url"``, optional ``"headers"``

        Optionally a ``"label"`` key provides a human-readable name (defaults
        to ``"server-{index}"``).

        Tool filtering (optional, applied after ``list_tools``):

        * ``"include_tools"`` — allowlist of tool names; when present, only
          these tools can be searched and called.
        * ``"exclude_tools"`` — denylist of tool names; applied after the
          allowlist.

        The catalog stays local; only search/call entry points reach the LLM.
        """
        self._servers_config = servers_config

        for idx, cfg in enumerate(servers_config):
            label = cfg.get("label", f"server-{idx}")
            transport = cfg.get("transport", "stdio").lower()

            try:
                if label in self._connections:
                    raise ValueError(f"MCP 服务标签重复：{label}")
                if transport == "stdio":
                    conn = await self._connect_stdio(label, cfg)
                elif transport == "sse":
                    conn = await self._connect_sse(label, cfg)
                else:
                    logger.warning("未知 MCP 传输方式 '%s'，跳过服务 %s", transport, label)
                    continue

                self._connections[label] = conn
                description = cfg.get("description")
                if isinstance(description, str) and description.strip():
                    conn.instructions = description

                # Discover tools
                include = cfg.get("include_tools")
                include_set = set(include) if include is not None else None
                exclude_set = set(cfg.get("exclude_tools") or [])

                catalog = []
                cursor = None
                while True:
                    result = await conn.session.list_tools(cursor=cursor)
                    catalog.extend(result.tools)
                    cursor = result.nextCursor
                    if not cursor:
                        break
                for tool in catalog:
                    if include_set is not None and tool.name not in include_set:
                        continue
                    if tool.name in exclude_set:
                        continue
                    mcp_tool = _MCPTool(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                        session=conn.session,
                        server_label=label,
                    )
                    if mcp_tool.qualified_name in self._tools:
                        raise ValueError(f"MCP 工具名称重复：{mcp_tool.qualified_name}")
                    conn.tools.append(mcp_tool)
                    self._tools[mcp_tool.qualified_name] = mcp_tool
                    self._search_server.add_tool(_MCPProxyTool(mcp_tool))
                    logger.debug("发现 MCP 工具：%s", mcp_tool.qualified_name)

                if include_set is not None or exclude_set:
                    logger.info(
                        "MCP 服务 '%s'：允许 %d/%d 个工具，过滤 %d 个",
                        label, len(conn.tools), len(catalog),
                        len(catalog) - len(conn.tools),
                    )

            except Exception:
                logger.exception("连接 MCP 服务 '%s' 失败", label)

        if self._tools:
            self._search_server.add_transform(BM25SearchTransform(max_results=5))
            for tool in await self._search_server.list_tools():
                if tool.name == "search_tools":
                    tool.description = (
                        "按相关性搜索整个允许的 MCP 工具目录，最多返回 5 个工具及完整参数定义。"
                        "任务匹配系统提示中的 MCP 能力时，主动搜索本地或远程专用工具，"
                        "不需要用户点名服务。query 使用工具名称或英文用途关键词，例如 inspect plist。"
                        "中文任务先转成目录中的英文词，不使用正则；搜到后用 mcp__call_tool 调用。"
                        f"已连接服务：{', '.join(label for label, conn in self._connections.items() if conn.tools)}。"
                    )
                else:
                    tool.description = (
                        "调用搜索发现的 MCP 工具；name 必须使用搜索返回的完整名称，"
                        "arguments 按返回的 inputSchema 填写，无参数时可省略。"
                    )
                self._tool_schemas.append(self._mcp_schema_to_openai(tool))
            logger.info("MCP 按需发现已启用：%d 个工具，仅注入 2 个入口", len(self._tools))

    async def _connect_stdio(self, label: str, cfg: dict[str, Any]) -> _MCPConnection:
        """Open a stdio transport connection."""
        command = cfg.get("command", "")
        # Resolve command to full path (e.g. "uvx" → "/path/to/uvx")
        resolved = shutil.which(command) or command

        server_params = StdioServerParameters(
            command=resolved,
            args=cfg.get("args", []),
            env=cfg.get("env"),
        )

        exit_stack = AsyncExitStack()
        read_stream, write_stream = await exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        initialized = await session.initialize()
        logger.info("MCP stdio 连接已建立：%s", label)
        return _MCPConnection(label=label, exit_stack=exit_stack, session=session,
                              instructions=initialized.instructions or "")

    async def _connect_sse(self, label: str, cfg: dict[str, Any]) -> _MCPConnection:
        """Open an SSE transport connection."""
        url = cfg.get("url", "")
        headers = cfg.get("headers")

        exit_stack = AsyncExitStack()
        if headers:
            read_stream, write_stream = await exit_stack.enter_async_context(
                sse_client(url=url, headers=headers)
            )
        else:
            read_stream, write_stream = await exit_stack.enter_async_context(
                sse_client(url=url)
            )
        session = await exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        initialized = await session.initialize()
        logger.info("MCP SSE 连接已建立：%s", label)
        return _MCPConnection(label=label, exit_stack=exit_stack, session=session,
                              instructions=initialized.instructions or "")

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------

    def get_capability_summary(self) -> str:
        """列出所有允许工具的名称和短用途，完整描述与参数仍按需搜索。"""
        lines = []
        for conn in self._connections.values():
            if not conn.tools:
                continue
            summary = " ".join(conn.instructions.split())[:600]
            lines.append(f"- {conn.label}（{len(conn.tools)} 个可检索工具）：{summary}")
            # 不按数量截断，避免目录末尾的能力永远没有机会被模型发现。
            for tool in conn.tools:
                description = " ".join(tool.description.split())[:80]
                lines.append(f"  - {tool.name}：{description}")
        return "\n".join(lines)

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """仅返回搜索和调用入口，完整工具目录不注入模型请求。

        Each entry follows::

            {
                "type": "function",
                "function": {
                    "name": "<tool_name>",
                    "description": "<description>",
                    "parameters": { ... }   # from MCP inputSchema
                }
            }
        """
        return list(self._tool_schemas)

    @staticmethod
    def _mcp_schema_to_openai(tool: Tool) -> dict[str, Any]:
        """把 FastMCP 入口参数转成现有 OpenAI 工具格式。"""
        input_schema = dict(tool.parameters)
        # MCP inputSchema should already follow JSON Schema; ensure required fields
        input_schema.setdefault("type", "object")
        input_schema.setdefault("properties", {})

        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": input_schema,
            },
        }

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call an MCP tool by *name* with the given *arguments*.

        Returns:
            The concatenated text content from the MCP server response.

        Raises:
            KeyError: If no tool named *name* has been discovered.
        """
        if name not in {"search_tools", "call_tool"} and name not in self._tools:
            # 保留旧调用方的无前缀名称；同名工具必须使用搜索返回的完整名称。
            matches = [tool for tool in self._tools.values() if tool.name == name]
            if len(matches) != 1:
                raise KeyError(f"MCP 工具不存在或名称不唯一：{name}，请先搜索工具")
            name = matches[0].qualified_name

        logger.debug("调用 MCP 工具：%s", name)
        result = await self._search_server.call_tool(name, arguments)

        # MCP CallToolResult has a .content list of TextContent / ImageContent / …
        text_parts: list[str] = []
        for block in result.content:
            # TextContent has a .text attribute
            if hasattr(block, "text"):
                text_parts.append(block.text)
            else:
                text_parts.append(str(block))

        response_text = "\n".join(text_parts)
        if not text_parts and result.structured_content is not None:
            # FastMCP 的空搜索列表只返回 structured_content，仍需给模型合法 JSON。
            structured = result.structured_content
            if result.meta and result.meta.get("fastmcp", {}).get("wrap_result"):
                structured = structured["result"]
            response_text = json.dumps(structured, ensure_ascii=False)
        if result.is_error:
            response_text = f"错误：MCP 工具执行失败\n{response_text}"
        logger.debug("MCP 工具 '%s' 返回 %d 个字符", name, len(response_text))
        return response_text

    # ------------------------------------------------------------------
    # Disconnection
    # ------------------------------------------------------------------

    async def disconnect_all(self) -> None:
        """Close all MCP server connections and clear internal state."""
        for label, conn in list(self._connections.items()):
            try:
                await conn.exit_stack.aclose()
                logger.info("MCP 服务已断开：%s", label)
            except Exception:
                logger.exception("断开 MCP 服务 '%s' 时出错", label)

        self._connections.clear()
        self._tools.clear()
        self._tool_schemas.clear()
        self._search_server = FastMCP("QRClaw MCP")

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @property
    def connected_servers(self) -> list[str]:
        """Labels of currently connected servers."""
        return list(self._connections.keys())

    @property
    def tool_names(self) -> list[str]:
        """Names of all discovered MCP tools."""
        return list(self._tools.keys())

    def has_tool(self, name: str) -> bool:
        """检查入口或远端工具是否可调用，兼容唯一的无前缀名称。"""
        if name in {"search_tools", "call_tool"}:
            return bool(self._tool_schemas)
        return name in self._tools or sum(tool.name == name for tool in self._tools.values()) == 1
