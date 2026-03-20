from openai import OpenAI
from javaclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
from javaclaw.tools.registry import get_schemas, execute

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)


def run(user_input: str):
    # messages 是整个对话历史，每轮都追加进去
    messages = [
        {"role": "user", "content": user_input}
    ]

    print(f"\n用户：{user_input}\n")

    # 最多循环 10 轮，防止死循环
    for i in range(10):
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=get_schemas(),
        )

        message = response.choices[0].message

        # LLM 没有调用工具，说明任务完成，输出最终答案
        if not message.tool_calls:
            print(f"Agent：{message.content}")
            return

        # LLM 要调用工具
        for tc in message.tool_calls:
            name = tc.function.name
            arguments = tc.function.arguments
            print(f"  → 调用工具：{name}，参数：{arguments}")

            result = execute(name, arguments)
            print(f"  ← 工具结果：{result[:100]}...\n")

            # 把这轮的 assistant 消息和工具结果都追加到 messages
            messages.append(message)
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
