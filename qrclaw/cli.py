import sys
import qrclaw.tools.builtin  # 触发工具注册
import qrclaw.tools.skills    # 触发 Skills 工具注册
from qrclaw.agent import run
from qrclaw.memory.session import Session
from qrclaw.logger import setup_logger
from qrclaw.config import LOG_LEVEL, LOG_MAX_DAYS, LOG_TO_FILE, LOG_TO_CONSOLE, LOG_CONSOLE_LEVEL, _MODEL_MAX_TOKENS
from qrclaw.skills.registry import SkillRegistry
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.filters import is_done

console = Console()

# prompt_toolkit 的键绑定
bindings = KeyBindings()

@bindings.add("escape", "enter")  # Alt+Enter 或 Esc+Enter 换行
def _newline(event):
    event.current_buffer.insert_text("\n")


def get_input() -> str:
    """多行输入：Enter 提交，Alt+Enter 换行"""
    session = PromptSession(key_bindings=bindings)
    return session.prompt(
        "> ",
        multiline=False,  # 默认单行，Alt+Enter 插入换行符实现多行
    )


def show_context_usage(session: Session):
    """显示上下文使用情况"""
    # 计算百分比
    percentage = (session.prompt_tokens / _MODEL_MAX_TOKENS) * 100 if session.prompt_tokens > 0 else 0

    # 根据百分比选择颜色
    if percentage < 50:
        color = "green"
    elif percentage < 70:
        color = "yellow"
    else:
        color = "red"

    # 显示状态栏
    usage_text = Text()
    usage_text.append("📊 上下文: ", style="dim")
    usage_text.append(f"{percentage:.1f}%", style=f"bold {color}")
    usage_text.append(f" ({session.prompt_tokens:,}/{_MODEL_MAX_TOKENS:,} tokens)", style="dim")

    # 如果接近压缩阈值，显示警告
    if percentage >= 60:
        usage_text.append(" ⚠️  接近压缩阈值", style="bold red")

    console.print(usage_text)


def handle_skill_command(args: str):
    """处理 /skill 命令"""
    parts = args.strip().split(maxsplit=1)

    if not parts:
        console.print("[red]用法: /skill <list|import> [参数][/red]")
        return

    subcommand = parts[0]

    if subcommand == "import":
        if len(parts) < 2:
            console.print("[red]用法: /skill import <skill_name_or_url>[/red]")
            console.print("[dim]示例:[/dim]")
            console.print("[dim]  /skill import agent-self-reflection[/dim]")
            console.print("[dim]  /skill import https://github.com/user/skill[/dim]")
            console.print()
            console.print("[dim]或者在对话中说：[/dim]")
            console.print("[dim]  请导入 skill agent-self-reflection[/dim]")
            return

        skill_identifier = parts[1]
        console.print(f"[cyan]请在对话中说：导入 skill {skill_identifier}[/cyan]")
        console.print("[dim]LLM 会使用 install-skill skill 帮你下载[/dim]")

    elif subcommand == "list":
        # 列出所有技能
        registry = SkillRegistry()
        registry.load_from_dir()

        if not registry.skills:
            console.print("[yellow]没有安装任何技能[/yellow]")
            console.print("[dim]在对话中说：导入 skill <skill_name>[/dim]")
            return

        table = Table(title="已安装的技能")
        table.add_column("名称", style="cyan")
        table.add_column("描述", style="green")
        table.add_column("版本", style="yellow")

        for name, skill in registry.skills.items():
            table.add_row(
                name,
                skill.description[:50] + "..." if len(skill.description) > 50 else skill.description,
                skill.version
            )

        console.print(table)

    else:
        console.print(f"[red]未知子命令: {subcommand}[/red]")
        console.print("[dim]可用命令: list, import[/dim]")


def main():
    # 创建会话（默认 session_id = "default"）
    session = Session()

    # 初始化日志系统（按会话 ID）
    setup_logger(
        session_id=session.session_id,
        log_level=LOG_LEVEL,
        log_to_file=LOG_TO_FILE,
        log_to_console=LOG_CONSOLE_LEVEL,
        log_max_days=LOG_MAX_DAYS,
    )

    console.print(
        "[bold cyan]QRClaw Agent[/bold cyan] 启动\n"
        "[dim]Enter 发送 · Alt+Enter 换行 · exit 退出 · clear 清除会话 · /skill 管理技能[/dim]\n"
    )

    if session.messages:
        console.print(f"[dim]已加载历史会话，共 {len(session.messages)} 条消息[/dim]\n")

    while True:
        try:
            # 显示上下文使用情况
            show_context_usage(session)

            user_input = get_input()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]再见！[/dim]")
            break

        user_input = user_input.strip()

        if not user_input:
            continue

        # 处理 /skill 命令
        if user_input.startswith("/skill "):
            handle_skill_command(user_input[7:])
            continue

        if user_input == "/skill":
            console.print("[dim]用法: /skill <list|import> [参数][/dim]")
            console.print("[dim]  list - 列出所有技能[/dim]")
            console.print("[dim]  import <skill_name> - 导入技能[/dim]")
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
        print(f"\033[{lines}A\033[J", end="")  # 上移N行，清除到底部

        console.print(Panel(
            user_input,
            title="[bold magenta]你[/bold magenta]",
            border_style="magenta",
            expand=False,
            width=min(len(user_input) + 6, console.width),
        ))

        run(user_input, session, console)


if __name__ == "__main__":
    main()