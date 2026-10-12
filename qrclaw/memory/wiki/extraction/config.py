"""
Extraction Configuration - 提取配置常量

与 memory_extraction.py 行 24-45 保持字段名兼容
"""

from dataclasses import dataclass
import math
import os


@dataclass
class ExtractionConfig:
    """自动记忆提取的批量、闲置及冷却配置，支持环境变量覆盖。"""

    # 初始化阈值：Token 数量达到多少时开始提取
    # 支持环境变量覆盖：MEMORY_INIT_THRESHOLD
    minimum_message_tokens_to_init: int = 5000

    # 更新间隔：Token 增长多少时触发下一次提取
    # 支持环境变量覆盖：MEMORY_UPDATE_INTERVAL
    minimum_tokens_between_update: int = 5000

    minimum_turns: int = 3
    idle_seconds: float = 120
    cooldown_seconds: float = 300

    # 供旧的独立策略函数兼容，自动批量提取不再依赖工具调用次数。
    # 支持环境变量覆盖：MEMORY_TOOL_CALL_INTERVAL
    tool_calls_between_updates: int = 0

    # 保留旧配置字段；自动队列已合并成每会话一个批次。
    max_pending: int = 3

    # LLM 调用最大重试次数
    max_retries: int = 3

    def __post_init__(self):
        """从环境变量加载配置（如果设置了）"""
        if "MEMORY_INIT_THRESHOLD" in os.environ:
            self.minimum_message_tokens_to_init = int(os.environ["MEMORY_INIT_THRESHOLD"])
        if "MEMORY_UPDATE_INTERVAL" in os.environ:
            self.minimum_tokens_between_update = int(os.environ["MEMORY_UPDATE_INTERVAL"])
        if "MEMORY_TOOL_CALL_INTERVAL" in os.environ:
            self.tool_calls_between_updates = int(os.environ["MEMORY_TOOL_CALL_INTERVAL"])
        if "MEMORY_MIN_TURNS" in os.environ:
            self.minimum_turns = int(os.environ["MEMORY_MIN_TURNS"])
        if "MEMORY_IDLE_SECONDS" in os.environ:
            self.idle_seconds = float(os.environ["MEMORY_IDLE_SECONDS"])
        if "MEMORY_COOLDOWN_SECONDS" in os.environ:
            self.cooldown_seconds = float(os.environ["MEMORY_COOLDOWN_SECONDS"])
        if (self.minimum_message_tokens_to_init <= 0 or self.minimum_tokens_between_update <= 0
                or self.minimum_turns <= 0 or self.idle_seconds < 0 or self.cooldown_seconds < 0
                or not math.isfinite(self.idle_seconds) or not math.isfinite(self.cooldown_seconds)):
            raise ValueError("记忆提取阈值必须为正数，闲置和冷却时间不能为负数")


DEFAULT_CONFIG = ExtractionConfig()
