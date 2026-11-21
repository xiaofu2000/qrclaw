"""自动记忆提取的批量、闲置及冷却配置。

默认值来自 ``qrclaw.config.settings.memory``（配置文件 ``memory`` 段，或 ``MEMORY_*`` 环境变量）；
测试和调用方可以按字段覆盖。
"""

from dataclasses import dataclass, field
import math

from qrclaw.config import settings


@dataclass
class ExtractionConfig:
    """一次提取任务使用的阈值；构造时校验，避免无效配置形成连续调用模型的循环。"""

    # 首次提取前，待处理正文达到该 token 数时不等闲置。
    minimum_message_tokens_to_init: int = field(default_factory=lambda: settings.memory.init_threshold)
    # 之后每次提取的长对话兜底阈值。
    minimum_tokens_between_update: int = field(default_factory=lambda: settings.memory.update_interval)
    minimum_turns: int = field(default_factory=lambda: settings.memory.min_turns)
    idle_seconds: float = field(default_factory=lambda: settings.memory.idle_seconds)
    cooldown_seconds: float = field(default_factory=lambda: settings.memory.cooldown_seconds)
    # 结构化输出解析失败时的重试次数。
    max_retries: int = 3

    def __post_init__(self):
        if (self.minimum_message_tokens_to_init <= 0 or self.minimum_tokens_between_update <= 0
                or self.minimum_turns <= 0 or self.idle_seconds < 0 or self.cooldown_seconds < 0
                or not math.isfinite(self.idle_seconds) or not math.isfinite(self.cooldown_seconds)):
            raise ValueError("记忆提取阈值必须为正数，闲置和冷却时间不能为负数")


DEFAULT_CONFIG = ExtractionConfig()
