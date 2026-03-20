import javaclaw.tools.builtin  # 触发工具注册
from javaclaw.agent import run
from javaclaw.memory.session import Session
from rich.console import Console
from rich.prompt import Prompt

console = Console()


def main():
    console.print("[bold cyan]JavaClaw Agent[/bold cyan] 启动，输入 [bold]exit[/bold] 退出，[bold]clear[/bold] 清除会话\n")

    session = Session()
    if session.messages:
        console.print(f"[dim]已加载历史会话，共 {len(session.messages)} 条消息[/dim]\n")

    while True:
        user_input = Prompt.ask("[bold magenta]你[/bold magenta]")

        if user_input.strip() == "exit":
            console.print("[dim]再见！[/dim]")
            break

        if user_input.strip() == "clear":
            session.clear()
            console.print("[dim]会话已清除[/dim]\n")
            continue

        if not user_input.strip():
            continue

        run(user_input.strip(), session)


if __name__ == "__main__":
    main()
