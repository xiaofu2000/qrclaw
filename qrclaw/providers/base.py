from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class LLMResponse:
    content: str                        # 文本回复，无工具调用时有值
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"         # stop | tool_calls | length
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    raw: object = None                  # 原始响应对象，供需要时使用


class LLMProvider(ABC):

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """发送消息，返回统一格式的响应。"""
        ...
