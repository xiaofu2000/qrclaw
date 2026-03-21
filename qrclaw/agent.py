import json
from openai import OpenAI
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from qrclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, MAX_ITERATIONS, COMPRESS_THRESHOLD
from qrclaw.tools.registry import get_schemas, execute, need_confirm
from qrclaw.memory.session import Session
from qrclaw.memory import compressor
from qrclaw.prompt import build_system_prompt
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.agent")

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)


def run(user_input: str, session: Session, console: Console):
    logger.info(f"收到用户输入: {user_input[:100]}...")
    
    session.add({"role": "user", "content": user_input})

    # system prompt 每次实时构建，不存进 session
    # 这样工作目录、工具列表永远是最新的
    tool_names = [s["function"]["name"] for s in get_schemas()]
    system_prompt = {"role": "system", "content": build_system_prompt(tool_names)}
    logger.debug(f"System prompt 已构建，可用工具: {', '.join(tool_names)}")

    for iteration in range(MAX_ITERATIONS):
        logger.debug(f"开始第 {iteration + 1} 轮推理")

        # 每次调 LLM 时把 system prompt 拼到最前面
        messages = [system_prompt, *session.messages]

        # spinner 只包住 LLM 请求这一步，拿到响应立即退出
        with console.status("[bold yellow]思考中...[/bold yellow]", spinner="dots"):
            logger.debug(f"调用 LLM，消息数: {len(messages)}")
            try:
                response = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    tools=get_schemas(),
                )
                logger.info(f"LLM 响应成功，使用 {response.usage.total_tokens} tokens (prompt: {response.usage.prompt_tokens}, completion: {response.usage.completion_tokens})")
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}", exc_info=True)
                raise

        finish_reason = response.choices[0].finish_reason
        message = response.choices[0].message

        # 检查是否需要压缩
        if response.usage.prompt_tokens > COMPRESS_THRESHOLD:
            logger.warning(f"Prompt tokens ({response.usage.prompt_tokens}) 超过阈值 ({COMPRESS_THRESHOLD})，触发压缩")
            compressor.summarize(session)
        else:
            logger.debug(f"Prompt tokens: {response.usage.prompt_tokens}, 阈值: {COMPRESS_THRESHOLD}")

        if finish_reason == "stop":
            logger.info(f"推理完成，最终答案长度: {len(message.content)} 字符")
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
            logger.warning("LLM 响应被截断 (finish_reason=length)")
            console.print("[bold red]警告：回复被截断，请尝试简化任务[/bold red]\n")
            return

        # assistant 消息只存一次，在工具循环之前
        assistant_msg_saved = False

        for tc in message.tool_calls:
            name = tc.function.name
            arguments = tc.function.arguments
            logger.info(f"工具调用: {name}, 参数: {arguments[:200]}...")

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
                logger.debug(f"工具 {name} 需要用户确认")
                console.print(f"[bold red]⚠ 需要确认[/bold red] 是否允许执行？(y/n) ", end="")
                choice = input().strip().lower()
                if choice != "y":
                    result = "用户拒绝执行此操作"
                    logger.warning(f"用户拒绝执行工具: {name}")
                    console.print(Panel(result, title="[bold red]已拒绝[/bold red]", border_style="red", expand=False))
                    if not assistant_msg_saved:
                        session.add(message.model_dump())
                        assistant_msg_saved = True
                    session.add({"role": "tool", "tool_call_id": tc.id, "content": result})
                    break  # 退出工具循环，回到外层让LLM重新推理

            if not assistant_msg_saved:
                session.add(message.model_dump())
                assistant_msg_saved = True

            try:
                result = execute(name, arguments)
                logger.info(f"工具执行成功: {name}, 结果长度: {len(result)} 字符")
            except Exception as e:
                result = f"工具执行失败: {str(e)}"
                logger.error(f"工具执行失败: {name}, 错误: {e}", exc_info=True)

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