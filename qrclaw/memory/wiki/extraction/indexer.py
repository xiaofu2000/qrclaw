"""
Extraction Indexer - 提取状态索引管理

管理提取触发状态：
- 记录上次提取的 token 数、工具调用数
- 追踪消息截断位置
- 提供状态快照
"""

from dataclasses import dataclass
from typing import Optional
from pathlib import Path
import json


@dataclass
class ExtractionState:
    """提取状态快照"""

    last_extraction_tokens: int = 0
    last_extraction_tool_calls: int = 0
    last_extraction_idx: int = 0
    total_extractions: int = 0
    last_extraction_time: float = 0.0


class ExtractionIndexer:
    """提取状态索引管理器"""

    def __init__(self, memory_dir: Path):
        """
        Args:
            memory_dir: Wiki memory 目录路径
        """
        self.memory_dir = Path(memory_dir)
        self.state_file = self.memory_dir / ".extraction_state.json"
        self._state: Optional[ExtractionState] = None

    def get_state(self) -> ExtractionState:
        """获取当前状态（懒加载）"""
        if self._state is None:
            self._state = self._load_state()
        return self._state

    def record_extraction(
        self,
        tokens: int,
        tool_calls: int,
        message_count: int,
        current_time: float,
    ) -> None:
        """
        记录一次提取事件

        Args:
            tokens: 当前 token 数
            tool_calls: 工具调用数
            message_count: 消息数量
            current_time: 当前时间戳
        """
        state = self.get_state()
        state.last_extraction_tokens = tokens
        state.last_extraction_tool_calls = tool_calls
        state.total_extractions += 1
        state.last_extraction_time = current_time
        self._save_state()

    def record_message_idx(self, idx: int) -> None:
        """记录消息索引"""
        state = self.get_state()
        state.last_extraction_idx = idx
        self._save_state()

    def reset(self) -> None:
        """重置状态"""
        self._state = ExtractionState()
        self._save_state()

    def _load_state(self) -> ExtractionState:
        """从文件加载状态"""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                return ExtractionState(**data)
            except (json.JSONDecodeError, TypeError):
                pass
        return ExtractionState()

    def _save_state(self) -> None:
        """保存状态到文件"""
        if self._state is not None:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(
                    {
                        "last_extraction_tokens": self._state.last_extraction_tokens,
                        "last_extraction_tool_calls": self._state.last_extraction_tool_calls,
                        "last_extraction_idx": self._state.last_extraction_idx,
                        "total_extractions": self._state.total_extractions,
                        "last_extraction_time": self._state.last_extraction_time,
                    },
                    indent=2,
                )
            )
