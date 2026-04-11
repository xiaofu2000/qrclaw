import subprocess
from pathlib import Path
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.filesystem")


def _resolve_path(path: str) -> Path:
    """
    解析文件路径。
    - 如果当前 agent 启用了沙箱，且路径是 /workspace 下的路径，
      将其映射到主机上的实际 workspace 目录（文件工具在主机侧执行）。
    - 其他情况直接 resolve。
    """
    try:
        from qrclaw.agent import get_workspace
        from qrclaw.sandbox import is_sandbox_enabled
        ws = get_workspace()
        if ws and is_sandbox_enabled(ws.agent_id):
            p = Path(path)
            # /workspace/... → 映射到主机 workspace root
            if path.startswith("/workspace"):
                relative = p.relative_to("/workspace") if path != "/workspace" else Path(".")
                return (ws.root / relative).resolve()
            # 相对路径 → 相对于 workspace root
            if not p.is_absolute():
                return (ws.root / p).resolve()
    except Exception:
        pass
    return Path(path).expanduser().resolve()


class ReadFileArgs(BaseModel):
    path: str = Field(description="文件路径。沙箱内用 /workspace/文件名，或相对路径")

class WriteFileArgs(BaseModel):
    path: str = Field(description="文件路径。沙箱内用 /workspace/文件名，或相对路径")
    content: str = Field(description="要写入的文本内容")

class ListDirectoryArgs(BaseModel):
    path: str = Field(description="目录路径。沙箱内用 /workspace 或 /workspace/子目录")


@register(description="读取本地文件的内容", args_model=ReadFileArgs)
def read_file(path: str) -> str:
    logger.debug(f"读取文件: {path}")
    try:
        p = _resolve_path(path)
        if p.is_dir():
            error_msg = f"错误：{path} 是一个目录，请使用 list_directory 工具查看目录内容"
            logger.warning(error_msg)
            return error_msg
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
        p = _resolve_path(path)
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
        p = _resolve_path(path)
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


# ── 精确文本替换工具 ─────────────────────────────────────────────────────────

class StrReplaceArgs(BaseModel):
    path: str = Field(description="要编辑的文件路径")
    old_str: str = Field(description="要被替换的原始文本片段，必须与文件中的内容完全一致（包括空格和缩进）")
    new_str: str = Field(description="替换后的新文本。留空字符串则为删除")


@register(
    description=(
        "精确替换文件中的一段文本。只替换第一个匹配项。"
        "old_str 必须与文件内容完全一致，包含足够的上下文以确保唯一匹配。"
        "比 write_file 更安全，不会丢失文件其他内容。"
    ),
    args_model=StrReplaceArgs,
    confirm=True,
)
def str_replace(path: str, old_str: str, new_str: str) -> str:
    logger.debug(f"str_replace: {path}")
    try:
        p = _resolve_path(path)
        if not p.is_file():
            return f"错误：文件不存在 {path}"

        content = p.read_text(encoding="utf-8")
        count = content.count(old_str)

        if count == 0:
            return "错误：未找到匹配的文本，请检查 old_str 是否与文件内容完全一致（注意空格和缩进）"
        if count > 1:
            return f"错误：匹配到 {count} 处，请在 old_str 中包含更多上下文使其唯一"

        new_content = content.replace(old_str, new_str, 1)
        p.write_text(new_content, encoding="utf-8")
        logger.info(f"str_replace 成功: {p}")
        return f"✅ 替换成功：{p}"
    except Exception as e:
        logger.error(f"str_replace 失败: {path}, 错误: {e}", exc_info=True)
        return f"错误：替换失败 {e}"


# ── 代码搜索工具 ─────────────────────────────────────────────────────────────

class GrepCodeArgs(BaseModel):
    pattern: str = Field(description="搜索的文本或正则表达式")
    path: str = Field(default=".", description="搜索的目录或文件路径，默认当前工作目录")
    include: str = Field(default="", description="限定文件类型，如 '*.py'、'*.ts'、'*.js'")


@register(
    description=(
        "在代码文件中搜索文本模式，返回匹配的文件名、行号和内容。"
        "支持正则表达式。用于查找函数定义、变量引用、import 语句等。"
        "比 run_shell 执行 grep 更安全、更方便。"
    ),
    args_model=GrepCodeArgs,
)
def grep_code(pattern: str, path: str = ".", include: str = "") -> str:
    logger.debug(f"grep_code: pattern={pattern}, path={path}, include={include}")
    try:
        p = _resolve_path(path)
        if not p.exists():
            return f"错误：路径不存在 {path}"

        # 优先用 ripgrep，没有则用 grep
        rg_path = _find_executable("rg")
        if rg_path:
            cmd = [rg_path, "--line-number", "--max-count", "30", "--no-heading"]
            if include:
                cmd += ["--glob", include]
            cmd += [pattern, str(p)]
        else:
            cmd = ["grep", "-rn", "--max-count=30"]
            if include:
                cmd += ["--include", include]
            cmd += [pattern, str(p)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(p) if p.is_dir() else str(p.parent),
        )

        output = result.stdout.strip()
        if not output:
            return "(无匹配结果)"

        lines = output.split("\n")
        if len(lines) > 30:
            output = "\n".join(lines[:30]) + f"\n\n... 共 {len(lines)} 条结果，仅显示前 30 条"

        logger.info(f"grep_code 成功: {len(lines)} 条匹配")
        return output
    except subprocess.TimeoutExpired:
        return "错误：搜索超时（30秒），请缩小搜索范围"
    except Exception as e:
        logger.error(f"grep_code 失败: {e}", exc_info=True)
        return f"错误：搜索失败 {e}"


def _find_executable(name: str) -> str | None:
    """查找可执行文件路径，找不到返回 None"""
    import shutil
    return shutil.which(name)
