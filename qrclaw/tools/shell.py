"""
Shell 命令执行工具

支持两种模式：
1. 直接执行：在主机上直接执行命令（无沙箱）
2. 沙箱执行：在 Docker 容器中执行命令（隔离环境）
"""
import os
import subprocess
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.shell")


class RunShellArgs(BaseModel):
    command: str = Field(description="要执行的 shell 命令，例如 ls -la 或 python3 hello.py")


def _get_current_agent_id() -> str:
    """获取当前 Agent ID"""
    try:
        from qrclaw.agent import get_agent_id
        return get_agent_id() or "default"
    except Exception:
        return "default"


def _get_workspace_root() -> str:
    """获取工作空间路径"""
    try:
        from qrclaw.agent import get_workspace
        ws = get_workspace()
        if ws:
            return str(ws.root)
    except Exception:
        pass
    return None


def _is_sandbox_enabled() -> bool:
    """检查是否启用沙箱"""
    try:
        from qrclaw.sandbox import is_sandbox_enabled
        agent_id = _get_current_agent_id()
        return is_sandbox_enabled(agent_id)
    except Exception:
        return False


def _exec_in_sandbox(command: str, cwd: str, timeout: int = 1800) -> str:
    """在沙箱中执行命令"""
    from qrclaw.sandbox import sandbox_manager, ContainerError
    
    agent_id = _get_current_agent_id()
    
    # 检查沙箱是否已创建
    if not sandbox_manager.has_sandbox(agent_id):
        try:
            from pathlib import Path
            workspace = Path(cwd) if cwd else None
            sandbox_manager.create_sandbox(agent_id, workspace=workspace)
            logger.info(f"已为 Agent {agent_id} 创建沙箱")
        except Exception as e:
            logger.warning(f"创建沙箱失败，降级为直接执行: {e}")
            return _exec_direct(command, cwd, timeout)
    
    # 在沙箱中执行
    try:
        result = sandbox_manager.exec(
            agent_id=agent_id,
            command=command,
            cwd="/workspace",
            timeout=timeout,
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\n[stderr] {result.stderr}"
        
        if result.exit_code == 0:
            logger.info(f"沙箱命令执行成功: {command[:100]}")
        else:
            logger.warning(f"沙箱命令执行失败 (exit code {result.exit_code}): {command[:100]}")
        
        return output or "(无输出)"
        
    except ContainerError as e:
        logger.error(f"沙箱执行失败: {e}")
        logger.warning("降级为直接执行模式")
        return _exec_direct(command, cwd, timeout)


def _exec_direct(command: str, cwd: str, timeout: int = 1800) -> str:
    """直接在主机执行命令"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
        )
        
        encoding = "gbk" if os.name == "nt" else "utf-8"
        stdout = result.stdout.decode(encoding, errors="replace").strip()
        stderr = result.stderr.decode(encoding, errors="replace").strip()
        
        output = stdout
        if stderr:
            output += f"\n[stderr] {stderr}"

        if result.returncode == 0:
            logger.info(f"命令执行成功: {command[:100]}")
        else:
            logger.warning(f"命令执行失败 (exit code {result.returncode}): {command[:100]}")

        return output or "(无输出)"
        
    except subprocess.TimeoutExpired:
        error_msg = "错误：命令执行超时（30分钟）"
        logger.warning(f"命令超时: {command[:100]}")
        return error_msg
    except Exception as e:
        error_msg = f"错误：{e}"
        logger.error(f"命令执行失败: {command[:100]}, 错误: {e}", exc_info=True)
        return error_msg


@register(description="在本地执行 shell 命令，返回输出结果，超时 30 分钟", args_model=RunShellArgs, confirm=True)
def run_shell(command: str) -> str:
    """执行 shell 命令，自动检测是否启用沙箱"""
    logger.debug(f"执行 shell 命令: {command[:100]}")
    
    cwd = _get_workspace_root()
    
    if cwd:
        logger.debug(f"Shell 命令将在目录下执行: {cwd}")
    
    if _is_sandbox_enabled():
        logger.info(f"在沙箱中执行命令: {command[:100]}")
        return _exec_in_sandbox(command, cwd)
    else:
        logger.debug(f"直接执行命令: {command[:100]}")
        return _exec_direct(command, cwd)