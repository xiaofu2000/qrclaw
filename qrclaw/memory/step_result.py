"""
步骤结果模块

存储单个步骤的执行结果，供后续串行步骤追溯上下文。
"""
from dataclasses import dataclass, field


@dataclass
class StepResult:
    """单个步骤的执行结果"""

    # 基本信息
    step_id: int
    description: str
    status: str = "success"  # "success" | "failed"

    # 结果
    summary: str = ""               # 简短摘要（给后续步骤看）
    output: str = ""                # 子 Agent 的最终输出
    messages: list[dict] = field(default_factory=list)  # 完整消息历史（可追溯）

    # 产出物
    created_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)

    def to_context_prompt(self) -> str:
        """生成给后续步骤注入的上下文字符串"""
        lines = [
            f"### Step {self.step_id} 结果：{self.description}",
            f"**状态**: {self.status}",
        ]
        if self.output:
            lines.append(f"**输出**:\n{self.output}")
        if self.created_files:
            lines.append(f"**创建文件**: {', '.join(self.created_files)}")
        if self.modified_files:
            lines.append(f"**修改文件**: {', '.join(self.modified_files)}")
        return "\n".join(lines)
