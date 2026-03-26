import json
import base64
from google import genai
from google.genai import types
from google.genai.types import HttpOptions
from qrclaw.providers.base import LLMProvider, LLMResponse, ToolCall
from qrclaw.config import OPENAI_MODEL, VERTEX_PROJECT, VERTEX_LOCATION
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.providers.vertex")

# thought_signature 在 tool_call 里的存储 key
_TS_KEY = "__thought_signature__"


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
        self._client = genai.Client(
            vertexai=True,
            project=VERTEX_PROJECT,
            location=VERTEX_LOCATION,
            http_options=HttpOptions(api_version="v1"),
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
                # 处理 tool_calls，恢复 thought_signature
                for tc in msg.get("tool_calls", []):
                    fn = tc["function"]
                    try:
                        args = json.loads(fn["arguments"])
                    except Exception:
                        args = {}
                    # 从 tc 里取回 thought_signature（base64 -> bytes）
                    ts_b64 = tc.get(_TS_KEY)
                    ts_bytes = base64.b64decode(ts_b64) if ts_b64 else None

                    part = types.Part(
                        function_call=types.FunctionCall(
                            name=fn["name"],
                            args=args,
                        )
                    )
                    if ts_bytes:
                        part.thought_signature = ts_bytes
                    parts.append(part)
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            elif role == "tool":
                # tool 结果转成 function_response，name 用 tool_call_id（即函数名）
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

        # 解析响应，把 thought_signature 用 base64 存进 ToolCall
        tool_calls = []
        text_content = ""
        finish_reason = "stop"

        candidate = response.candidates[0]
        for part in candidate.content.parts:
            if part.text:
                text_content += part.text
            elif part.function_call:
                fc = part.function_call
                ts = getattr(part, "thought_signature", None)
                ts_b64 = base64.b64encode(ts).decode() if ts else None
                tool_calls.append(ToolCall(
                    id=fc.name,
                    name=fc.name,
                    arguments=json.dumps(dict(fc.args), ensure_ascii=False),
                    thought_signature=ts_b64,
                ))

        if tool_calls:
            finish_reason = "tool_calls"

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
