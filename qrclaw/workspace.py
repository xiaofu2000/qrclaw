"""
工作空间模块

每个 agent 有独立的工作空间，所有路径都从这里派生，不在各模块中硬编码。

目录结构：
  ~/.qrclaw/agents/<agent_id>/
    ├── sessions/          会话文件
    ├── logs/              日志文件
    ├── skills/            技能
    ├── MEMORY.md          中期记忆
    ├── HEARTBEAT.md       心跳任务配置
    └── sub-agents/        子 agent 工作空间
        └── <sub_id>/
            ├── sessions/
            ├── logs/
            ├── skills/
            └── MEMORY.md
"""
from pathlib import Path

# 所有 agent 的根目录
AGENTS_ROOT = Path.home() / ".qrclaw" / "agents"


class Workspace:
    """agent 工作空间，持有该 agent 所有资源的路径。"""

    def __init__(self, agent_id: str = "default", _root: Path = None):
        self.agent_id = agent_id
        # _root 由 sub_agent() 传入，支持嵌套路径
        self.root = _root or (AGENTS_ROOT / agent_id)

        # 各子目录/文件路径
        self.sessions_dir = self.root / "sessions"
        self.logs_dir = self.root / "logs"
        self.skills_dir = self.root / "skills"
        self.memory_file = self.root / "MEMORY.md"
        self.heartbeat_file = self.root / "HEARTBEAT.md"
        self.sub_agents_dir = self.root / "sub-agents"

        # 确保目录都存在
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def sub_agent(self, sub_id: str) -> "Workspace":
        """
        创建子 agent 工作空间，嵌套在当前 agent 下。

        路径：<当前agent_root>/sub-agents/<sub_id>/
        """
        sub_root = self.sub_agents_dir / sub_id
        return Workspace(agent_id=sub_id, _root=sub_root)


def list_agents() -> list[str]:
    """列出所有顶级 agent ID。"""
    if not AGENTS_ROOT.exists():
        return []
    return [d.name for d in sorted(AGENTS_ROOT.iterdir()) if d.is_dir()]