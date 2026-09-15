"""MCP Client Manager — connects to MCP servers, discovers tools, and routes tool calls.

Supports both stdio and SSE transports via the official ``mcp`` Python SDK.

Usage::

    manager = MCPManager()
    await manager.connect_all([
        {"transport": "stdio", "command": "python", "args": ["-m", "my_mcp_server"]},
        {"transport": "sse", "url": "http://localhost:8080/sse"},
    ])

    schemas = manager.get_tool_schemas()          # OpenAI Function Calling format
    result = await manager.call_tool("my_tool", {"key": "value"})
    await manager.disconnect_all()
"""

from __future__ import annotations

import logging
import shutil
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client

logger = logging.getLogger(__name__)


@dataclass
class _MCPTool:
    """Internal representation of a discovered MCP tool."""

    name: str
    description: str
    input_schema: dict[str, Any]
    session: ClientSession
    server_label: str


@dataclass
class _MCPConnection:
    """Holds resources for a single MCP server connection."""

    label: str
    exit_stack: AsyncExitStack
    session: ClientSession
    tools: list[_MCPTool] = field(default_factory=list)


class MCPManager:
    """Manages connections to multiple MCP servers and provides unified tool access.

    Attributes:
        _servers_config: Raw server configuration list.
        _connections: Active connections keyed by server label.
        _tools: Discovered tools keyed by tool name.
    """

    def __init__(self) -> None:
        self._servers_config: list[dict[str, Any]] = []
        self._connections: dict[str, _MCPConnection] = {}
        self._tools: dict[str, _MCPTool] = {}

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
        """
        self._servers_config = servers_config

        for idx, cfg in enumerate(servers_config):
            label = cfg.get("label", f"server-{idx}")
            transport = cfg.get("transport", "stdio").lower()

            try:
                if transport == "stdio":
                    conn = await self._connect_stdio(label, cfg)
                elif transport == "sse":
                    conn = await self._connect_sse(label, cfg)
                else:
                    logger.warning("Unknown MCP transport '%s' for %s, skipping", transport, label)
                    continue

                self._connections[label] = conn

                # Discover tools
                result = await conn.session.list_tools()
                for tool in result.tools:
                    mcp_tool = _MCPTool(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=tool.inputSchema or {"type": "object", "properties": {}},
                        session=conn.session,
                        server_label=label,
                    )
                    conn.tools.append(mcp_tool)
                    self._tools[tool.name] = mcp_tool
                    logger.info("Discovered MCP tool: %s (from %s)", tool.name, label)

            except Exception:
                logger.exception("Failed to connect to MCP server '%s'", label)

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
        await session.initialize()
        logger.info("MCP stdio connection established: %s", label)
        return _MCPConnection(label=label, exit_stack=exit_stack, session=session)

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
        await session.initialize()
        logger.info("MCP SSE connection established: %s (%s)", label, url)
        return _MCPConnection(label=label, exit_stack=exit_stack, session=session)

    # ------------------------------------------------------------------
    # Tool discovery
    # ------------------------------------------------------------------

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Return all discovered MCP tool schemas in OpenAI Function Calling format.

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
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            schema = self._mcp_schema_to_openai(tool)
            schemas.append(schema)
        return schemas

    @staticmethod
    def _mcp_schema_to_openai(tool: _MCPTool) -> dict[str, Any]:
        """Convert an MCP tool's inputSchema to OpenAI Function Calling format."""
        input_schema = dict(tool.input_schema)
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
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"MCP tool '{name}' not found. Available: {list(self._tools.keys())}")

        logger.debug("Calling MCP tool '%s' with args: %s", name, arguments)
        result = await tool.session.call_tool(name, arguments)

        # MCP CallToolResult has a .content list of TextContent / ImageContent / …
        text_parts: list[str] = []
        for block in result.content:
            # TextContent has a .text attribute
            if hasattr(block, "text"):
                text_parts.append(block.text)
            else:
                text_parts.append(str(block))

        response_text = "\n".join(text_parts)
        logger.debug("MCP tool '%s' returned %d chars", name, len(response_text))
        return response_text

    # ------------------------------------------------------------------
    # Disconnection
    # ------------------------------------------------------------------

    async def disconnect_all(self) -> None:
        """Close all MCP server connections and clear internal state."""
        for label, conn in list(self._connections.items()):
            try:
                await conn.exit_stack.aclose()
                logger.info("Disconnected MCP server: %s", label)
            except Exception:
                logger.exception("Error disconnecting MCP server '%s'", label)

        self._connections.clear()
        self._tools.clear()

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
        """Check whether a tool named *name* is available."""
        return name in self._tools
