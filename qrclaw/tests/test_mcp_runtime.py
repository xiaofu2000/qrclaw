"""MCP stdio transport must stay live through a tool call and close cleanly."""

import sys

import qrclaw.config as config
import qrclaw.tools  # noqa: F401
from qrclaw.agent import _cleanup_mcp, _init_mcp
from qrclaw.tools.registry import _tools, execute


def test_stdio_mcp_tool_lifecycle(temp_dir, monkeypatch):
    server = temp_dir / "echo_server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "mcp = FastMCP('test')\n"
        "@mcp.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return 'echo:' + text\n"
        "mcp.run()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "MCP_ENABLED", True)
    monkeypatch.setattr(config, "MCP_SERVERS", [{
        "transport": "stdio", "command": sys.executable, "args": [str(server)],
    }])

    assert _init_mcp()
    try:
        assert execute("mcp__echo", '{"text":"hello"}') == "echo:hello"
    finally:
        _cleanup_mcp()
    assert "mcp__echo" not in _tools
