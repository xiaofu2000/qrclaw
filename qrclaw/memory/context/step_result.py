"""步骤结果供重规划、后续子任务及最终汇总复用。"""
from dataclasses import dataclass


@dataclass
class StepResult:
    """保存真实步骤输出，不重复维护未使用的文件和消息副本。"""
    step_id: str | int
    description: str
    output: str = ""

    def to_context_prompt(self) -> str:
        """将步骤结果转换为后续模型请求的上下文。"""
        return f"### Step {self.step_id} 结果：{self.description}\n{self.output}"
