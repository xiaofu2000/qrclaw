"""
沙箱配置模块

所有配置存储在 ~/.qrclaw/permissions.yaml 中。
Docker 沙箱启用时，物理隔离已经解决了安全问题。
"""
import yaml
from pathlib import Path
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

# 配置文件路径
PERMISSIONS_FILE = Path.home() / ".qrclaw" / "permissions.yaml"


# ========== 配置模型 ==========

class MountConfig(BaseModel):
    """挂载配置"""
    host: str  # 主机路径
    container: str  # 容器内路径
    mode: Literal["ro", "rw"] = "ro"  # 访问模式

    def to_docker_bind(self) -> str:
        """转换为 Docker 挂载格式"""
        return f"{self.host}:{self.container}:{self.mode}"


class SandboxConfig(BaseModel):
    """沙箱配置"""
    enabled: bool = True  # 是否启用沙箱
    image: str = "python:3.11-slim"  # Docker 镜像
    network: Literal["none", "bridge"] = "none"  # 网络模式
    memory: str = "512m"  # 内存限制
    pids_limit: int = 256  # 进程数限制
    mounts: List[MountConfig] = Field(default_factory=list)  # 挂载配置


class AgentConfig(BaseModel):
    """Agent 配置"""
    sandbox: Optional[SandboxConfig] = None


class PermissionConfig(BaseModel):
    """全局配置"""
    default_policy: Literal["restricted", "full"] = "restricted"
    agents: dict[str, AgentConfig] = Field(default_factory=dict)


# 默认配置
DEFAULT_CONFIG = {
    "default_policy": "restricted",
    "agents": {
        "default": {
            "sandbox": {
                "enabled": False
            }
        }
    }
}


# ========== 配置管理 ==========

class ConfigManager:
    """配置管理器（单例）"""
    
    _instance = None
    _config: PermissionConfig = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance
    
    def _load_config(self):
        """加载配置"""
        if not PERMISSIONS_FILE.exists():
            self._create_default_config()
        
        try:
            with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                if "agents" not in data:
                    data["agents"] = {}
                if "default" not in data["agents"]:
                    data["agents"]["default"] = DEFAULT_CONFIG["agents"]["default"]
                self._config = PermissionConfig(**data)
        except Exception as e:
            print(f"❌ 加载配置失败: {e}，使用默认配置")
            self._config = PermissionConfig(**DEFAULT_CONFIG)
    
    def _create_default_config(self):
        """创建默认配置文件"""
        PERMISSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        
        config_content = """# QRClaw 沙箱配置
#
# sandbox: Docker 沙箱配置
#   - enabled: 是否启用沙箱
#     - false: 完全信任，无限制
#     - true: Docker 容器隔离
#   - image: Docker 镜像
#   - network: none（无网络）或 bridge（允许网络）
#   - memory: 内存限制
#   - pids_limit: 进程数限制
#   - mounts: 挂载配置
#       - host: 主机路径
#       - container: 容器内路径
#       - mode: ro（只读）或 rw（读写）

default_policy: "restricted"

agents:
  # 主 Agent：完全信任，不启用沙箱
  default:
    sandbox:
      enabled: false
  
  # 示例：开发 Agent（启用沙箱）
  # developer:
  #   sandbox:
  #     enabled: true
  #     mounts:
  #       - host: "~/projects/my-app"
  #         container: "/workspace"
  #         mode: "rw"
  #       - host: "/data/knowledge-base"
  #         container: "/knowledge"
  #         mode: "ro"
"""
        with open(PERMISSIONS_FILE, "w", encoding="utf-8") as f:
            f.write(config_content)
    
    def get_agent_config(self, agent_id: str) -> AgentConfig:
        """获取 Agent 配置"""
        if agent_id in self._config.agents:
            return self._config.agents[agent_id]
        return AgentConfig()
    
    def is_sandbox_enabled(self, agent_id: str) -> bool:
        """检查是否启用沙箱"""
        config = self.get_agent_config(agent_id)
        if config.sandbox:
            return config.sandbox.enabled
        # 默认：default agent 不启用，其他 agent 启用
        return agent_id != "default"
    
    def get_sandbox_config(self, agent_id: str) -> Optional[SandboxConfig]:
        """获取沙箱配置"""
        return self.get_agent_config(agent_id).sandbox
    
    def get_sandbox_mounts(self, agent_id: str) -> List[MountConfig]:
        """获取挂载配置"""
        config = self.get_agent_config(agent_id)
        if config.sandbox and config.sandbox.mounts:
            return config.sandbox.mounts
        return []


# 全局单例
config_manager = ConfigManager()


# ========== 便捷函数 ==========

def get_agent_config(agent_id: str) -> AgentConfig:
    """获取 Agent 配置"""
    return config_manager.get_agent_config(agent_id)


def is_sandbox_enabled(agent_id: str) -> bool:
    """检查是否启用沙箱"""
    return config_manager.is_sandbox_enabled(agent_id)


def get_sandbox_config(agent_id: str) -> Optional[SandboxConfig]:
    """获取沙箱配置"""
    return config_manager.get_sandbox_config(agent_id)


def get_sandbox_mounts(agent_id: str) -> List[MountConfig]:
    """获取挂载配置"""
    return config_manager.get_sandbox_mounts(agent_id)


# ========== 禁止挂载的敏感路径 ==========

BLOCKED_HOST_PATHS = [
    "/etc",
    "/root",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/run",
    "/var/run",
]


def validate_mount_path(host_path: str) -> bool:
    """验证挂载路径是否安全"""
    path = Path(host_path).expanduser().resolve()
    path_str = str(path)

    if "docker.sock" in path_str:
        raise ValueError(f"🚫 禁止挂载 Docker socket: {host_path}")
    
    for blocked in BLOCKED_HOST_PATHS:
        blocked_path = Path(blocked).resolve()
        if path == blocked_path or blocked_path in path.parents:
            raise ValueError(f"🚫 禁止挂载敏感路径: {host_path} (匹配 {blocked})")
    
    return True
