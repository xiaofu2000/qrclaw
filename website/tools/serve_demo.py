"""启动只使用公开样例材料的 QRClaw 官网演示服务。"""

from __future__ import annotations

import argparse
from functools import wraps
import os
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[2]
DEMO_ROOT = REPOSITORY / "website/.local/demo-workspace"
ALLOWED_TOOLS = {
    "read_file", "write_file", "list_directory",
    "write_wiki_page", "read_wiki_page", "list_wiki_pages", "search_wiki",
}
NOTES = {
    "01-产品定位.md": """# 星河项目笔记：定位
这是官网演示专用的虚构项目，不对应真实客户或用户。
星河是一款帮助独立创作者整理读书笔记的本地工具。
首版聚焦：导入 Markdown、按主题整理、导出周报。
暂不加入账号系统、云同步和团队协作。
""",
    "02-交付进度.md": """# 星河项目笔记：交付
Markdown 导入已经完成，样例目录内的三篇文档可以正确读取。
主题标签功能已完成界面，自动归类规则仍需验证。
周报导出尚未开始，先采用 Markdown 格式。
下一步：验证分类规则，接着完成周报导出。
""",
    "03-待确认事项.md": """# 星河项目笔记：待确认
首版以本地文件为输入，不接入第三方账号。
分类准确率还未测量，不能写成已经达标。
发布日尚未确定，需等导出与异常处理验收完成。
样例截图只能使用虚构笔记，不展示私人资料。
""",
}


def prepare_workspace() -> None:
    """只补齐缺失的公开样例，不覆盖已有会话、记忆和任务结果。"""
    notes_dir = DEMO_ROOT / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    for name, content in NOTES.items():
        path = notes_dir / name
        if not path.exists():
            path.write_text(content, encoding="utf-8")
    os.chdir(DEMO_ROOT)
    sys.path.insert(0, str(REPOSITORY))


def bounded_path(path: str) -> Path:
    """解析真实路径，拒绝越界访问及指向演示目录之外的符号链接。"""
    requested = Path(path).expanduser()
    resolved = (requested if requested.is_absolute() else DEMO_ROOT / requested).resolve()
    if not resolved.is_relative_to(DEMO_ROOT.resolve()):
        raise PermissionError("演示工具只能访问官网演示工作空间")
    return resolved


def guard_file_tool(function):
    """保留产品原有文件工具行为，在调用前增加演示目录限制。"""
    @wraps(function)
    def checked_tool(*, path: str, **kwargs):
        """只把校验通过的绝对路径交给原工具。"""
        return function(path=str(bounded_path(path)), **kwargs)
    return checked_tool


def build_demo_app():
    """使用真实 Agent 与模型配置，关闭本进程的外部 MCP 和多余工具。"""
    prepare_workspace()
    from qrclaw import config
    config.MCP_ENABLED = False
    config.MCP_SERVERS = []
    from qrclaw.tools import registry
    for name in tuple(registry._tools):
        if name not in ALLOWED_TOOLS:
            del registry._tools[name]
    for name in ("read_file", "write_file", "list_directory"):
        registry._tools[name]["fn"] = guard_file_tool(registry._tools[name]["fn"])
    from qrclaw.server.app import create_app
    from qrclaw.workspace import Workspace
    workspace = Workspace(
        agent_id="website-demo",
        _root=DEMO_ROOT,
        _sandbox_policy_agent_id="default",
    )
    return create_app(workspace)


def self_check() -> None:
    """验证路径边界和工具白名单，不调用模型、不触碰私人文件。"""
    from qrclaw import config
    from qrclaw.tools import registry
    assert not config.MCP_ENABLED and not config.MCP_SERVERS
    assert set(registry._tools) == ALLOWED_TOOLS
    assert bounded_path("notes/01-产品定位.md") == DEMO_ROOT / "notes/01-产品定位.md"
    for path in ("../outside.md", str(REPOSITORY / "README.md")):
        try:
            bounded_path(path)
        except PermissionError:
            pass
        else:
            raise AssertionError("越界路径未被拒绝")
    link = DEMO_ROOT / ".boundary-check-link"
    try:
        link.symlink_to(DEMO_ROOT.parent, target_is_directory=True)
        try:
            bounded_path(str(link / "outside.md"))
        except PermissionError:
            pass
        else:
            raise AssertionError("越界符号链接未被拒绝")
    finally:
        link.unlink(missing_ok=True)
    print(f"演示服务自检通过：MCP 关闭、{len(ALLOWED_TOOLS)} 个工具、目录与符号链接边界有效。")


def main() -> None:
    """解析启动参数；服务固定监听本机，避免演示数据暴露到网络。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8876, help="演示服务本机端口")
    parser.add_argument("--self-check", action="store_true", help="仅执行隔离自检")
    args = parser.parse_args()
    app = build_demo_app()
    if args.self_check:
        self_check()
        return
    import uvicorn
    (DEMO_ROOT.parent / "demo-api.pid").write_text(str(os.getpid()), encoding="utf-8")
    print(f"官网演示服务已准备：127.0.0.1:{args.port}，数据位于 {DEMO_ROOT}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
