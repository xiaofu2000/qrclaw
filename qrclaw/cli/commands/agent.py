"""
/agent 命令处理模块
"""
import shutil
from rich.console import Console
from rich.table import Table
from qrclaw.workspace import Workspace, list_agents, AGENTS_ROOT
from qrclaw.memory.context.session import Session
from qrclaw.logger import setup_logger
from qrclaw.config import LOG_LEVEL, LOG_MAX_DAYS, LOG_TO_FILE, LOG_TO_CONSOLE, LOG_CONSOLE_LEVEL
from qrclaw.tools.agent_tools import create_agent as _create_agent, delete_agent as _delete_agent


def handle(args: str, current_workspace: Workspace, current_session: Session, console: Console) -> tuple[Workspace, Session]:
    """
    处理 /agent 子命令。
    返回 (workspace, session)，切换 agent 时两者都会更新。

    子命令：
      list              列出所有顶级 agent
      new <id>          新建并切换
      switch <id>       切换到已有 agent
      delete <id>       删除指定 agent（不能删当前）
    """
    parts = args.strip().split(maxsplit=1)

    if not parts or not parts[0]:
        _print_help(console)
        return current_workspace, current_session

    subcommand = parts[0]
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "list":
        return _cmd_list(current_workspace, console)
    elif subcommand == "new":
        return _cmd_new(sub_args, current_workspace, current_session, console)
    elif subcommand == "switch":
        return _cmd_switch(sub_args, current_workspace, current_session, console)
    elif subcommand == "delete":
        return _cmd_delete(sub_args, current_workspace, current_session, console)
    else:
        console.print(f"[red]未知子命令: {subcommand}[/red]")
        _print_help(console)
        return current_workspace, current_session


def _cmd_list(current_workspace: Workspace, console: Console) -> tuple[Workspace, Session]:
    agents = list_agents()
    if not agents:
        console.print("[yellow]没有已创建的 agent[/yellow]")
        return current_workspace, None

    table = Table(title="Agent 列表")
    table.add_column("Agent ID", style="cyan")
    table.add_column("路径", style="dim")
    table.add_column("", style="bold yellow")

    for agent_id in agents:
        marker = "← 当前" if agent_id == current_workspace.agent_id else ""
        table.add_row(agent_id, str(AGENTS_ROOT / agent_id), marker)

    console.print(table)
    return current_workspace, None


def _cmd_new(agent_id: str, current_workspace: Workspace, current_session: Session, console: Console) -> tuple[Workspace, Session]:
    if not agent_id:
        console.print("[red]用法: /agent new <agentID>[/red]")
        return current_workspace, current_session

    # 调用统一的创建函数（会自动写入 permissions.yaml）
    result = _create_agent(agent_id)
    
    if result.startswith("错误") or result.startswith("❌"):
        console.print(f"[red]{result}[/red]")
        return current_workspace, current_session
    
    console.print(f"[green]{result}[/green]")
    
    new_workspace = Workspace(agent_id=agent_id)
    new_session = _switch_to(new_workspace, console)
    console.print(f"[bold cyan]已切换到 agent: {agent_id}[/bold cyan]")
    return new_workspace, new_session


def _cmd_switch(agent_id: str, current_workspace: Workspace, current_session: Session, console: Console) -> tuple[Workspace, Session]:
    if not agent_id:
        console.print("[red]用法: /agent switch <agentID>[/red]")
        return current_workspace, current_session

    if agent_id == current_workspace.agent_id:
        console.print(f"[yellow]已经在 agent: {agent_id}[/yellow]")
        return current_workspace, current_session

    target_workspace = Workspace(agent_id=agent_id)
    new_session = _switch_to(target_workspace, console)
    console.print(f"[bold cyan]已切换到 agent: {agent_id}[/bold cyan]")
    return target_workspace, new_session


def _cmd_delete(agent_id: str, current_workspace: Workspace, current_session: Session, console: Console) -> tuple[Workspace, Session]:
    if not agent_id:
        console.print("[red]用法: /agent delete <agentID>[/red]")
        return current_workspace, current_session

    if agent_id == current_workspace.agent_id:
        console.print("[red]不能删除当前正在使用的 agent，请先切换到其他 agent[/red]")
        return current_workspace, current_session

    # 调用统一的删除函数（会清理 permissions.yaml）
    result = _delete_agent(agent_id)
    
    if result.startswith("错误") or result.startswith("❌"):
        console.print(f"[red]{result}[/red]")
    else:
        console.print(f"[green]{result}[/green]")
    
    return current_workspace, current_session


def _switch_to(workspace: Workspace, console: Console) -> Session:
    """切换到指定 workspace，重建日志和 session。"""
    setup_logger(
        session_id="init",
        log_level=LOG_LEVEL,
        log_to_file=LOG_TO_FILE,
        log_to_console=LOG_CONSOLE_LEVEL,
        log_max_days=LOG_MAX_DAYS,
        console_level=LOG_CONSOLE_LEVEL,
        log_dir=workspace.logs_dir,
    )
    new_session = Session(sessions_dir=workspace.sessions_dir)
    # 用真实 session_id 重建日志
    setup_logger(
        session_id=new_session.session_id,
        log_level=LOG_LEVEL,
        log_to_file=LOG_TO_FILE,
        log_to_console=LOG_CONSOLE_LEVEL,
        log_max_days=LOG_MAX_DAYS,
        log_dir=workspace.logs_dir,
    )
    if new_session.messages:
        console.print(f"[dim]已加载历史会话，共 {len(new_session.messages)} 条消息[/dim]")
    return new_session


def _print_help(console: Console) -> None:
    console.print("[dim]用法: /agent <list|new|switch|delete> [agentID][/dim]")
    console.print("[dim]  list              列出所有 agent[/dim]")
    console.print("[dim]  new <id>          新建 agent[/dim]")
    console.print("[dim]  switch <id>       切换 agent[/dim]")
    console.print("[dim]  delete <id>       删除 agent[/dim]")
