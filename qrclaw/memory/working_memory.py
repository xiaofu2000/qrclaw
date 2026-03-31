"""
工作记忆模块

存储当前任务的关键信息，会话级别，跨步骤共享。
用于主 Agent 多轮对话、子 Agent 继承上下文、步骤间信息传递。
"""
from dataclasses import dataclass, field
from typing import Any
import copy


@dataclass
class WorkingMemory:
    """
    当前任务的工作记忆。
    
    生命周期：
    - 主 Agent 创建时初始化
    - 串行步骤之间共享和更新
    - 并行步骤继承快照，只读
    - 并行结束后由主 Agent 整合
    
    使用方式：
    - 子 Agent 通过工具调用更新 working_memory
    - 每轮 ReAct 后注入 system prompt
    """
    
    # 任务信息
    goal: str = ""                      # 当前任务目标
    original_request: str = ""          # 用户原始请求
    
    # 收集的信息
    relevant_files: list[str] = field(default_factory=list)    # 发现的相关文件
    key_findings: list[str] = field(default_factory=list)      # 关键发现
    decisions: list[str] = field(default_factory=list)         # 做出的决策
    
    # 产出物
    created_files: list[str] = field(default_factory=list)     # 创建的文件
    modified_files: list[str] = field(default_factory=list)    # 修改的文件
    
    # 其他元数据
    metadata: dict[str, Any] = field(default_factory=dict)     # 扩展字段
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "goal": self.goal,
            "original_request": self.original_request,
            "relevant_files": self.relevant_files,
            "key_findings": self.key_findings,
            "decisions": self.decisions,
            "created_files": self.created_files,
            "modified_files": self.modified_files,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "WorkingMemory":
        """从字典反序列化"""
        return cls(
            goal=data.get("goal", ""),
            original_request=data.get("original_request", ""),
            relevant_files=data.get("relevant_files", []),
            key_findings=data.get("key_findings", []),
            decisions=data.get("decisions", []),
            created_files=data.get("created_files", []),
            modified_files=data.get("modified_files", []),
            metadata=data.get("metadata", {}),
        )
    
    def copy(self) -> "WorkingMemory":
        """深拷贝一份（用于子 Agent 继承）"""
        return copy.deepcopy(self)
    
    def to_prompt(self) -> str:
        """转换成可注入 prompt 的字符串"""
        lines = []
        
        if self.goal:
            lines.append(f"**目标**: {self.goal}")
        
        if self.relevant_files:
            # 最多显示最近 10 个
            files = self.relevant_files[-10:]
            lines.append(f"**相关文件**: {', '.join(files)}")
        
        if self.key_findings:
            lines.append("**关键发现**:")
            for f in self.key_findings[-5:]:
                lines.append(f"  - {f}")
        
        if self.decisions:
            lines.append("**已做决策**:")
            for d in self.decisions[-5:]:
                lines.append(f"  - {d}")
        
        if self.created_files or self.modified_files:
            files = self.created_files[-5:] + self.modified_files[-5:]
            lines.append(f"**产出文件**: {', '.join(files)}")
        
        if not lines:
            return ""
        
        return "## 当前任务工作记忆\n" + "\n".join(lines)
    
    def add_finding(self, finding: str):
        """添加发现（去重）"""
        if finding and finding not in self.key_findings:
            self.key_findings.append(finding)
    
    def add_decision(self, decision: str):
        """添加决策（去重）"""
        if decision and decision not in self.decisions:
            self.decisions.append(decision)
    
    def add_relevant_file(self, file_path: str):
        """添加相关文件（去重）"""
        if file_path and file_path not in self.relevant_files:
            self.relevant_files.append(file_path)
    
    def add_created_file(self, file_path: str):
        """添加创建的文件（去重）"""
        if file_path and file_path not in self.created_files:
            self.created_files.append(file_path)
    
    def add_modified_file(self, file_path: str):
        """添加修改的文件（去重）"""
        if file_path and file_path not in self.modified_files:
            self.modified_files.append(file_path)
    
    def merge(self, other: "WorkingMemory"):
        """
        合并另一个 WorkingMemory（用于并行步骤结束后整合）。
        合并策略：去重合并列表，不覆盖单值字段。
        """
        # 列表字段合并（去重）
        for f in other.relevant_files:
            if f not in self.relevant_files:
                self.relevant_files.append(f)
        
        for f in other.key_findings:
            if f not in self.key_findings:
                self.key_findings.append(f)
        
        for d in other.decisions:
            if d not in self.decisions:
                self.decisions.append(d)
        
        for f in other.created_files:
            if f not in self.created_files:
                self.created_files.append(f)
        
        for f in other.modified_files:
            if f not in self.modified_files:
                self.modified_files.append(f)
        
        # metadata 合并
        self.metadata.update(other.metadata)
    
    def clear(self):
        """清空工作记忆（新任务开始时）"""
        self.goal = ""
        self.original_request = ""
        self.relevant_files.clear()
        self.key_findings.clear()
        self.decisions.clear()
        self.created_files.clear()
        self.modified_files.clear()
        self.metadata.clear()