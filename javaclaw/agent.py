import json
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from javaclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, MAX_ITERATIONS, COMPRESS_THRESHOLD
from javaclaw.tools.registry import get_schemas, execute
from javaclaw.memory.session import Session
from javaclaw.memory import compressor
from javaclaw.prompt import build_system_prompt

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)


def run(user_input: str, session: Session, console: Console):
    # 第一次对话时注入 system prompt
    if not session.messages:
        tool_names = [s["function"]["name"] for s in get_schemas()]
        session.add({"role": "system", "content": build_system_prompt(tool_names)})

    session.add({"role": "user", "content": user_input})

    for i in range(MAX_ITERATIONS):

        # spinner 只包住 LLM 请求这一步，拿到响应立即退出
        with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=session.messages,
                tools=get_schemas(),
            )

        finish_reason = response.choices[0].finish_reason
        message = response.choices[0].message

        if response.usage.prompt_tokens > COMPRESS_THRESHOLD:
            compressor.summarize(session)

        if finish_reason == "stop":
            session.add({"role": "assistant", "content": message.content})
            console.print(Panel(
                message.content,
                title="[bold green]Agent[/bold green]",
                border_style="green",
                expand=False,  # 不撑满整行，按内容宽度显示
            ))
            return

        if finish_reason == "length":
            console.print("[bold red]警告：回复被截断，请尝试简化任务[/bold red]")
            return

        session.add(message.model_dump())

        for tc in message.tool_calls:
            name = tc.function.name
            arguments = tc.function.arguments

            # 把 JSON 字符串格式化后高亮显示
            try:
                args_formatted = json.dumps(json.loads(arguments), ensure_ascii=False, indent=2)
            except Exception:
                args_formatted = arguments
            console.print(Panel(
                f"[bold cyan]{name}[/bold cyan]\n" + args_formatted,
                title="[bold yellow]▶ 调用工具[/bold yellow]",
                border_style="yellow",
                expand=False,
            ))

            result = execute(name, arguments)

            preview = result[:200] + "..." if len(result) > 200 else result
            console.print(Panel(
                preview,
                title="[bold blue]◀ 工具结果[/bold blue]",
                border_style="blue",
                expand=False,
            ))

            session.add({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
