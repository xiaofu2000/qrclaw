import os
import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.config import TAVILY_API_KEY
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.builtin")


# ── 参数模型 ──────────────────────────────────────────────

class ReadFileArgs(BaseModel):
    path: str = Field(description="文件的绝对路径，例如 /tmp/test.txt 或 C:\\Users\\test.txt")


class WriteFileArgs(BaseModel):
    path: str = Field(description="文件的绝对路径，文件不存在会自动创建")
    content: str = Field(description="要写入的文本内容")


class RunShellArgs(BaseModel):
    command: str = Field(description="要执行的 shell 命令，例如 ls -la 或 python3 hello.py")


class ListDirectoryArgs(BaseModel):
    path: str = Field(description="要列出的目录路径，例如 /tmp 或 ~/Documents")


class WebSearchArgs(BaseModel):
    query: str = Field(description="搜索关键词，用自然语言描述想查找的内容")


# ── 工具函数 ──────────────────────────────────────────────

@register(description="读取本地文件的内容", args_model=ReadFileArgs)
def read_file(path: str) -> str:
    logger.debug(f"读取文件: {path}")
    try:
        p = Path(path).expanduser().resolve()
        content = p.read_text(encoding="utf-8")
        logger.info(f"读取文件成功: {p}, 大小: {len(content)} 字符")
        return content
    except FileNotFoundError:
        error_msg = f"错误：文件不存在 {path}"
        logger.warning(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"错误：{e}"
        logger.error(f"读取文件失败: {path}, 错误: {e}", exc_info=True)
        return error_msg


@register(description="把内容写入本地文件，文件不存在会自动创建，已存在则覆盖", args_model=WriteFileArgs, confirm=True)
def write_file(path: str, content: str) -> str:
    logger.debug(f"写入文件: {path}, 内容长度: {len(content)}")
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        logger.info(f"写入文件成功: {p}")
        return f"已写入：{p}"
    except Exception as e:
        error_msg = f"错误：写入失败 {e}"
        logger.error(f"写入文件失败: {path}, 错误: {e}", exc_info=True)
        return error_msg


@register(description="列出目录下的文件和子目录，LLM用此工具了解目录结构", args_model=ListDirectoryArgs)
def list_directory(path: str) -> str:
    logger.debug(f"列出目录: {path}")
    try:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            error_msg = f"错误：{path} 不是一个目录"
            logger.warning(error_msg)
            return error_msg
        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = []
        for entry in entries:
            tag = "[文件]" if entry.is_file() else "[目录]"
            lines.append(f"{tag} {entry.name}")
        result = "\n".join(lines) if lines else "(空目录)"
        logger.info(f"列出目录成功: {p}, 共 {len(entries)} 项")
        return result
    except Exception as e:
        error_msg = f"错误：{e}"
        logger.error(f"列出目录失败: {path}, 错误: {e}", exc_info=True)
        return error_msg


@register(description="联网搜索，获取最新信息，适合查找新闻、文档、技术资料", args_model=WebSearchArgs)
def web_search(query: str) -> str:
    logger.debug(f"联网搜索: {query}")
    if not TAVILY_API_KEY:
        error_msg = "错误：未配置 TAVILY_API_KEY"
        logger.error(error_msg)
        return error_msg
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        response = client.search(query=query, max_results=5)
        results = response.get("results", [])
        if not results:
            logger.info(f"搜索无结果: {query}")
            return "未找到相关结果"
        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r.get('title', '')}")
            lines.append(f"   {r.get('url', '')}")
            lines.append(f"   {r.get('content', '')[:200]}")
            lines.append("")
        result = "\n".join(lines)
        logger.info(f"搜索成功: {query}, 找到 {len(results)} 条结果")
        return result
    except Exception as e:
        error_msg = f"错误：搜索失败 {e}"
        logger.error(f"搜索失败: {query}, 错误: {e}", exc_info=True)
        return error_msg


@register(description="在本地执行 shell 命令，返回输出结果，超时 30 秒", args_model=RunShellArgs, confirm=True)
def run_shell(command: str) -> str:
    logger.debug(f"执行 shell 命令: {command}")
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
        
        if result.returncode == 0:
            logger.info(f"命令执行成功: {command}")
        else:
            logger.warning(f"命令执行失败 (exit code {result.returncode}): {command}")
        
        return output or "(无输出)"
    except subprocess.TimeoutExpired:
        error_msg = "错误：命令执行超时（30秒）"
        logger.warning(f"命令超时: {command}")
        return error_msg
    except Exception as e:
        error_msg = f"错误：{e}"
        logger.error(f"命令执行失败: {command}, 错误: {e}", exc_info=True)
        return error_msg