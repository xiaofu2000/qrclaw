"""
路径验证器

验证挂载路径的安全性，防止敏感路径被挂载到容器中。
"""
from pathlib import Path
from typing import Optional

from qrclaw.logger import get_logger
from .config import BLOCKED_HOST_PATHS

logger = get_logger("qrclaw.sandbox.validator")


class PathValidationError(Exception):
    """路径验证异常"""
    pass


def validate_mount_path(host_path: str) -> bool:
    """
    验证挂载路径是否安全
    
    Args:
        host_path: 主机路径
        
    Returns:
        是否安全
        
    Raises:
        PathValidationError: 路径不安全
    """
    try:
        path = Path(host_path).expanduser().resolve()
        path_str = str(path)
        
        # 检查黑名单
        for blocked in BLOCKED_HOST_PATHS:
            if path_str.startswith(blocked):
                raise PathValidationError(
                    f"🚫 [Sandbox] 禁止挂载敏感路径: {host_path}\n"
                    f"   匹配规则: {blocked}"
                )
        
        # 检查 Docker socket
        if "docker.sock" in path_str:
            raise PathValidationError(
                f"🚫 [Sandbox] 禁止挂载 Docker socket: {host_path}\n"
                f"   这将导致容器逃逸风险"
            )
        
        logger.debug(f"挂载路径验证通过: {host_path}")
        return True
        
    except Exception as e:
        if isinstance(e, PathValidationError):
            raise
        raise PathValidationError(f"🚫 [Sandbox] 路径验证失败: {host_path} - {e}")


def get_container_path(host_path: str, mounts: list) -> Optional[str]:
    """
    根据挂载配置，获取主机路径对应的容器内路径
    
    Args:
        host_path: 主机路径
        mounts: 挂载配置列表 (list[MountConfig])
        
    Returns:
        容器内路径，如果不在挂载范围内则返回 None
    """
    from .config import MountConfig
    
    host_resolved = Path(host_path).expanduser().resolve()
    
    for mount in mounts:
        mount_host = Path(mount.host).expanduser().resolve()
        
        try:
            relative = host_resolved.relative_to(mount_host)
            return str(Path(mount.container) / relative)
        except ValueError:
            continue
    
    return None