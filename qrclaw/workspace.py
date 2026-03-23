"""
工作空间模块

每个 agent 有独立的工作空间，所有路径都从这里派生，不在各模块中硬编码。

目录结构：
  ~/.qrclaw/agents/<agent_id>/
    ├── sessions/     会话文件
    ├── logs/         日志文件
    ├── skills/       技能
    └── MEMORY.md     长期记忆
"""
from pathlib import Path

# 所有 agent 的根目录
AGENTS_ROOT = Path.home() / ".qrclaw" / "agents"


class Workspace:
    """agent 工作空间，持有该 agent 所有资源的路径。"""

    def __init__(self, agent_id: str = "default"):
        self.agent_id = agent_id
        self.root = AGENTS_ROOT / agent_id

        # 各子目录/文件路径
        self.sessions_dir = self.root / "sessions"
        self.logs_dir = self.root / "logs"
        self.skills_dir = self.root / "skills"
        self.memory_file = self.root / "MEMORY.md"

        # 确保目录都存在
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)


def list_agents() -> list[str]:
    """列出所有已存在的 agent ID。"""
    if not AGENTS_ROOT.exists():
        return []
    return [d.name for d in sorted(AGENTS_ROOT.iterdir()) if d.is_dir()]
