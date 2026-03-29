"""
容器操作层

直接与 Docker 交互，提供容器的创建、执行、销毁等操作。
"""
import subprocess
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from qrclaw.logger import get_logger
from .config import BLOCKED_HOST_PATHS, MountConfig

logger = get_logger("qrclaw.sandbox.container")


@dataclass
class ContainerInfo:
    """容器信息"""
    container_id: str
    container_name: str
    status: str
    image: str


@dataclass
class ExecResult:
    """命令执行结果"""
    exit_code: int
    stdout: str
    stderr: str
    duration: float


class ContainerError(Exception):
    """容器操作异常"""
    pass


class ContainerConfig:
    """容器配置"""
    def __init__(
        self,
        image: str = "python:3.11-slim",
        network: str = "none",
        memory: str = "512m",
        pids_limit: int = 256,
        cap_drop: list = None,
        tmpfs: list = None,
        env: dict = None,
    ):
        self.image = image
        self.network = network
        self.memory = memory
        self.pids_limit = pids_limit
        self.cap_drop = cap_drop or ["ALL"]
        self.tmpfs = tmpfs or ["/tmp", "/var/tmp", "/run"]
        self.env = env or {"LANG": "C.UTF-8"}


class ContainerManager:
    """容器管理器"""
    
    def __init__(self):
        self._check_docker()
    
    def _check_docker(self):
        """检查 Docker 是否可用"""
        try:
            result = subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                raise ContainerError("Docker 未正确安装或未运行")
            logger.debug(f"Docker 版本: {result.stdout.strip()}")
        except FileNotFoundError:
            raise ContainerError("Docker 未安装，请先安装 Docker")
        except subprocess.TimeoutExpired:
            raise ContainerError("Docker 检查超时")
    
    def create(
        self,
        name: str,
        workspace: Path,
        mounts: list[MountConfig],
        config: ContainerConfig,
    ) -> str:
        """创建容器"""
        args = ["docker", "create", "--name", name, "--read-only"]
        
        # 安全选项
        for cap in config.cap_drop:
            args.extend(["--cap-drop", cap])
        args.extend(["--security-opt", "no-new-privileges"])
        
        # 资源限制
        args.extend(["--memory", config.memory])
        args.extend(["--pids-limit", str(config.pids_limit)])
        args.extend(["--network", config.network])
        
        # tmpfs
        for tmpfs in config.tmpfs:
            args.extend(["--tmpfs", tmpfs])
        
        # 环境变量
        for key, value in config.env.items():
            args.extend(["-e", f"{key}={value}"])
        
        # 工作空间挂载
        workspace_str = str(workspace.expanduser().resolve())
        if not Path(workspace_str).exists():
            Path(workspace_str).mkdir(parents=True, exist_ok=True)
        args.extend(["-v", f"{workspace_str}:/workspace:rw", "-w", "/workspace"])
        
        # 额外挂载
        for mount in mounts:
            host_path = str(Path(mount.host).expanduser().resolve())
            self._validate_mount_path(host_path)
            if not Path(host_path).exists():
                logger.warning(f"挂载路径不存在: {host_path}")
                Path(host_path).mkdir(parents=True, exist_ok=True)
            args.extend(["-v", mount.to_docker_bind()])
        
        # 标签
        args.extend(["--label", "qrclaw.sandbox=1"])
        
        # 镜像和命令
        args.append(config.image)
        args.extend(["sleep", "infinity"])
        
        logger.info(f"创建容器: {name}")
        
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise ContainerError(f"创建容器失败: {result.stderr}")
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            raise ContainerError("创建容器超时")
    
    def start(self, container_id: str) -> bool:
        """启动容器"""
        try:
            result = subprocess.run(
                ["docker", "start", container_id],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                raise ContainerError(f"启动容器失败: {result.stderr}")
            logger.info(f"容器已启动: {container_id[:12]}")
            return True
        except subprocess.TimeoutExpired:
            raise ContainerError("启动容器超时")
    
    def exec(
        self,
        container_id: str,
        command: str,
        cwd: str = "/workspace",
        timeout: int = 300,
        env: Optional[dict] = None,
    ) -> ExecResult:
        """在容器中执行命令"""
        args = ["docker", "exec", "-i", "-w", cwd]
        
        if env:
            for key, value in env.items():
                args.extend(["-e", f"{key}={value}"])
        
        args.extend([container_id, "sh", "-c", command])
        
        start_time = time.time()
        
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return ExecResult(
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration=time.time() - start_time,
            )
        except subprocess.TimeoutExpired:
            raise ContainerError(f"命令执行超时（{timeout}s）")
    
    def stop(self, container_id: str, timeout: int = 10) -> bool:
        """停止容器"""
        try:
            result = subprocess.run(
                ["docker", "stop", "-t", str(timeout), container_id],
                capture_output=True, text=True, timeout=timeout + 10
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def remove(self, container_id: str, force: bool = False) -> bool:
        """删除容器"""
        try:
            args = ["docker", "rm"]
            if force:
                args.append("-f")
            args.append(container_id)
            result = subprocess.run(args, capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception:
            return False
    
    def exists(self, container_id: str) -> bool:
        """检查容器是否存在"""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", container_id],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False
    
    def is_running(self, container_id: str) -> bool:
        """检查容器是否运行"""
        try:
            result = subprocess.run(
                ["docker", "inspect", "--format", "{{.State.Status}}", container_id],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0 and result.stdout.strip() == "running"
        except Exception:
            return False
    
    def cleanup_exited(self) -> int:
        """清理已退出的沙箱容器"""
        try:
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "label=qrclaw.sandbox=1",
                 "--filter", "status=exited", "--filter", "status=dead",
                 "--format", "{{.ID}}"],
                capture_output=True, text=True, timeout=10
            )
            ids = result.stdout.strip().split("\n")
            cleaned = 0
            for cid in ids:
                if cid and self.remove(cid):
                    cleaned += 1
            return cleaned
        except Exception:
            return 0
    
    def _validate_mount_path(self, host_path: str):
        """验证挂载路径"""
        path = Path(host_path).resolve()
        for blocked in BLOCKED_HOST_PATHS:
            if str(path).startswith(blocked):
                raise ContainerError(f"禁止挂载敏感路径: {host_path}")