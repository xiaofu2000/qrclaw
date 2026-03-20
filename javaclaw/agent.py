import json
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from javaclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, MAX_ITERATIONS, COMPRESS_THRESHOLD
from javaclaw.tools.registry import get_schemas, execute, need_confirm
from javaclaw.memory.session import Session
from javaclaw.memory import compressor
from javaclaw.prompt import build_system_prompt

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)


def run(user_input: str, session: Session, console: Console):
    session.add({"role": "user", "content": user_input})

    # system prompt 每次实时构建，不存进 session
    # 这样工作目录、工具列表永远是最新的
    tool_names = [s["function"]["name"] for s in get_schemas()]
    system_prompt = {"role": "system", "content": build_system_prompt(tool_names)}

    for i in range(MAX_ITERATIONS):

        # 每次调 LLM 时把 system prompt 拼到最前面
        messages = [system_prompt, *session.messages]

        # spinner 只包住 LLM 请求这一步，拿到响应立即退出
        with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
            response = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                tools=get_schemas(),
            )

        finish_reason = response.choices[0].finish_reason
        message = response.choices[0].message

        if response.usage.prompt_tokens > COMPRESS_THRESHOLD:
            compressor.summarize(session)

        if finish_reason == "stop":
            session.add({"role": "assistant", "content": message.content})
            console.print()  # 添加空行
            console.print(Panel(
                message.content,
                title="[bold green]Agent[/bold green]",
                border_style="green",
                expand=False,  # 不撑满整行，按内容宽度显示
            ))
            console.print()  # 添加空行
            return

        if finish_reason == "length":
            console.print("[bold red]警告：回复被截断，请尝试简化任务[/bold red]\n")
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
            console.print()  # 添加空行
            console.print(Panel(
                f"[bold cyan]{name}[/bold cyan]\n" + args_formatted,
                title="[bold yellow]▶ 调用工具[/bold yellow]",
                border_style="yellow",
                expand=False,
            ))
            console.print()  # 添加空行

            # 高风险工具执行前，询问用户确认
            if need_confirm(name):
                console.print(f"[bold red]⚠ 需要确认[/bold red] 是否允许执行？(y/n) ", end="")
                choice = input().strip().lower()
                if choice != "y":
                    result = "用户拒绝执行"
                    console.print(Panel(result, title="[bold red]已拒绝[/bold red]", border_style="red", expand=False))
                    session.add({"role": "tool", "tool_call_id": tc.id, "content": result})
                    continue

            result = execute(name, arguments)

            preview = result[:200] + "..." if len(result) > 200 else result
            console.print(Panel(
                preview,
                title="[bold blue]◀ 工具结果[/bold blue]",
                border_style="blue",
                expand=False,
            ))
            console.print()  # 添加空行

            session.add({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })