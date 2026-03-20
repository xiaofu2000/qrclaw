import os
import subprocess
from pathlib import Path
from javaclaw.tools.registry import register


@register(schema={
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取本地文件的内容",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"}
            },
            "required": ["path"]
        }
    }
})
def read_file(path: str) -> str:
    try:
        # Path() 自动处理 Windows/macOS/Linux 的路径格式差异
        p = Path(path).expanduser().resolve()
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"错误：文件不存在 {path}"
    except Exception as e:
        return f"错误：{e}"


@register(schema={
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "把内容写入本地文件，文件不存在会自动创建，已存在则覆盖",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的内容"}
            },
            "required": ["path", "content"]
        }
    }
})
def write_file(path: str, content: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        # 目录不存在时自动创建，parents=True 支持多级目录
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"已写入：{p}"
    except Exception as e:
        return f"错误：写入失败 {e}"


@register(schema={
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": "在本地执行shell命令，返回输出结果，超时30秒",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的shell命令"}
            },
            "required": ["command"]
        }
    }
})
def run_shell(command: str) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=30,
            # 不用 text=True，拿原始 bytes 自己解码，避免 Windows GBK 乱码
        )
        # Windows 用 GBK，其他系统用 UTF-8，解码失败时用 replace 占位不崩溃
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
