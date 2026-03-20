from openai import OpenAI
from javaclaw.config import OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL, MAX_ITERATIONS
from javaclaw.tools.registry import get_schemas, execute
from javaclaw.memory.session import Session

client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL or None)


def run(user_input: str, session: Session):
    # 把用户消息存入会话
    session.add({"role": "user", "content": user_input})
    print(f"\n用户：{user_input}\n")

    for i in range(MAX_ITERATIONS):
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=session.messages,  # 用会话历史，而不是临时列表
            tools=get_schemas(),
        )

        finish_reason = response.choices[0].finish_reason
        message = response.choices[0].message

        # finish_reason == "stop" 说明 LLM 认为任务完成了
        if finish_reason == "stop":
            session.add({"role": "assistant", "content": message.content})
            print(f"Agent：{message.content}")
            return

        # token 被截断，任务没完成但也没法继续
        if finish_reason == "length":
            print("警告：回复被截断，请尝试简化任务")
            return

        # 有工具调用
        # 先把 assistant 的这条消息存入会话
        session.add(message.model_dump())

        for tc in message.tool_calls:
            name = tc.function.name
            arguments = tc.function.arguments
            print(f"  → 调用工具：{name}，参数：{arguments}")

            result = execute(name, arguments)
            print(f"  ← 工具结果：{result[:100]}...\n")

            # 把工具结果存入会话
            session.add({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
