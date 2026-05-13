from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str          # JSON 字符串
    thought_signature: str | None = None  # base64 编码，Vertex AI thinking model 专用


@dataclass
class LLMResponse:
    content: str                        # 文本回复，无工具调用时有值
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"         # stop | tool_calls | length
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: object = None                  # 原始响应对象，供需要时使用
    reasoning_content: str | None = None  # thinking 模式的推理内容，需原样回传


class LLMProvider(ABC):

    @abstractmethod
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        json_mode: bool = False,
        temperature: float | None = None,
    ) -> LLMResponse:
        """
        发送消息，返回统一格式的响应。

        Args:
            messages:    消息列表
            tools:       工具 schema 列表
            json_mode:   为 True 时强制 LLM 输出合法 JSON（OpenAI response_format）
                         不支持的 provider 忽略此参数，由调用方自行做正则兜底
            temperature: 温度参数，越低越确定性输出。为 None 时使用默认值。
        """
        ...
