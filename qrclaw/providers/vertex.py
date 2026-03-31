import os
import json
import base64
from google import genai
from google.genai import types
from google.genai.types import HttpOptions
from qrclaw.providers.base import LLMProvider, LLMResponse, ToolCall
from qrclaw.config import OPENAI_MODEL, VERTEX_API_KEY
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
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"
        self._client = genai.Client(
            http_options=HttpOptions(api_version="v1"),
            api_key=VERTEX_API_KEY,
        )
        logger.info("Vertex AI 渠道已初始化")

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False) -> LLMResponse:
        # json_mode=True 时设置 response_mime_type="application/json"，强制输出合法 JSON
        contents = []
        system_parts = []

        i = 0
        while i < len(messages):
            msg = messages[i]
            role = msg.get("role")
            content = msg.get("content", "")

            if role == "system":
                if content:
                    system_parts.append(content)
                i += 1

            elif role == "user":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(text=content or "")]
                ))
                i += 1

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
                i += 1

            elif role == "tool":
                # 把连续的 tool 消息合并到同一个 Content，Vertex AI 要求 function_response 数量与 function_call 一致
                tool_parts = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    m = messages[i]
                    tool_call_id = m.get("tool_call_id", "")
                    c = m.get("content", "")
                    try:
                        result = json.loads(c) if c else {}
                        if not isinstance(result, dict):
                            result = {"result": c}
                    except Exception:
                        result = {"result": c}
                    tool_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=tool_call_id,
                            response=result,
                        )
                    ))
                    i += 1
                contents.append(types.Content(role="user", parts=tool_parts))

            else:
                i += 1

        config_kwargs = {}
        if system_parts:
            config_kwargs["system_instruction"] = "\n".join(system_parts)
        vertex_tools = _build_vertex_tools(tools)
        if vertex_tools:
            config_kwargs["tools"] = vertex_tools
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

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
