"""
工作空间模块

每个 agent 有独立的工作空间，所有路径都从这里派生。
子 agent 共享父 agent 的工作空间（子 agent 是一次性的）。
"""
import os
from pathlib import Path

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
        self.memory_file = self.root / "MEMORY.md"
        self.heartbeat_file = self.root / "HEARTBEAT.md"

        # 确保目录都存在
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)


def list_agents() -> list[str]:
    """列出所有顶级 agent ID"""
    if not AGENTS_ROOT.exists():
        return []
    return [d.name for d in sorted(AGENTS_ROOT.iterdir()) if d.is_dir()]


def ensure_workspace_cwd(workspace: Workspace) -> bool:
    """
    根据 agent 配置自动设置工作目录。
    
    如果 agent 启用了沙箱，切换 cwd 到 workspace.root。
    如果 agent 是 full access，不切换 cwd。
    
    Returns:
        bool: 是否切换了 cwd
    """
    from qrclaw.sandbox import is_sandbox_enabled, is_full_access
    
    agent_id = workspace.agent_id
    
    # 如果是 full access 且未启用沙箱，不需要切换 cwd
    if is_full_access(agent_id) and not is_sandbox_enabled(agent_id):
        return False
    
    # 切换 cwd 到 workspace.root
    target_cwd = str(workspace.root.resolve())
    current_cwd = os.getcwd()
    
    if current_cwd != target_cwd:
        os.chdir(target_cwd)
        return True
    
    return False