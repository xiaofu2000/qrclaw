import os
import subprocess
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.shell")

class RunShellArgs(BaseModel):
    command: str = Field(description="要执行的 shell 命令，例如 ls -la 或 python3 hello.py")

@register(description="在本地执行 shell 命令，返回输出结果，超时 30 分钟", args_model=RunShellArgs, confirm=True)
def run_shell(command: str) -> str:
    logger.debug(f"执行 shell 命令: {command}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=1800,  # 30 分钟 = 1800 秒
        )
        encoding = "gbk" if os.name == "nt" else "utf-8"
        stdout = result.stdout.decode(encoding, errors="replace").strip()
        stderr = result.stderr.decode(encoding, errors="replace").strip()
        output = stdout
        if stderr:
            output += f"\n[stderr] {stderr}"

        if result.returncode == 0:
            logger.info(f"命令执行成功: {command}")
        else:
            logger.warning(f"命令执行失败 (exit code {result.returncode}): {command}")

        return output or "(无输出)"
    except subprocess.TimeoutExpired:
        error_msg = "错误：命令执行超时（30分钟）"
        logger.warning(f"命令超时: {command}")
        return error_msg
    except Exception as e:
        error_msg = f"错误：{e}"
        logger.error(f"命令执行失败: {command}, 错误: {e}", exc_info=True)
        return error_msg
