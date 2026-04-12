"""
Extraction Configuration - 提取配置常量

与 memory_extraction.py 行 24-45 保持字段名兼容
"""

from dataclasses import dataclass
import os


@dataclass
class ExtractionConfig:
    """Memory extraction configuration with environment variable overrides"""

    # 初始化阈值：Token 数量达到多少时开始提取
    # 支持环境变量覆盖：MEMORY_INIT_THRESHOLD
    minimum_message_tokens_to_init: int = 10000

    # 更新间隔：Token 增长多少时触发下一次提取
    # 支持环境变量覆盖：MEMORY_UPDATE_INTERVAL
    minimum_tokens_between_update: int = 5000

    # 工具调用次数间隔
    # 支持环境变量覆盖：MEMORY_TOOL_CALL_INTERVAL
    tool_calls_between_updates: int = 3

    # 最大待处理数量
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


DEFAULT_CONFIG = ExtractionConfig()
