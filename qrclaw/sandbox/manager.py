"""
沙箱管理器

提供高层 API，管理 Agent 沙箱的完整生命周期。
"""
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from datetime import datetime

from qrclaw.logger import get_logger
from qrclaw.workspace import Workspace
from .config import (
    SandboxConfig,
    MountConfig,
    get_sandbox_config,
    get_sandbox_mounts,
    is_sandbox_enabled,
    validate_mount_path,
)
from .container import ContainerManager, ContainerError, ExecResult

logger = get_logger("qrclaw.sandbox.manager")


@dataclass
class SandboxHandle:
    """沙箱句柄"""
    agent_id: str
    container_id: str
    container_name: str
    workspace: Path
    mounts: list
    config: SandboxConfig
    created_at: datetime = field(default_factory=datetime.now)
    _started: bool = False
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        sandbox_manager.destroy_sandbox(self.agent_id)


class SandboxManager:
    """沙箱管理器（单例）"""
    
    _instance = None
    _containers: dict[str, SandboxHandle] = {}
    _container_mgr: ContainerManager = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SandboxManager, cls).__new__(cls)
        return cls._instance
    
    def _get_container_mgr(self) -> ContainerManager:
        """延迟初始化 ContainerManager"""
        if self._container_mgr is None:
            self._container_mgr = ContainerManager()
        return self._container_mgr
    
    def create_sandbox(
        self,
        agent_id: str,
        workspace: Optional[Path] = None,
        mounts: Optional[list[MountConfig]] = None,
        config: Optional[SandboxConfig] = None,
    ) -> SandboxHandle:
        """创建沙箱"""
        # 如果已存在，先销毁
        if agent_id in self._containers:
            logger.warning(f"沙箱已存在，将重新创建: {agent_id}")
            self.destroy_sandbox(agent_id)
        
        # 获取配置
        if config is None:
            config = get_sandbox_config(agent_id)
        
        if config is None:
            config = SandboxConfig()
        
        # 检查是否启用沙箱
        if not config.enabled:
            logger.info(f"Agent {agent_id} 未启用沙箱，使用直接执行模式")
            return self._create_noop_handle(agent_id, workspace)
        
        # 获取工作空间
        if workspace is None:
            workspace = Workspace(agent_id).root
        
        # 合并挂载配置
        effective_mounts = list(config.mounts) if config.mounts else []
        config_mounts = get_sandbox_mounts(agent_id)
        if config_mounts:
            effective_mounts.extend(config_mounts)
        if mounts:
            effective_mounts.extend(mounts)
        
        # 验证挂载路径
        for mount in effective_mounts:
            try:
                validate_mount_path(mount.host)
            except ValueError as e:
                logger.error(f"挂载路径验证失败: {e}")
                raise
        
        # 容器名称
        container_name = f"qrclaw-sbx-{agent_id}"
        
        # 创建容器（此时才初始化 ContainerManager）
        try:
            container_mgr = self._get_container_mgr()
            container_id = container_mgr.create(
                name=container_name,
                workspace=workspace,
                mounts=effective_mounts,
                image=config.image,
                network=config.network,
                memory=config.memory,
                pids_limit=config.pids_limit,
            )
            
            handle = SandboxHandle(
                agent_id=agent_id,
                container_id=container_id,
                container_name=container_name,
                workspace=workspace,
                mounts=effective_mounts,
                config=config,
            )
            
            # 启动容器
            container_mgr.start(container_id)
            handle._started = True
            
            # 缓存
            self._containers[agent_id] = handle
            
            logger.info(f"沙箱创建成功: {agent_id} (容器: {container_id[:12]})")
            return handle
            
        except ContainerError as e:
            logger.error(f"创建沙箱失败: {e}")
            raise
    
    def _create_noop_handle(self, agent_id: str, workspace: Optional[Path]) -> SandboxHandle:
        """创建无操作句柄（沙箱未启用时使用）"""
        if workspace is None:
            workspace = Workspace(agent_id).root
        
        return SandboxHandle(
            agent_id=agent_id,
            container_id="",
            container_name="",
            workspace=workspace,
            mounts=[],
            config=SandboxConfig(enabled=False),
            _started=True,
        )
    
    def exec(
        self,
        agent_id: str,
        command: str,
        cwd: str = "/workspace",
        timeout: int = 300,
        env: Optional[dict] = None,
    ) -> ExecResult:
        """在沙箱中执行命令"""
        handle = self._containers.get(agent_id)
        
        # 如果没有沙箱，直接执行
        if not handle or not handle.container_id:
            return self._exec_direct(command, cwd, timeout, env)
        
        # 检查容器是否运行
        container_mgr = self._get_container_mgr()
        if not container_mgr.is_running(handle.container_id):
            container_mgr.start(handle.container_id)
        
        return container_mgr.exec(
            container_id=handle.container_id,
            command=command,
            cwd=cwd,
            timeout=timeout,
            env=env,
        )
    
    def _exec_direct(self, command: str, cwd: str, timeout: int, env: Optional[dict]) -> ExecResult:
        """直接在主机执行命令"""
        import subprocess
        import time
        import os
        
        exec_env = os.environ.copy()
        if env:
            exec_env.update(env)
        
        start_time = time.time()
        
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd if os.path.exists(cwd) else None,
                env=exec_env,
            )
            
            return ExecResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=time.time() - start_time,
            )
            
        except subprocess.TimeoutExpired:
            raise ContainerError(f"命令执行超时（{timeout}s）: {command[:100]}")
    
    def destroy_sandbox(self, agent_id: str, force: bool = False) -> bool:
        """销毁沙箱"""
        handle = self._containers.get(agent_id)
        
        if not handle:
            return True
        
        if not handle.container_id:
            del self._containers[agent_id]
            return True
        
        try:
            container_mgr = self._get_container_mgr()
            container_mgr.stop(handle.container_id)
            container_mgr.remove(handle.container_id, force=force)
            del self._containers[agent_id]
            logger.info(f"沙箱已销毁: {agent_id}")
            return True
        except Exception as e:
            logger.error(f"销毁沙箱失败: {e}")
            return False
    
    def get_sandbox(self, agent_id: str) -> Optional[SandboxHandle]:
        """获取沙箱句柄"""
        return self._containers.get(agent_id)
    
    def has_sandbox(self, agent_id: str) -> bool:
        """检查沙箱是否存在"""
        return agent_id in self._containers
    
    def list_sandboxes(self) -> list[str]:
        """列出所有沙箱"""
        return list(self._containers.keys())
    
    def cleanup(self) -> int:
        """清理已退出的容器"""
        if self._container_mgr is None:
            return 0
        cleaned = self._container_mgr.cleanup_exited()
        to_remove = [
            aid for aid, h in self._containers.items()
            if h.container_id and not self._container_mgr.exists(h.container_id)
        ]
        for aid in to_remove:
            del self._containers[aid]
        return cleaned


# 全局单例
sandbox_manager = SandboxManager()


# ========== 便捷函数 ==========

def create_sandbox(agent_id: str, **kwargs) -> SandboxHandle:
    """创建沙箱"""
    return sandbox_manager.create_sandbox(agent_id, **kwargs)


def exec_in_sandbox(agent_id: str, command: str, **kwargs) -> ExecResult:
    """在沙箱中执行命令"""
    return sandbox_manager.exec(agent_id, command, **kwargs)


def destroy_sandbox(agent_id: str, **kwargs) -> bool:
    """销毁沙箱"""
    return sandbox_manager.destroy_sandbox(agent_id, **kwargs)