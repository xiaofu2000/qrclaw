"""
安全模块测试

测试覆盖：
- 权限配置加载
- 路径访问检查
- 命令安全检查
- 权限继承
"""
import pytest
from pathlib import Path
from qrclaw.security import (
    SecurityManager,
    AgentPermission,
    PermissionConfig,
    _get_effective_agent_id,
    DANGEROUS_COMMANDS,
    PROTECTED_PATHS,
    PERMISSION_DENIED_SUFFIX,
)


class TestPermissionModel:
    """测试权限模型"""

    def test_agent_permission_defaults(self):
        """测试默认权限配置"""
        perm = AgentPermission()
        
        assert perm.access == "scoped"
        assert perm.allow_paths == []

    def test_agent_permission_full(self):
        """测试完全访问权限"""
        perm = AgentPermission(access="full")
        
        assert perm.access == "full"

    def test_permission_config_defaults(self):
        """测试默认全局配置"""
        config = PermissionConfig()
        
        assert config.default_policy == "restricted"
        assert config.agents == {}


class TestSecurityManager:
    """测试安全管理器"""

    def test_singleton_pattern(self):
        """测试单例模式"""
        manager1 = SecurityManager()
        manager2 = SecurityManager()
        
        assert manager1 is manager2

    def test_get_permission_default_agent(self):
        """测试获取 default agent 权限"""
        manager = SecurityManager()
        perm = manager.get_permission("default")
        
        # default agent 应该有 full access
        assert perm.access == "full"

    def test_get_permission_unknown_agent(self):
        """测试获取未知 agent 权限"""
        manager = SecurityManager()
        perm = manager.get_permission("unknown_agent")
        
        # 未知 agent 应该使用默认策略
        assert perm.access == "scoped"


class TestPathSafety:
    """测试路径安全检查"""

    def test_check_path_in_workspace(self, temp_dir):
        """测试工作区内的路径"""
        manager = SecurityManager()
        
        # scoped agent 访问 workspace 内的文件应该放行
        manager._check_path_safety(
            path_str=str(temp_dir / "test.txt"),
            perm=AgentPermission(access="scoped"),
            workspace_root=temp_dir,
            tool_name="read_file",
            agent_id="test_agent"
        )
        # 不抛异常 = 通过

    def test_check_path_outside_workspace(self, temp_dir):
        """测试工作区外的路径"""
        manager = SecurityManager()
        
        # scoped agent 访问 workspace 外的文件应该拒绝
        with pytest.raises(PermissionError) as exc_info:
            manager._check_path_safety(
                path_str="/tmp/outside.txt",
                perm=AgentPermission(access="scoped"),
                workspace_root=temp_dir,
                tool_name="read_file",
                agent_id="test_agent"
            )
        
        assert "无权访问" in str(exc_info.value)

    def test_check_path_whitelist(self, temp_dir):
        """测试白名单路径"""
        manager = SecurityManager()
        
        # 允许访问 /tmp
        perm = AgentPermission(
            access="scoped",
            allow_paths=["/tmp"]
        )
        
        # 应该放行
        manager._check_path_safety(
            path_str="/tmp/allowed.txt",
            perm=perm,
            workspace_root=temp_dir,
            tool_name="read_file",
            agent_id="test_agent"
        )

    def test_check_path_readonly_write_outside(self, temp_dir):
        """测试只读权限在白名单外写入"""
        manager = SecurityManager()
        
        # 白名单路径
        perm = AgentPermission(access="readonly", allow_paths=["/tmp"])
        
        # 只读权限尝试写入白名单路径应该拒绝
        with pytest.raises(PermissionError) as exc_info:
            manager._check_path_safety(
                path_str="/tmp/test.txt",
                perm=perm,
                workspace_root=temp_dir,
                tool_name="write_file",
                agent_id="test_agent"
            )
        
        assert "只读" in str(exc_info.value)

    def test_check_path_readonly_read(self, temp_dir):
        """测试只读权限读取"""
        manager = SecurityManager()
        
        perm = AgentPermission(access="readonly")
        
        # 只读权限读取应该放行
        manager._check_path_safety(
            path_str=str(temp_dir / "test.txt"),
            perm=perm,
            workspace_root=temp_dir,
            tool_name="read_file",
            agent_id="test_agent"
        )

    def test_check_protected_path(self, temp_dir):
        """测试系统保护路径"""
        manager = SecurityManager()
        
        # 保护路径（如 permissions.yaml）
        protected_path = str(PROTECTED_PATHS[0])
        
        # 非 default agent 应该拒绝
        with pytest.raises(PermissionError) as exc_info:
            manager._check_path_safety(
                path_str=protected_path,
                perm=AgentPermission(access="full"),
                workspace_root=temp_dir,
                tool_name="read_file",
                agent_id="not_default"
            )
        
        assert "系统配置文件" in str(exc_info.value)


