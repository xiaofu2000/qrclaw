"""MCP → QRClaw tool registry bridge.

Bridges MCP tools discovered by :class:`MCPManager` into QRClaw's internal
tool registry so they become available in the ReAct loop automatically.

Usage::

    from qrclaw.mcp_integration import MCPManager, register_mcp_tools

    manager = MCPManager()
    await manager.connect_all(servers_config)
    await register_mcp_tools(manager)
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, Optional, Type

from pydantic import BaseModel, Field, create_model

from qrclaw.mcp_integration.client import MCPManager

logger = __import__("qrclaw.logger", fromlist=["get_logger"]).get_logger("qrclaw.mcp_integration.bridge")

# Module-level reference to the MCPManager used by the last register_mcp_tools() call.
_active_manager: MCPManager | None = None


# ── JSON Schema → Python type mapping ──────────────────────────────────────

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _json_type_to_python(json_type: str | None) -> type:
    """Map a JSON Schema primitive type to a Python type."""
    if json_type is None:
        return str
    return _TYPE_MAP.get(json_type, str)


def _build_pydantic_model(
    tool_name: str,
    properties: dict[str, Any],
    required_fields: list[str] | None,
) -> Type[BaseModel]:
    """Dynamically create a Pydantic ``BaseModel`` subclass from MCP tool
    parameter *properties* (JSON Schema ``properties`` object).

    All fields default to ``Optional`` (i.e. ``None``).  Fields listed in
    *required_fields* are made required (no default).
    """
    required_set = set(required_fields or [])
    field_definitions: dict[str, Any] = {}

    for field_name, field_schema in properties.items():
        # field_schema may be a dict like {"type": "string", "description": "..."}
        if not isinstance(field_schema, dict):
            field_schema = {}

        python_type = _json_type_to_python(field_schema.get("type"))
        description = field_schema.get("description", "")

        if field_name in required_set:
            # Required field — no default
            field_definitions[field_name] = (python_type, Field(..., description=description))
        else:
            # Optional field — default None
            field_definitions[field_name] = (Optional[python_type], Field(default=None, description=description))

    model_name = f"MCP_{tool_name}_Args"
    return create_model(model_name, **field_definitions)


def _make_sync_handler(tool_name: str, manager: MCPManager):
    """Create a **synchronous** wrapper that calls ``manager.call_tool``.

    Since QRClaw's ``execute()`` is synchronous but ``call_tool`` is async,
    we use ``asyncio.run()`` when no running loop exists, or spawn a thread
    when inside an already-running loop.
    """

    def handler(**kwargs) -> str:
        # Filter out None values (optional fields not supplied)
        filtered = {k: v for k, v in kwargs.items() if v is not None}
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We are inside a running loop — create a new loop in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, manager.call_tool(tool_name, filtered))
                    return future.result(timeout=300)
            else:
                return loop.run_until_complete(manager.call_tool(tool_name, filtered))
        except RuntimeError:
            # No event loop at all — create one
            return asyncio.run(manager.call_tool(tool_name, filtered))

    return handler


async def register_mcp_tools(manager: MCPManager) -> list[str]:
    """Discover MCP tools via *manager* and inject them into QRClaw's tool registry.

    Each MCP tool is registered under the name ``mcp__<original_name>`` to
    avoid collisions with built-in tools.

    Returns:
        A list of the fully-qualified tool names that were registered.
    """
    import qrclaw.tools.registry as reg

    global _active_manager
    _active_manager = manager

    schemas = manager.get_tool_schemas()
    registered: list[str] = []

    for schema in schemas:
        func_info = schema.get("function", {})
        original_name = func_info.get("name", "")
        if not original_name:
            logger.warning("Skipping MCP tool with empty name")
            continue

        qualified_name = f"mcp__{original_name}"

        # Extract parameters from the OpenAI-format schema
        parameters = func_info.get("parameters", {})
        properties = parameters.get("properties", {})
        required = parameters.get("required")
        description = func_info.get("description", f"MCP tool: {original_name}")

        # 1. Build a dynamic Pydantic model
        dynamic_model = _build_pydantic_model(original_name, properties, required)

        # 2. Create a sync handler that routes to manager.call_tool
        handler = _make_sync_handler(original_name, manager)

        # 3. Rewrite the schema to use the qualified name
        qualified_schema = {
            "type": "function",
            "function": {
                "name": qualified_name,
                "description": description,
                "parameters": parameters,
            },
        }

        # 4. Inject into QRClaw's tool registry
        reg._tools[qualified_name] = {
            "fn": handler,
            "model": dynamic_model,
            "schema": qualified_schema,
            "confirm": False,
            "agents": None,  # visible to all agent types
        }

        registered.append(qualified_name)
        logger.info("Registered MCP tool into QRClaw registry: %s", qualified_name)

    logger.info("MCP bridge: registered %d tools total", len(registered))
    return registered


async def unregister_mcp_tools() -> None:
    """Remove all ``mcp__*`` tools from QRClaw's registry."""
    import qrclaw.tools.registry as reg

    to_remove = [name for name in reg._tools if name.startswith("mcp__")]
    for name in to_remove:
        del reg._tools[name]
    logger.info("Unregistered %d MCP tools", len(to_remove))


def get_active_manager() -> MCPManager | None:
    """Return the :class:`MCPManager` used by the last ``register_mcp_tools`` call."""
    return _active_manager
