"""
/skill 命令处理模块
"""
from rich.console import Console
from rich.table import Table
from qrclaw.skills.registry import SkillRegistry


def handle(args: str, console: Console, workspace) -> None:
    """处理 /skill 子命令。"""
    parts = args.strip().split(maxsplit=1)

    if not parts or not parts[0]:
        _print_help(console)
        return

    subcommand = parts[0]
    sub_args = parts[1].strip() if len(parts) > 1 else ""

    if subcommand == "list":
        _cmd_list(console, workspace)
    elif subcommand == "import":
        _cmd_import(sub_args, console)
    else:
        console.print(f"[red]未知子命令: {subcommand}[/red]")
        _print_help(console)


def _cmd_list(console: Console, workspace) -> None:
    registry = SkillRegistry()
    registry.load_from_dir(workspace.skills_dir)

    if not registry.skills:
        console.print("[yellow]没有安装任何技能[/yellow]")
        console.print("[dim]在对话中说：导入 skill <skill_name>[/dim]")
        return

    table = Table(title="已安装的技能")
    table.add_column("名称", style="cyan")
    table.add_column("描述", style="green")
    table.add_column("版本", style="yellow")

    for name, skill in registry.skills.items():
        desc = skill.description
        table.add_row(
            name,
            desc[:50] + "..." if len(desc) > 50 else desc,
            skill.version,
        )

    console.print(table)


def _cmd_import(skill_identifier: str, console: Console) -> None:
    if not skill_identifier:
        console.print("[red]用法: /skill import <skill_name_or_url>[/red]")
        console.print("[dim]示例:[/dim]")
        console.print("[dim]  /skill import agent-self-reflection[/dim]")
        console.print("[dim]  /skill import https://github.com/user/skill[/dim]")
        console.print()
        console.print("[dim]或者在对话中说：请导入 skill agent-self-reflection[/dim]")
        return

    console.print(f"[cyan]请在对话中说：导入 skill {skill_identifier}[/cyan]")
    console.print("[dim]LLM 会使用 install-skill skill 帮你下载[/dim]")


def _print_help(console: Console) -> None:
    console.print("[dim]用法: /skill <list|import> [参数][/dim]")
    console.print("[dim]  list              列出所有技能[/dim]")
    console.print("[dim]  import <name>     导入技能[/dim]")