class TestShellSafety:
    """测试 Shell 命令安全检查"""

    def test_dangerous_commands_patterns(self):
        """测试高危命令正则"""
        # 测试一些高危命令
        dangerous_examples = [
            "rm -rf /",
            "rm -rf ~",
            "rm -rf *",
            "mkfs /dev/sda1",
            "chmod 777 /",
        ]
        
        import re
        for cmd in dangerous_examples:
            # 至少匹配一个高危模式
            matches = [re.search(pattern, cmd, re.IGNORECASE) for pattern in DANGEROUS_COMMANDS]
            assert any(matches), f"命令 '{cmd}' 应该被识别为高危"

    def test_check_shell_safety_dangerous_command(self, temp_dir):
        """测试高危命令检查"""
        manager = SecurityManager()
        
        # 高危命令应该直接拒绝
        with pytest.raises(PermissionError) as exc_info:
            manager._check_shell_safety(
                command="rm -rf /",
                perm=AgentPermission(access="scoped"),
                workspace_root=temp_dir,
                agent_id="test_agent"
            )
        
        assert "高危命令" in str(exc_info.value)


class TestPermissionInheritance:
    """测试权限继承"""

    def test_get_effective_agent_id_no_parent(self):
        """测试没有父 agent 的情况"""
        # 清空父 agent 映射
        from qrclaw.tools import spawn_agent
        spawn_agent._parent_agent_map.clear()
        
        result = _get_effective_agent_id("standalone_agent")
        
        assert result == "standalone_agent"

    def test_get_effective_agent_id_with_parent(self):
        """测试有父 agent 的情况"""
        from qrclaw.tools.spawn_agent import set_parent_agent, clear_parent_agent
        
        # 设置父子关系
        set_parent_agent("child_agent", "parent_agent")
        
        # 应该返回父 agent
        result = _get_effective_agent_id("child_agent")
        
        # 清理
        clear_parent_agent("child_agent")
        
        # 由于递归查找，最终应该返回 parent_agent
        # 但如果 parent_agent 没有父 agent，就返回 parent_agent
        assert result == "parent_agent"


class TestAccessCheck:
    """测试完整访问检查"""

    def test_check_access_full_permission(self, temp_dir):
        """测试完全权限"""
        manager = SecurityManager()
        
        # full access 应该放行所有操作
        manager.check_access(
            agent_id="default",
            tool_name="read_file",
            args={"path": "/any/path"},
            workspace_root=temp_dir
        )

    def test_check_access_scoped_in_workspace(self, temp_dir):
        """测试受限权限在 workspace 内"""
        manager = SecurityManager()
        
        # scoped 在 workspace 内应该放行
        manager.check_access(
            agent_id="test_agent",
            tool_name="read_file",
            args={"path": str(temp_dir / "test.txt")},
            workspace_root=temp_dir
        )

    def test_check_access_scoped_outside_workspace(self, temp_dir):
        """测试受限权限在 workspace 外"""
        manager = SecurityManager()
        
        # scoped 在 workspace 外应该拒绝
        with pytest.raises(PermissionError):
            manager.check_access(
                agent_id="test_agent",
                tool_name="read_file",
                args={"path": "/tmp/outside.txt"},
                workspace_root=temp_dir
            )

    def test_check_access_no_path_arg(self, temp_dir):
        """测试无路径参数的工具"""
        manager = SecurityManager()
        
        # 无路径参数的工具应该跳过路径检查
        manager.check_access(
            agent_id="test_agent",
            tool_name="some_tool",
            args={},
            workspace_root=temp_dir
        )