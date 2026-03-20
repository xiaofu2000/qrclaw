import os
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from javaclaw.tools.registry import register


# ── 参数模型 ──────────────────────────────────────────────

class ReadFileArgs(BaseModel):
    path: str = Field(description="文件的绝对路径，例如 /tmp/test.txt 或 C:\\Users\\test.txt")


class WriteFileArgs(BaseModel):
    path: str = Field(description="文件的绝对路径，文件不存在会自动创建")
    content: str = Field(description="要写入的文本内容")


class RunShellArgs(BaseModel):
    command: str = Field(description="要执行的 shell 命令，例如 ls -la 或 python3 hello.py")


# ── 工具函数 ──────────────────────────────────────────────

@register(description="读取本地文件的内容", args_model=ReadFileArgs)
def read_file(path: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"错误：文件不存在 {path}"
    except Exception as e:
        return f"错误：{e}"


@register(description="把内容写入本地文件，文件不存在会自动创建，已存在则覆盖", args_model=WriteFileArgs)
def write_file(path: str, content: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入：{p}"
    except Exception as e:
        return f"错误：写入失败 {e}"


@register(description="在本地执行 shell 命令，返回输出结果，超时 30 秒", args_model=RunShellArgs)
def run_shell(command: str) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=30,
        )
        encoding = "gbk" if os.name == "nt" else "utf-8"
        stdout = result.stdout.decode(encoding, errors="replace").strip()
        stderr = result.stderr.decode(encoding, errors="replace").strip()
        output = stdout
        if stderr:
            output += f"\n[stderr] {stderr}"
        return output or "(无输出)"
    except subprocess.TimeoutExpired:
        return "错误：命令执行超时（30秒）"
    except Exception as e:
        return f"错误：{e}"
