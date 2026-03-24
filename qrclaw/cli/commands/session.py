"""
/session 命令处理模块
"""
from rich.console import Console
from rich.table import Table
from qrclaw.memory.session import Session, list_sessions, delete_session
from qrclaw.workspace import Workspace
from qrclaw.logger import setup_logger
from qrclaw.config import LOG_LEVEL, LOG_MAX_DAYS, LOG_TO_FILE, LOG_TO_CONSOLE, LOG_CONSOLE_LEVEL


def handle(args: str, current_session: Session, console: Console, workspace: Workspace) -> Session:
    """
    处理 /session 子命令，返回（可能切换后的）Session。

    子命令：
      list              列出当前 agent 的所有会话
      new [id]          新建并切换（不传 id 则自动生成）
      switch <id>       切换到已有会话
      delete <id>       删除指定会话
    """
    parts = args.strip().split(maxsplit=1)

    if not parts or not parts[0]:
        _print_help(console)
        return current_session

    subcommand = parts[0]
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "list":
        return _cmd_list(current_session, console, workspace)
    elif subcommand == "new":
        return _cmd_new(sub_args, current_session, console, workspace)
    elif subcommand == "switch":
        return _cmd_switch(sub_args, current_session, console, workspace)
    elif subcommand == "delete":
        return _cmd_delete(sub_args, current_session, console, workspace)
    else:
        console.print(f"[red]未知子命令: {subcommand}[/red]")
        _print_help(console)
        return current_session


def _cmd_list(current_session: Session, console: Console, workspace: Workspace) -> Session:
    sessions = list_sessions(workspace.sessions_dir)
    if not sessions:
        console.print("[yellow]没有已保存的会话[/yellow]")
        return current_session

    table = Table(title=f"会话列表 (agent: {workspace.agent_id})")
    table.add_column("会话 ID", style="cyan")
    table.add_column("消息数", style="green", justify="right")
    table.add_column("最后更新", style="dim")
    table.add_column("", style="bold yellow")

    for s in sessions:
        marker = "← 当前" if s["id"] == current_session.session_id else ""
        table.add_row(s["id"], str(s["message_count"]), s["updated_at"], marker)

    console.print(table)
    return current_session


def _cmd_new(session_id: str, current_session: Session, console: Console, workspace: Workspace) -> Session:
    _reinit_logger(session_id or None, workspace)
    # 关键修复：必须传入 resume=False，否则会恢复最近的会话
    new_session = Session(sessions_dir=workspace.sessions_dir, session_id=session_id or None, resume=False)
    console.print(f"[bold cyan]已新建并切换到会话: {new_session.session_id}[/bold cyan]")
    return new_session


def _cmd_switch(session_id: str, current_session: Session, console: Console, workspace: Workspace) -> Session:
    if not session_id:
        console.print("[red]用法: /session switch <会话ID>[/red]")
        return current_session

    _reinit_logger(session_id, workspace)
    target = Session(sessions_dir=workspace.sessions_dir, session_id=session_id)
    msg = f"已切换到会话: {session_id}"
    if target.messages:
        msg += f"（{len(target.messages)} 条历史消息）"
    console.print(f"[bold cyan]{msg}[/bold cyan]")
    return target


def _cmd_delete(session_id: str, current_session: Session, console: Console, workspace: Workspace) -> Session:
    if not session_id:
        console.print("[red]用法: /session delete <会话ID>[/red]")
        return current_session

    if session_id == current_session.session_id:
        console.print("[red]不能删除当前正在使用的会话，请先切换到其他会话[/red]")
        return current_session

    if delete_session(session_id, workspace.sessions_dir):
        console.print(f"[dim]会话 {session_id} 已删除[/dim]")
    else:
        console.print(f"[yellow]会话 {session_id} 不存在[/yellow]")

    return current_session


def _reinit_logger(session_id: str | None, workspace: Workspace) -> None:
    setup_logger(
        session_id=session_id or "tmp",
        log_level=LOG_LEVEL,
        log_to_file=LOG_TO_FILE,
        log_to_console=LOG_TO_CONSOLE,
        log_max_days=LOG_MAX_DAYS,
        console_level=LOG_CONSOLE_LEVEL,
        log_dir=workspace.logs_dir,
    )


def _print_help(console: Console) -> None:
    console.print("[dim]用法: /session <list|new|switch|delete> [会话ID][/dim]")
    console.print("[dim]  list              列出所有会话[/dim]")
    console.print("[dim]  new [id]          新建会话（不传 id 则自动生成）[/dim]")
    console.print("[dim]  switch <id>       切换会话[/dim]")
    console.print("[dim]  delete <id>       删除会话[/dim]")