"""
主程序入口模块

负责参数解析、初始化和主循环。

Import 顺序说明：
  触发大量子模块 import 的代码（tools、agent 等）必须在 setup_logger 之后延迟导入，
  否则各模块顶层的 get_logger() 会在日志系统初始化前执行，导致日志写入错误的文件。
"""
import argparse
from rich.console import Console
from rich.panel import Panel
from qrclaw.logger import setup_logger
from qrclaw.workspace import Workspace
from qrclaw.config import LOG_LEVEL, LOG_MAX_DAYS, LOG_TO_FILE, LOG_TO_CONSOLE, LOG_CONSOLE_LEVEL
from qrclaw.cli.input import get_input
from qrclaw.cli.display import show_context_usage, show_plan_progress
from qrclaw.cli.commands import session as session_cmd
from qrclaw.cli.commands import skill as skill_cmd

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(prog="qrclaw", add_help=False)
    parser.add_argument("-a", "--agent", default="default", metavar="ID",
                        help="指定 agent ID（默认: default）")
    parser.add_argument("-h", "--help", action="store_true")
    args, _ = parser.parse_known_args()

    if args.help:
        _print_help()
        return

    # 1. 初始化工作空间（确定所有路径）
    workspace = Workspace(agent_id=args.agent)

    # 2. 延迟导入各子模块（在 setup_logger 之前不能触发 get_logger）
    import qrclaw.tools.builtin  # noqa: E402  触发工具注册
    import qrclaw.tools.skills   # noqa: E402  触发 Skills 工具注册
    from qrclaw.agent import run
    from qrclaw.memory.session import Session

    # 3. 创建 Session（路径由 workspace 决定）
    session = Session(sessions_dir=workspace.sessions_dir)

    # 4. 初始化日志（路径由 workspace 决定）
    setup_logger(
        session_id=session.session_id,
        log_level=LOG_LEVEL,
        log_to_file=LOG_TO_FILE,
        log_to_console=LOG_TO_CONSOLE,
        log_max_days=LOG_MAX_DAYS,
        console_level=LOG_CONSOLE_LEVEL,
        log_dir=workspace.logs_dir,
    )

    console.print(
        f"[bold cyan]QRClaw Agent[/bold cyan] 启动  "
        f"[dim]agent: [/dim][bold cyan]{workspace.agent_id}[/bold cyan]  "
        f"[dim]会话: [/dim][bold cyan]{session.session_id}[/bold cyan]\n"
        "[dim]Enter 发送 · Alt+Enter 换行 · exit 退出 · clear 清除 · "
        "/session 管理会话 · /skill 管理技能[/dim]\n"
    )

    if session.messages:
        console.print(f"[dim]已加载历史会话，共 {len(session.messages)} 条消息[/dim]\n")

    while True:
        try:
            show_plan_progress(console, session)
            show_context_usage(console, session)
            user_input = get_input(session.session_id)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见！[/dim]")
            break

        user_input = user_input.strip()

        if not user_input:
            continue

        if user_input.startswith("/session"):
            sub = user_input[8:].strip()
            session = session_cmd.handle(sub, session, console, workspace)
            continue

        if user_input.startswith("/skill"):
            sub = user_input[6:].strip()
            skill_cmd.handle(sub, console, workspace)
            continue

        if user_input == "exit":
            console.print("[dim]再见！[/dim]")
            break

        if user_input == "clear":
            session.clear()
            console.print("[dim]会话已清除[/dim]\n")
            continue

        # 清除 prompt_toolkit 留下的输入行，再打印带框版本
        lines = user_input.count("\n") + 1
        print(f"\033[{lines}A\033[J", end="")

        console.print(Panel(
            user_input,
            title="[bold magenta]你[/bold magenta]",
            border_style="magenta",
            expand=False,
            width=min(len(user_input) + 6, console.width),
        ))

        try:
            run(user_input, session, console, workspace)
        except Exception:
            console.print_exception()


def _print_help() -> None:
    console.print("[bold cyan]QRClaw Agent[/bold cyan]")
    console.print()
    console.print("用法: qrclaw [-a <agentID>]")
    console.print()
    console.print("选项:")
    console.print("  -a, --agent <ID>     指定 agent ID（默认: default）")
    console.print()
    console.print("运行时命令:")
    console.print("  /session list              列出所有会话")
    console.print("  /session new [id]          新建会话")
    console.print("  /session switch <id>       切换会话")
    console.print("  /session delete <id>       删除会话")
    console.print("  /skill list                列出技能")
    console.print("  /skill import <name>       导入技能")
    console.print("  clear                      清除当前会话")
    console.print("  exit                       退出")
