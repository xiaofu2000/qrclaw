"""
工作空间模块

每个 agent 有独立的工作空间，所有路径都从这里派生。
子 agent 共享父 agent 的工作空间（子 agent 是一次性的）。
"""
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qrclaw.memory import MemoryManager

# 所有 agent 的根目录
AGENTS_ROOT = Path.home() / ".qrclaw" / "agents"


class Workspace:
    """agent 工作空间，持有该 agent 所有资源的路径。"""

    def __init__(self, agent_id: str = "default", _root: Path = None):
        self.agent_id = agent_id
        self.root = _root or (AGENTS_ROOT / agent_id)

        # 各子目录/文件路径
        self.sessions_dir = self.root / "sessions"
        self.logs_dir = self.root / "logs"
        self.skills_dir = self.root / "skills"
        
        # 记忆系统路径（统一到 agents/{agent_id}/memory/）
        self.memory_dir = self.root / "memory"
        self.heartbeat_file = self.root / "HEARTBEAT.md"
        self.agent_file = self.root / "AGENT.md"

        # 确保目录都存在
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def get_memory_manager(self) -> "MemoryManager":
        """
        获取 MemoryManager 实例
        
        Returns:
            MemoryManager: 增强版记忆管理器
        """
        from qrclaw.memory import MemoryManager
        return MemoryManager(self.memory_dir)

    def sub_agent(self, sub_id: str) -> "Workspace":
        """
        创建子 agent 工作空间
        
        子 agent 共享父 agent 的工作目录，agent_id 用于标识和日志区分。
        这与 sub-agents/ 目录下的持久化子 agent 不同，这里的子 agent
        是轻量级的、一次性的任务执行器。
        
        Args:
            sub_id: 子 agent 标识符（如 "heartbeat", "memory-extraction"）
            
        Returns:
            Workspace: 新建的工作空间（共享 root）
        """
        # agent_id 格式：{parent_id}-{sub_id}
        new_agent_id = f"{self.agent_id}-{sub_id}"
        return Workspace(agent_id=new_agent_id, _root=self.root)


def list_agents() -> list[str]:
    """列出所有顶级 agent ID"""
    if not AGENTS_ROOT.exists():
        return []
    return [d.name for d in sorted(AGENTS_ROOT.iterdir()) if d.is_dir()]
