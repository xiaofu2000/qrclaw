"""
显示/渲染模块

负责上下文状态栏等与纯展示相关的逻辑。
"""
from rich.console import Console
from rich.text import Text
from rich.panel import Panel
from qrclaw.memory.session import Session
from qrclaw.config import _MODEL_MAX_TOKENS, COMPRESS_THRESHOLD


def show_context_usage(console: Console, session: Session) -> None:
    """在控制台打印当前会话的上下文使用情况。"""
    percentage = (
        (session.prompt_tokens / _MODEL_MAX_TOKENS) * 100
        if session.prompt_tokens > 0
        else 0
    )

    if percentage < 50:
        color = "green"
    elif percentage < 70:
        color = "yellow"
    else:
        color = "red"

    text = Text()
    text.append(f"[{session.session_id}] ", style="bold cyan")
    text.append("上下文: ", style="dim")
    text.append(f"{percentage:.1f}%", style=f"bold {color}")
    text.append(f" ({session.prompt_tokens:,}/{_MODEL_MAX_TOKENS:,} tokens)", style="dim")

    if session.prompt_tokens > COMPRESS_THRESHOLD:
        text.append(" ⚠  接近压缩阈值", style="bold red")

    console.print(text)


def show_plan_progress(console: Console, session: Session) -> None:
    """打印当前执行计划进度，无计划时不显示。"""
    if not session.active_plan:
        return

    plan = session.active_plan
    lines = Text()
    lines.append(f"目标：{plan['goal']}\n", style="bold")

    for step in plan["steps"]:
        if step["done"]:
            lines.append(f"  ✅ Step {step['id']}: {step['description']}\n", style="dim green")
        else:
            lines.append(f"  ⬜ Step {step['id']}: {step['description']}\n", style="white")

    console.print(Panel(
        lines,
        title="[bold yellow]执行计划[/bold yellow]",
        border_style="yellow",
        expand=False,
    ))
