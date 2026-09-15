"""记忆提取阈值与模型调用配置。"""
from dataclasses import dataclass
import os


@dataclass
class ExtractionConfig:
    """轮末提取阈值与模型重试配置，支持环境变量覆盖。"""

    # 初始化阈值：Token 数量达到多少时开始提取
    # 支持环境变量覆盖：MEMORY_INIT_THRESHOLD
    minimum_message_tokens_to_init: int = 8000

    # 更新间隔：Token 增长多少时触发下一次提取
    # 支持环境变量覆盖：MEMORY_UPDATE_INTERVAL
    minimum_tokens_between_update: int = 4000

    # LLM 调用最大重试次数
    max_retries: int = 3

    def __post_init__(self):
        """从环境变量加载配置（如果设置了）"""
        if "MEMORY_INIT_THRESHOLD" in os.environ:
            self.minimum_message_tokens_to_init = int(os.environ["MEMORY_INIT_THRESHOLD"])
        if "MEMORY_UPDATE_INTERVAL" in os.environ:
            self.minimum_tokens_between_update = int(os.environ["MEMORY_UPDATE_INTERVAL"])
