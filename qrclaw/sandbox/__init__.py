"""
沙箱隔离模块

提供基于 Docker 的容器隔离，确保 Agent 只能访问指定的目录。
配置从 permissions.yaml 读取。
"""

from .config import (
    MountConfig,
    SandboxConfig,
    AgentConfig,
    config_manager,
    get_agent_config,
    is_sandbox_enabled,
    get_sandbox_config,
    get_sandbox_mounts,
    is_full_access,
    BLOCKED_HOST_PATHS,
)
from .validator import validate_mount_path, PathValidationError
from .manager import (
    SandboxManager,
    SandboxHandle,
    sandbox_manager,
    create_sandbox,
    exec_in_sandbox,
    destroy_sandbox,
)
from .container import ExecResult, ContainerError

__all__ = [
    # 配置
    "MountConfig",
    "SandboxConfig",
    "AgentConfig",
    "config_manager",
    "get_agent_config",
    "is_sandbox_enabled",
    "get_sandbox_config",
    "get_sandbox_mounts",
    "is_full_access",
    "BLOCKED_HOST_PATHS",
    # 验证
    "validate_mount_path",
    "PathValidationError",
    # 沙箱管理
    "SandboxManager",
    "SandboxHandle",
    "sandbox_manager",
    "create_sandbox",
    "exec_in_sandbox",
    "destroy_sandbox",
    # 容器
    "ExecResult",
    "ContainerError",
]