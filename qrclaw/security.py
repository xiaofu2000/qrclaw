"""
安全与权限控制模块 (Security & Permissions)

负责管理 Agent 的权限，实施沙箱隔离，防止越权访问。
配置存储在 ~/.qrclaw/permissions.yaml 中。
"""
import os
import yaml
from pathlib import Path
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

# 权限配置文件路径
PERMISSIONS_FILE = Path.home() / ".qrclaw" / "permissions.yaml"

class AgentPermission(BaseModel):
    """单个 Agent 的权限配置"""
    access: Literal["full", "scoped", "readonly"] = "scoped"
    allow_paths: List[str] = Field(default_factory=list)

class PermissionConfig(BaseModel):
    """全局权限配置"""
    default_policy: Literal["restricted", "full"] = "restricted"
    agents: dict[str, AgentPermission] = Field(default_factory=dict)

# 默认配置：Default Agent 拥有最高权限
DEFAULT_PERMISSIONS = {
    "default_policy": "restricted",
    "agents": {
        "default": {
            "access": "full",
            "allow_paths": []
        }
    }
}

class SecurityManager:
    _instance = None
    _config: PermissionConfig = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SecurityManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self):
        """加载权限配置，文件不存在则创建默认配置"""
        if not PERMISSIONS_FILE.exists():
            self._create_default_config()
        
        try:
            with open(PERMISSIONS_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                # 兼容性处理：确保 default agent 始终存在
                if "agents" not in data:
                    data["agents"] = {}
                if "default" not in data["agents"]:
                    data["agents"]["default"] = DEFAULT_PERMISSIONS["agents"]["default"]
                
                self._config = PermissionConfig(**data)
        except Exception as e:
            print(f"❌ 加载权限配置失败: {e}，将使用默认安全策略")
            self._config = PermissionConfig(**DEFAULT_PERMISSIONS)

    def _create_default_config(self):
        """创建默认的 permissions.yaml"""
        PERMISSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PERMISSIONS_FILE, "w", encoding="utf-8") as f:
            yaml.dump(DEFAULT_PERMISSIONS, f, default_flow_style=False, allow_unicode=True)
            # 添加注释
            f.write("\n# access: full (无限制) | scoped (仅限workspace+白名单) | readonly (只读)\n")
            f.write("# allow_paths: 允许访问的外部路径列表\n")

    def get_permission(self, agent_id: str) -> AgentPermission:
        """获取指定 Agent 的权限配置"""
        # 1. 优先查明确配置
        if agent_id in self._config.agents:
            return self._config.agents[agent_id]
        
        # 2. 回退到默认策略
        # 如果全局策略是 restricted，则新 Agent 默认为 scoped
        return AgentPermission(access="scoped")

    def check_access(self, agent_id: str, tool_name: str, args: dict, workspace_root: Path):
        """
        核心切面：检查工具调用是否合规
        
        Args:
            agent_id: 当前 Agent ID
            tool_name: 工具名称
            args: 工具参数
            workspace_root: 当前 Agent 的工作区根目录
        
        Raises:
            PermissionError: 如果权限不足
        """
        perm = self.get_permission(agent_id)

        # Level 1: Full Access (Root)
        if perm.access == "full":
            return  # 放行

        # Level 2: 检查文件路径
        # 针对涉及文件操作的工具进行拦截
        if tool_name in ["read_file", "write_file", "list_directory", "delete_file"]:
            path_str = args.get("path")
            if not path_str:
                return # 无路径参数，跳过
            
            self._check_path_safety(path_str, perm, workspace_root, tool_name)

        # Level 3: 检查 Shell 命令 (待扩展)
        if tool_name == "run_shell":
            command = args.get("command", "")
            # 简单防护：禁止 rm -rf / 等
            # 这里可以扩展更复杂的命令分析
            if "rm -rf /" in command:
                raise PermissionError(f"🚫 [Security] 禁止执行高危命令: {command}")

    def _check_path_safety(self, path_str: str, perm: AgentPermission, workspace_root: Path, tool_name: str):
        """检查路径是否在允许范围内"""
        try:
            target_path = Path(path_str).expanduser().resolve()
            ws_root = workspace_root.resolve()
        except Exception as e:
            raise PermissionError(f"🚫 [Security] 路径解析失败: {e}")

        # 1. 检查是否在 Workspace 内部
        if self._is_subpath(target_path, ws_root):
            return # 放行

        # 2. 检查白名单
        for allowed in perm.allow_paths:
            allowed_path = Path(allowed).expanduser().resolve()
            if self._is_subpath(target_path, allowed_path):
                # 如果是 readonly 权限，且试图写操作 -> 拦截
                if perm.access == "readonly" and tool_name in ["write_file", "delete_file"]:
                    raise PermissionError(f"🚫 [Security] 该路径只读，禁止写入: {path_str}")
                return # 放行

        # 3. 拦截
        raise PermissionError(f"🚫 [Security] Agent 无权访问 Workspace 外部路径: {path_str}\n(请在 permissions.yaml 中配置 allow_paths)")

    def _is_subpath(self, target: Path, parent: Path) -> bool:
        """判断 target 是否是 parent 的子路径"""
        try:
            target.relative_to(parent)
            return True
        except ValueError:
            return False

# 全局单例
security_manager = SecurityManager()
