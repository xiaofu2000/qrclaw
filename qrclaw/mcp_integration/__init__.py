"""MCP (Model Context Protocol) integration module for QRClaw."""

from qrclaw.mcp_integration.client import MCPManager
from qrclaw.mcp_integration.bridge import register_mcp_tools

__all__ = ["MCPManager", "register_mcp_tools"]
