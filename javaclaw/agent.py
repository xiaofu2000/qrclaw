from openai import OpenAI
from javaclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, MAX_ITERATIONS, COMPRESS_THRESHOLD
from javaclaw.tools.registry import get_schemas, execute
from javaclaw.memory.session import Session
from javaclaw.memory import compressor

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)


def run(user_input: str, session: Session):
    session.add({"role": "user", "content": user_input})

    for i in range(MAX_ITERATIONS):
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=session.messages,
            tools=get_schemas(),
        )

        finish_reason = response.choices[0].finish_reason
        message = response.choices[0].message

        # 检查 token 用量，超阈值就压缩
        if response.usage.prompt_tokens > COMPRESS_THRESHOLD:
            compressor.summarize(session)

        if finish_reason == "stop":
            session.add({"role": "assistant", "content": message.content})
            print(f"Agent：{message.content}")
            return

        if finish_reason == "length":
            print("警告：回复被截断，请尝试简化任务")
            return

        session.add(message.model_dump())

        for tc in message.tool_calls:
            name = tc.function.name
            arguments = tc.function.arguments
            print(f"  → 调用工具：{name}，参数：{arguments}")

            result = execute(name, arguments)
            print(f"  ← 工具结果：{result[:100]}...\n")

            session.add({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
