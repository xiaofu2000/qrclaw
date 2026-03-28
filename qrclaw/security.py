"""
安全与权限控制模块 (Security & Permissions)

负责管理 Agent 的权限，实施沙箱隔离，防止越权访问。
配置存储在 ~/.qrclaw/permissions.yaml 中。

权限继承规则：
- 子 agent 继承父 agent 的权限
- 这样子 agent 可以访问父 agent 能访问的路径
"""
import os
import re
import yaml
from pathlib import Path
from typing import List, Literal, Optional
from pydantic import BaseModel, Field

# 权限配置文件路径
PERMISSIONS_FILE = Path.home() / ".qrclaw" / "permissions.yaml"

# 系统保护路径：只有 default agent 可访问
PROTECTED_PATHS = [
    Path.home() / ".qrclaw" / "permissions.yaml",
    Path.home() / ".qrclaw" / "config.yaml",
]

# 权限拒绝提示后缀
PERMISSION_DENIED_SUFFIX = "\n\n⚠️ 你的上级不允许操作除了你工作空间以外的任何文件，请立即停止！"

# LLM 安全检查的 prompt
SHELL_SAFETY_PROMPT = """你是一个命令行安全审查员。判断以下 shell 命令是否安全。

安全规则：
1. 命令只能操作当前工作目录内的文件
2. 不能访问、读取、修改工作目录外的任何文件或目录
3. 不能使用绝对路径（如 /etc/passwd, ~/.ssh/id_rsa）
4. 不能使用管道或重定向访问外部文件
5. 不能执行网络操作（curl, wget, nc 等）
6. 不能修改系统配置或环境变量
7. 不能执行提权操作（sudo, su, chmod 777 等）

工作目录: {workspace}

命令:
{command}

只返回一个词: true 或 false
true = 安全，允许执行
false = 危险，拒绝执行

不要解释，只返回 true 或 false。"""


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


# 高危命令黑名单（正则匹配）
DANGEROUS_COMMANDS = [
    r"rm\s+-rf\s+/",
    r"rm\s+-rf\s+~",
    r"rm\s+-rf\s+\*",
    r">\s*/dev/sd[a-z]",
    r"mkfs\s+",
    r"dd\s+if=.*of=/dev/",
    r":(){ :|:& };:",  # fork bomb
    r"chmod\s+777\s+/",
    r"chown\s+.*\s+/",
]


def _llm_check_command_safety(command: str, workspace: Path) -> bool:
    """
    使用 LLM 判断命令是否安全
    
    Args:
        command: 要检查的命令
        workspace: 工作目录路径
    
    Returns:
        True = 安全，False = 危险
    """
    try:
        from qrclaw.providers import provider
        from qrclaw.logger import get_logger
        
        logger = get_logger("qrclaw.security")
        
        prompt = SHELL_SAFETY_PROMPT.format(
            workspace=str(workspace),
            command=command
        )
        
        logger.debug(f"LLM 安全检查: {command[:100]}...")
        
        response = provider.chat([{"role": "user", "content": prompt}])
        result = response.content.strip().lower()
        
        logger.info(f"LLM 安全检查结果: {result}")
        
        return result == "true"
    
    except Exception as e:
        # LLM 调用失败时，默认拒绝（安全优先）
        from qrclaw.logger import get_logger
        logger = get_logger("qrclaw.security")
        logger.error(f"LLM 安全检查失败: {e}")
        return False


