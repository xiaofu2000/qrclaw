import os
import json
from google import genai
from google.genai import types
from google.genai.types import HttpOptions
from qrclaw.providers.base import LLMProvider, LLMResponse, ToolCall
from qrclaw.config import OPENAI_API_KEY, OPENAI_MODEL
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.providers.vertex")


def _build_vertex_tools(schemas: list[dict]) -> list[types.Tool] | None:
    """把 OpenAI tool schema 转换成 Vertex AI 的 Tool 格式"""
    if not schemas:
        return None
    declarations = []
    for s in schemas:
        fn = s["function"]
        params = fn.get("parameters")
        declarations.append(types.FunctionDeclaration(
            name=fn["name"],
            description=fn.get("description", ""),
            parameters=params,
        ))
    return [types.Tool(function_declarations=declarations)]


class VertexProvider(LLMProvider):

    def __init__(self):
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
        self._client = genai.Client(
            http_options=HttpOptions(api_version="v1"),
            api_key=OPENAI_API_KEY,
        )
        logger.info("Vertex AI 渠道已初始化")

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        contents = []
        system_parts = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                if content:
                    system_parts.append(content)

            elif role == "user":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text=content or "")]
                ))

            elif role == "assistant":
                parts = []
                if content:
                    parts.append(types.Part(text=content))
                # 处理 tool_calls
                for tc in msg.get("tool_calls", []):
                    fn = tc["function"]
                    try:
                        args = json.loads(fn["arguments"])
                    except Exception:
                        args = {}
                    parts.append(types.Part(
                        function_call=types.FunctionCall(
                            name=fn["name"],
                            args=args,
                        )
                    ))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif role == "tool":
                # tool 结果，转成 function_response
                tool_call_id = msg.get("tool_call_id", "")
                try:
                    result = json.loads(content) if content else {}
                    if not isinstance(result, dict):
                        result = {"result": content}
                except Exception:
                    result = {"result": content}
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(
                        function_response=types.FunctionResponse(
                            name=tool_call_id,
                            response=result,
                        )
                    )]
                ))

        config_kwargs = {}
        if system_parts:
            config_kwargs["system_instruction"] = "\n".join(system_parts)
        vertex_tools = _build_vertex_tools(tools)
        if vertex_tools:
            config_kwargs["tools"] = vertex_tools

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        response = self._client.models.generate_content(
            model=OPENAI_MODEL,
            contents=contents,
            config=config,
        )

        # 解析响应
        tool_calls = []
        text_content = ""
        finish_reason = "stop"

        candidate = response.candidates[0]
        for part in candidate.content.parts:
            if part.text:
                text_content += part.text
            elif part.function_call:
                fc = part.function_call
                tool_calls.append(ToolCall(
                    id=fc.name,  # Vertex AI 没有 tool_call_id，用函数名代替
                    name=fc.name,
                    arguments=json.dumps(dict(fc.args), ensure_ascii=False),
                ))

        if tool_calls:
            finish_reason = "tool_calls"

        # token 统计
        usage = response.usage_metadata
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        completion_tokens = getattr(usage, "candidates_token_count", 0) or 0

        logger.info(f"Vertex AI 响应成功，tokens: {prompt_tokens + completion_tokens}")
        return LLMResponse(
            content=text_content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            raw=response,
        )
