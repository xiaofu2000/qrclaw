"""
沙箱模块测试

测试沙箱配置、验证器、容器管理等核心功能。
"""
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from qrclaw.sandbox.config import (
    MountConfig,
    SandboxConfig,
    AgentConfig,
    BLOCKED_HOST_PATHS,
    validate_mount_path,
)
from qrclaw.sandbox.validator import (
    get_container_path,
    PathValidationError,
)


class TestMountConfig:
    """测试挂载配置"""
    
    def test_valid_mount(self):
        """测试有效的挂载配置"""
        mount = MountConfig(
            host="~/projects/my-app",
            container="/workspace",
            mode="rw"
        )
        assert mount.host == "~/projects/my-app"
        assert mount.container == "/workspace"
        assert mount.mode == "rw"
    
    def test_to_docker_bind(self):
        """测试转换为 Docker 挂载格式"""
        mount = MountConfig(
            host="/host/path",
            container="/container/path",
            mode="ro"
        )
        bind = mount.to_docker_bind()
        assert bind == "/host/path:/container/path:ro"


class TestSandboxConfig:
    """测试沙箱配置"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = SandboxConfig()
        assert config.enabled is True
        assert config.image == "python:3.11-slim"
        assert config.network == "none"
        assert config.memory == "512m"
        assert config.pids_limit == 256
    
    def test_custom_config(self):
        """测试自定义配置"""
        config = SandboxConfig(
            enabled=False,
            network="bridge",
            memory="1g",
        )
        assert config.enabled is False
        assert config.network == "bridge"
        assert config.memory == "1g"


class TestAgentConfig:
    """测试 Agent 配置"""
    
    def test_default_config(self):
        """测试默认配置"""
        config = AgentConfig()
        assert config.sandbox is None
    
    def test_with_sandbox(self):
        """测试带沙箱配置"""
        config = AgentConfig(
            sandbox=SandboxConfig(
                enabled=True,
                mounts=[
                    MountConfig(host="~/app", container="/workspace", mode="rw")
                ]
            )
        )
        assert config.sandbox is not None
        assert config.sandbox.enabled is True
        assert len(config.sandbox.mounts) == 1


class TestValidateMountPath:
    """测试挂载路径验证"""
    
    def test_valid_path(self, tmp_path):
        """测试有效路径"""
        path = str(tmp_path / "test")
        assert validate_mount_path(path) is True
    
    def test_blocked_path(self):
        """测试禁止挂载的敏感路径"""
        for blocked in BLOCKED_HOST_PATHS:
            with pytest.raises(ValueError, match="禁止挂载敏感路径"):
                validate_mount_path(blocked)
    
    def test_docker_sock_blocked(self):
        """测试禁止挂载 Docker socket"""
        with pytest.raises(ValueError, match="禁止挂载 Docker socket"):
            validate_mount_path("/var/run/docker.sock")


class TestGetContainerPath:
    """测试获取容器内路径"""
    
    def test_get_container_path(self):
        """测试获取容器内路径"""
        mounts = [
            MountConfig(host="/host/workspace", container="/workspace", mode="rw"),
            MountConfig(host="/host/data", container="/data", mode="ro"),
        ]
        
        # 工作空间内的文件
        container_path = get_container_path("/host/workspace/file.txt", mounts)
        assert container_path == "/workspace/file.txt"
        
        # 数据目录内的文件
        container_path = get_container_path("/host/data/subdir/file.txt", mounts)
        assert container_path == "/data/subdir/file.txt"
        
        # 不在挂载范围内
        container_path = get_container_path("/other/path", mounts)
        assert container_path is None


class TestContainerManager:
    """测试容器管理器"""
    
    @patch("subprocess.run")
    def test_check_docker(self, mock_run):
        """测试 Docker 检查"""
        from qrclaw.sandbox.container import ContainerManager
        
        mock_run.return_value = MagicMock(returncode=0, stdout="Docker version 24.0.0")
        
        manager = ContainerManager()
        assert manager is not None
    
    @patch("subprocess.run")
    def test_docker_not_installed(self, mock_run):
        """测试 Docker 未安装"""
        from qrclaw.sandbox.container import ContainerManager, ContainerError
        
        mock_run.side_effect = FileNotFoundError()
        
        with pytest.raises(ContainerError, match="Docker 未安装"):
            ContainerManager()


class TestConfigManager:
    """测试配置管理器"""
    
    def test_get_agent_config_default(self):
        """测试获取默认配置"""
        from qrclaw.sandbox.config import config_manager
        
        config = config_manager.get_agent_config("non-existent-agent")
        assert config.sandbox is None
    
    def test_is_sandbox_enabled_default(self):
        """测试默认沙箱状态"""
        from qrclaw.sandbox.config import config_manager
        
        # default agent 默认不启用沙箱
        # 其他 agent 默认启用沙箱
        assert config_manager.is_sandbox_enabled("default") is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])