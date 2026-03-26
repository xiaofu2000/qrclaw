from pathlib import Path
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.filesystem")

class ReadFileArgs(BaseModel):
    path: str = Field(description="文件的绝对路径，例如 /tmp/test.txt 或 C:\\Users\\test.txt")

class WriteFileArgs(BaseModel):
    path: str = Field(description="文件的绝对路径，文件不存在会自动创建")
    content: str = Field(description="要写入的文本内容")

class ListDirectoryArgs(BaseModel):
    path: str = Field(description="要列出的目录路径，例如 /tmp 或 ~/Documents")

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