def _get_effective_agent_id(agent_id: str) -> str:
    """
    获取有效的 agent ID（用于权限继承）
    
    子 agent 会继承父 agent 的权限。
    例如：如果 sub-agent "coder" 的父 agent 是 "default"，
    那么 "coder" 会使用 "default" 的权限配置。
    
    Args:
        agent_id: 当前 agent ID（可能是子 agent）
    
    Returns:
        str: 用于权限查找的有效 agent ID
    """
    # 尝试导入 spawn_agent 模块获取父 agent 映射
    try:
        from qrclaw.tools.spawn_agent import get_parent_agent_id
        parent_id = get_parent_agent_id(agent_id)
        if parent_id:
            # 递归查找，直到找到没有父 agent 的顶层 agent
            return _get_effective_agent_id(parent_id)
    except ImportError:
        pass
    
    # 没有父 agent，返回自身
    return agent_id


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
        """
        获取指定 Agent 的权限配置（支持子 agent 权限继承）
        
        子 agent 会继承父 agent 的权限。
        例如：如果 "coder" 是 "default" 的子 agent，
        那么 "coder" 会使用 "default" 的权限配置。
        """
        # 获取有效的 agent ID（可能从父 agent 继承）
        effective_id = _get_effective_agent_id(agent_id)
        
        if effective_id != agent_id:
            from qrclaw.logger import get_logger
            logger = get_logger("qrclaw.security")
            logger.debug(f"子 agent '{agent_id}' 继承父 agent '{effective_id}' 的权限")

        # 1. 优先查明确配置
        if effective_id in self._config.agents:
            return self._config.agents[effective_id]

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
                return  # 无路径参数，跳过

            self._check_path_safety(path_str, perm, workspace_root, tool_name, agent_id)

        # Level 3: 检查 Shell 命令
        if tool_name == "run_shell":
            command = args.get("command", "")
            self._check_shell_safety(command, perm, workspace_root, agent_id)

    def _check_path_safety(self, path_str: str, perm: AgentPermission, workspace_root: Path, tool_name: str, agent_id: str):
        """检查路径是否在允许范围内"""
        try:
            target_path = Path(path_str).expanduser().resolve()
            ws_root = workspace_root.resolve()
        except Exception as e:
            raise PermissionError(f"🚫 [Security] 路径解析失败: {e}{PERMISSION_DENIED_SUFFIX}")

        # 检查保护路径（只有 default agent 可访问）
        for protected in PROTECTED_PATHS:
            if target_path == protected.resolve():
                if agent_id != "default":
                    raise PermissionError(f"🚫 [Security] 系统配置文件只有 default agent 可访问: {path_str}{PERMISSION_DENIED_SUFFIX}")

        # 1. 检查是否在 Workspace 内部
        if self._is_subpath(target_path, ws_root):
            return  # 放行

        # 2. 检查白名单
        for allowed in perm.allow_paths:
            allowed_path = Path(allowed).expanduser().resolve()
            if self._is_subpath(target_path, allowed_path):
                # 如果是 readonly 权限，且试图写操作 -> 拦截
                if perm.access == "readonly" and tool_name in ["write_file", "delete_file"]:
                    raise PermissionError(f"🚫 [Security] 该路径只读，禁止写入: {path_str}{PERMISSION_DENIED_SUFFIX}")
                return  # 放行

        # 3. 拦截
        raise PermissionError(f"🚫 [Security] Agent 无权访问 Workspace 外部路径: {path_str}{PERMISSION_DENIED_SUFFIX}")

    def _check_shell_safety(self, command: str, perm: AgentPermission, workspace_root: Path, agent_id: str):
        """
        检查 Shell 命令安全性
        
        对于 scoped 权限的 agent：
        1. 禁止高危命令（rm -rf /, fork bomb 等）- 规则匹配
        2. 使用 LLM 判断命令是否试图访问工作目录外的文件
        """
        # 检查高危命令（规则匹配，快速拦截）
        for pattern in DANGEROUS_COMMANDS:
            if re.search(pattern, command, re.IGNORECASE):
                raise PermissionError(f"🚫 [Security] 禁止执行高危命令: {command}{PERMISSION_DENIED_SUFFIX}")

        # 使用 LLM 判断命令安全性
        if not _llm_check_command_safety(command, workspace_root):
            raise PermissionError(f"🚫 [Security] LLM 判断命令存在安全风险，拒绝执行: {command}{PERMISSION_DENIED_SUFFIX}")

    def _is_subpath(self, target: Path, parent: Path) -> bool:
        """判断 target 是否是 parent 的子路径"""
        try:
            target.relative_to(parent)
            return True
        except ValueError:
            return False


# 全局单例
security_manager = SecurityManager()