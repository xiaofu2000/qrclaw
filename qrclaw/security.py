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
SHELL_SAFETY_PROMPT = """你是安全审查员。判断 shell 命令是否安全。

工作目录: {workspace}
命令会自动在这个目录下执行。

命令:
{command}

判断标准：
- 命令是否只操作工作目录内的文件？
- 绝对路径（如 /etc/passwd, ~/.ssh）是否在工作目录内？
- 相对路径默认指向工作目录内

只返回一个词: true 或 false"""

# 高危命令黑名单（直接拒绝，不问 LLM）
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


def _llm_check_command_safety(command: str, workspace: Path) -> bool:
    """
    使用 LLM 判断命令是否安全（仅用于无法通过规则判断的命令）
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
        from qrclaw.logger import get_logger
        logger = get_logger("qrclaw.security")
        logger.error(f"LLM 安全检查失败: {e}")
        return False


def _check_command_paths(command: str, workspace: Path) -> Optional[bool]:
    """
    检查命令中的路径是否安全
    
    Returns:
        True = 路径安全（都在工作目录内）
        False = 路径危险（访问了外部目录）
        None = 无法判断，需要 LLM
    """
    ws_resolved = workspace.resolve()
    
    # 提取命令中的所有路径参数
    # 排除 URL、选项参数等
    words = command.split()
    
    found_paths = []
    for word in words:
        # 跳过选项参数（以 - 开头）
        if word.startswith('-'):
            continue
        # 跳过 URL
        if word.startswith('http://') or word.startswith('https://'):
            continue
        # 跳过纯数字、命令本身等
        if re.match(r'^[a-zA-Z0-9_\-\.]+$', word) and not any(c in word for c in ['/', '~', '$']):
            continue
        
        # 可能是路径
        if any(c in word for c in ['/', '~', '$', '.']):
            found_paths.append(word)
    
    # 没有路径参数 → 安全
    if not found_paths:
        return True
    
    # 检查每个路径
    for path_str in found_paths:
        try:
            # 展开 ~ 和 $HOME
            expanded = os.path.expandvars(os.path.expanduser(path_str))
            
            # 如果是相对路径（不是以 / 开头），需要检查是否逃逸
            if not expanded.startswith('/'):
                # 相对路径，解析后检查是否在工作目录内
                resolved = (ws_resolved / expanded).resolve()
            else:
                # 绝对路径
                resolved = Path(expanded).resolve()
            
            # 检查是否在工作目录内
            try:
                resolved.relative_to(ws_resolved)
                # 在工作目录内，继续检查下一个
            except ValueError:
                # 不在工作目录内，危险！
                return False
                
        except Exception:
            # 路径解析失败，交给 LLM
            return None
    
    # 所有路径都在工作目录内
    return True


def _get_effective_agent_id(agent_id: str) -> str:
    """
    获取有效的 agent ID（用于权限继承）
    """
    try:
        from qrclaw.tools.spawn_agent import get_parent_agent_id
        parent_id = get_parent_agent_id(agent_id)
        if parent_id:
            return _get_effective_agent_id(parent_id)
    except ImportError:
        pass
    
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
            f.write("\n# access: full (无限制) | scoped (仅限workspace+白名单) | readonly (只读)\n")
            f.write("# allow_paths: 允许访问的外部路径列表\n")

    def get_permission(self, agent_id: str) -> AgentPermission:
        """获取指定 Agent 的权限配置（支持子 agent 权限继承）"""
        effective_id = _get_effective_agent_id(agent_id)
        
        if effective_id != agent_id:
            from qrclaw.logger import get_logger
            logger = get_logger("qrclaw.security")
            logger.debug(f"子 agent '{agent_id}' 继承父 agent '{effective_id}' 的权限")

        if effective_id in self._config.agents:
            return self._config.agents[effective_id]

        return AgentPermission(access="scoped")

    def check_access(self, agent_id: str, tool_name: str, args: dict, workspace_root: Path):
        """
        核心切面：检查工具调用是否合规
        """
        perm = self.get_permission(agent_id)

        # Full Access 直接放行
        if perm.access == "full":
            return

        # 检查文件路径工具
        if tool_name in ["read_file", "write_file", "list_directory", "delete_file"]:
            path_str = args.get("path")
            if path_str:
                self._check_path_safety(path_str, perm, workspace_root, tool_name, agent_id)

        # 检查 Shell 命令
        if tool_name == "run_shell":
            command = args.get("command", "")
            self._check_shell_safety(command, workspace_root)

    def _check_path_safety(self, path_str: str, perm: AgentPermission, workspace_root: Path, tool_name: str, agent_id: str):
        """检查路径是否在允许范围内"""
        try:
            target_path = Path(path_str).expanduser().resolve()
            ws_root = workspace_root.resolve()
        except Exception as e:
            raise PermissionError(f"🚫 [Security] 路径解析失败: {e}{PERMISSION_DENIED_SUFFIX}")

        # 检查保护路径
        for protected in PROTECTED_PATHS:
            if target_path == protected.resolve():
                if agent_id != "default":
                    raise PermissionError(f"🚫 [Security] 系统配置文件只有 default agent 可访问: {path_str}{PERMISSION_DENIED_SUFFIX}")

        # 检查是否在 Workspace 内部
        if self._is_subpath(target_path, ws_root):
            return

        # 检查白名单
        for allowed in perm.allow_paths:
            allowed_path = Path(allowed).expanduser().resolve()
            if self._is_subpath(target_path, allowed_path):
                if perm.access == "readonly" and tool_name in ["write_file", "delete_file"]:
                    raise PermissionError(f"🚫 [Security] 该路径只读，禁止写入: {path_str}{PERMISSION_DENIED_SUFFIX}")
                return

        raise PermissionError(f"🚫 [Security] Agent 无权访问 Workspace 外部路径: {path_str}{PERMISSION_DENIED_SUFFIX}")

    def _check_shell_safety(self, command: str, workspace_root: Path):
        """
        检查 Shell 命令安全性
        
        策略：
        1. 高危命令黑名单 → 直接拒绝
        2. 检查路径 → 相对路径安全，绝对路径检查是否在工作目录内
        3. 无法判断 → LLM 兜底
        """
        from qrclaw.logger import get_logger
        logger = get_logger("qrclaw.security")
        
        # 1. 高危命令黑名单
        for pattern in DANGEROUS_COMMANDS:
            if re.search(pattern, command, re.IGNORECASE):
                raise PermissionError(f"🚫 [Security] 禁止执行高危命令: {command}{PERMISSION_DENIED_SUFFIX}")

        # 2. 检查命令中的路径
        path_result = _check_command_paths(command, workspace_root)
        
        if path_result is True:
            logger.debug(f"路径检查通过: {command[:50]}")
            return
        
        if path_result is False:
            raise PermissionError(f"🚫 [Security] 命令访问了工作目录外的文件: {command}{PERMISSION_DENIED_SUFFIX}")

        # 3. 无法判断，交给 LLM
        logger.debug(f"路径无法判断，调用 LLM: {command[:50]}")
        if not _llm_check_command_safety(command, workspace_root):
            raise PermissionError(f"🚫 [Security] 命令存在安全风险: {command}{PERMISSION_DENIED_SUFFIX}")

    def _is_subpath(self, target: Path, parent: Path) -> bool:
        """判断 target 是否是 parent 的子路径"""
        try:
            target.relative_to(parent)
            return True
        except ValueError:
            return False


# 全局单例
security_manager = SecurityManager()