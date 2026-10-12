"""CLI entrypoint for the local qrclaw server."""
from __future__ import annotations

import argparse
import ipaddress
import os


def serve(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="qrclaw serve")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只允许本机访问")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args(argv)

    try:
        local = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        local = args.host == "localhost"
    if not local and not os.getenv("QRCLAW_API_TOKEN"):
        parser.error("非本机监听需先设置 QRCLAW_API_TOKEN")

    import uvicorn

    uvicorn.run(
        "qrclaw.server.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )

