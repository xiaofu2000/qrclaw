import os
import re
import subprocess
import threading
from pathlib import Path
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.filesystem")


# ============================================================================
# 配置常量
# ============================================================================

_DEFAULT_MAX_READ_CHARS = 100_000
_max_read_chars_cached: int | None = None


def _get_max_read_chars() -> int:
    """返回配置的最大字符读取数。首次调用时从 config.yaml 读取并缓存。"""
    global _max_read_chars_cached
    if _max_read_chars_cached is not None:
        return _max_read_chars_cached
    try:
        from qrclaw.config import load_config
        cfg = load_config()
        val = cfg.get("file_read_max_chars")
        if isinstance(val, (int, float)) and val > 0:
            _max_read_chars_cached = int(val)
            return _max_read_chars_cached
    except Exception:
        pass
    _max_read_chars_cached = _DEFAULT_MAX_READ_CHARS
    return _max_read_chars_cached


_LARGE_FILE_HINT_BYTES = 512_000
_DEFAULT_OFFSET = 1
_DEFAULT_LIMIT = 500
_MAX_LIMIT = 2000


# ============================================================================
# 设备路径防护
# ============================================================================
_BLOCKED_DEVICE_PATHS = frozenset({
    "/dev/zero", "/dev/random", "/dev/urandom", "/dev/full",
    "/dev/stdin", "/dev/tty", "/dev/console",
    "/dev/stdout", "/dev/stderr",
    "/dev/fd/0", "/dev/fd/1", "/dev/fd/2",
})


def _is_blocked_device(filepath: str) -> bool:
    normalized = os.path.expanduser(filepath)
    if normalized in _BLOCKED_DEVICE_PATHS:
        return True
    if normalized.startswith("/proc/") and normalized.endswith(("/fd/0", "/fd/1", "/fd/2")):
        return True
    return False


# ============================================================================
# 敏感路径防护
# ============================================================================
_SENSITIVE_PATH_PREFIXES = (
    "/etc/", "/boot/", "/usr/lib/systemd/",
    "/private/etc/", "/private/var/",
)
_SENSITIVE_EXACT_PATHS = {"/var/run/docker.sock", "/run/docker.sock"}


def _check_sensitive_path(filepath: str) -> str | None:
    try:
        resolved = os.path.realpath(os.path.expanduser(filepath))
    except (OSError, ValueError):
        resolved = filepath
    normalized = os.path.normpath(os.path.expanduser(filepath))
    for prefix in _SENSITIVE_PATH_PREFIXES:
        if resolved.startswith(prefix) or normalized.startswith(prefix):
            return f"拒绝写入敏感系统路径: {filepath}\n如需修改系统文件，请使用终端工具。"
    if resolved in _SENSITIVE_EXACT_PATHS or normalized in _SENSITIVE_EXACT_PATHS:
        return f"拒绝写入敏感系统路径: {filepath}\n如需修改系统文件，请使用终端工具。"
    return None


# ============================================================================
# 二进制文件防护
# ============================================================================
_BINARY_EXTENSIONS = frozenset({
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a",
    ".pyc", ".pyo", ".pyd", ".egg", ".whl",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".ico", ".webp",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".rar", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".class", ".jar", ".war",
    ".ttf", ".otf", ".woff", ".woff2",
    ".db", ".sqlite", ".sqlite3",
})


def _has_binary_extension(filepath: str) -> bool:
    return Path(filepath).suffix.lower() in _BINARY_EXTENSIONS


# ============================================================================
# 读取追踪器：循环检测 + 去重 + 内存清理
# ============================================================================
_read_tracker_lock = threading.Lock()
_read_tracker: dict = {}


def _get_task_tracker(task_id: str = "default") -> dict:
    with _read_tracker_lock:
        if task_id not in _read_tracker:
            _read_tracker[task_id] = {
                "last_key": None,
                "consecutive": 0,
                "read_history": set(),
                "dedup": {},
                "read_timestamps": {},
            }
        return _read_tracker[task_id]


def clear_read_tracker(task_id: str = None) -> None:
    """清理读取追踪器，防止内存泄漏。传入 task_id 只清理该任务，不传则清理全部。"""
    with _read_tracker_lock:
        if task_id:
            _read_tracker.pop(task_id, None)
        else:
            _read_tracker.clear()


def reset_file_dedup(task_id: str = None) -> None:
    """清除去重缓存。上下文压缩后调用，让 model 能重新获取文件完整内容。"""
    with _read_tracker_lock:
        if task_id:
            task_data = _read_tracker.get(task_id)
            if task_data and "dedup" in task_data:
                task_data["dedup"].clear()
        else:
            for task_data in _read_tracker.values():
                if "dedup" in task_data:
                    task_data["dedup"].clear()


def notify_other_tool_call(task_id: str = "default") -> None:
    """重置连续读取/搜索计数器。由工具调度器在执行非读取工具后调用。"""
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if task_data:
            task_data["last_key"] = None
            task_data["consecutive"] = 0


def get_read_files_summary(task_id: str = "default") -> list:
    """返回当前 session 中已读文件列表，供上下文压缩器使用。"""
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id, {})
        read_history = task_data.get("read_history", set())
        seen_paths: dict = {}
        for (path, offset, limit) in read_history:
            if path not in seen_paths:
                seen_paths[path] = []
            seen_paths[path].append(f"lines {offset}-{offset + limit - 1}")
        return [
            {"path": p, "regions": regions}
            for p, regions in sorted(seen_paths.items())
        ]


# ============================================================================
# 陈旧检测辅助函数
# ============================================================================

def _check_file_staleness(filepath: str, task_id: str = "default") -> str | None:
    """检查文件是否在 agent 上次读取后被外部修改。返回警告字符串或 None。"""
    try:
        resolved = str(Path(filepath).expanduser().resolve())
    except (OSError, ValueError):
        return None
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if not task_data:
            return None
        read_mtime = task_data.get("read_timestamps", {}).get(resolved)
    if read_mtime is None:
        return None
    try:
        current_mtime = os.path.getmtime(resolved)
    except OSError:
        return None
    if current_mtime != read_mtime:
        return (
            f"[警告：{filepath} 在您读取后被外部修改，"
            "您读取的内容可能已过期。建议写入前重新读取文件确认。]"
        )
    return None


def _update_read_timestamp(filepath: str, task_id: str = "default") -> None:
    """写入成功后更新文件修改时间记录，防止连续写入触发误报。"""
    try:
        resolved = str(Path(filepath).expanduser().resolve())
        current_mtime = os.path.getmtime(resolved)
    except (OSError, ValueError):
        return
    with _read_tracker_lock:
        task_data = _read_tracker.get(task_id)
        if task_data is not None:
            task_data.setdefault("read_timestamps", {})[resolved] = current_mtime


# ============================================================================
# 路径解析
# ============================================================================

def _resolve_path(path: str) -> Path:
    """
    解析文件路径。沙箱模式下将 /workspace/... 映射到主机 workspace 目录。
    """
    try:
        from qrclaw.agent import get_workspace
        from qrclaw.sandbox import is_sandbox_enabled
        ws = get_workspace()
        if ws and is_sandbox_enabled(ws.agent_id):
            p = Path(path)
            if path.startswith("/workspace"):
                relative = p.relative_to("/workspace") if path != "/workspace" else Path(".")
                return (ws.root / relative).resolve()
            if not p.is_absolute():
                return (ws.root / p).resolve()
    except Exception:
        pass
    return Path(path).expanduser().resolve()


# ============================================================================
# 参数模型
# ============================================================================

class ReadFileArgs(BaseModel):
    path: str = Field(description="文件路径。沙箱内用 /workspace/文件名，或相对路径")
    offset: int = Field(default=_DEFAULT_OFFSET, description="起始行号（1-indexed）")
    limit: int = Field(default=_DEFAULT_LIMIT, description="最大读取行数（默认500，最大2000）")


class WriteFileArgs(BaseModel):
    path: str = Field(description="文件路径。沙箱内用 /workspace/文件名，或相对路径")
    content: str = Field(description="要写入的文本内容")


class ListDirectoryArgs(BaseModel):
    path: str = Field(description="目录路径。沙箱内用 /workspace 或 /workspace/子目录")


class PatchArgs(BaseModel):
    mode: str = Field(
        default="replace",
        description="编辑模式：'replace' 精确/模糊替换单文件，'patch' 应用 V4A 多文件补丁"
    )
    path: str = Field(
        default="",
        description="要编辑的文件路径（replace mode 必填）"
    )
    old_string: str = Field(
        default="",
        description=(
            "要替换的原始文本（replace mode 必填）。"
            "支持模糊匹配，轻微空白/缩进差异可容忍。"
            "包含足够上下文确保唯一匹配。"
        )
    )
    new_string: str = Field(
        default="",
        description="替换后的新文本（replace mode 必填）。留空字符串则为删除该段文本。"
    )
    replace_all: bool = Field(
        default=False,
        description="是否替换所有匹配项（默认只替换第一个）"
    )
    patch_content: str = Field(
        default="",
        alias="patch",
        description=(
            "V4A 格式补丁内容（patch mode 必填）。格式：\n"
            "*** Begin Patch\n"
            "*** Update File: path/to/file.py\n"
            "@@ context hint @@\n"
            " context line\n"
            "-removed line\n"
            "+added line\n"
            "*** Add File: path/to/new.py\n"
            "+new content\n"
            "*** Delete File: path/to/old.py\n"
            "*** Move File: old.py -> new.py\n"
            "*** End Patch"
        )
    )

    class Config:
        populate_by_name = True


class GrepCodeArgs(BaseModel):
    pattern: str = Field(description="搜索的文本或正则表达式")
    path: str = Field(default=".", description="搜索的目录或文件路径，默认当前工作目录")
    include: str = Field(default="", description="限定文件类型，如 '*.py'、'*.ts'、'*.js'")
    limit: int = Field(default=30, description="最大返回结果数（默认30）")
    offset: int = Field(default=0, description="跳过前 N 条结果（分页用）")


# ============================================================================
# file_ops 适配器（供 patch_parser.apply_v4a_operations 使用）
# ============================================================================

class _QrclawFileOps:
    """
    patch_parser.apply_v4a_operations 需要一个 file_ops 适配器。
    提供 write_file / read_file_raw / delete_file / move_file 四个方法，
    返回值带 .error / .content 属性，与 Hermes ShellFileOperations 接口一致。
    """

    class _Result:
        def __init__(self, error=None, content=None):
            self.error = error
            self.content = content

    def write_file(self, path: str, content: str) -> "_QrclawFileOps._Result":
        try:
            p = _resolve_path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return self._Result()
        except Exception as e:
            return self._Result(error=str(e))

    def read_file_raw(self, path: str) -> "_QrclawFileOps._Result":
        try:
            p = _resolve_path(path)
            content = p.read_text(encoding="utf-8")
            return self._Result(content=content)
        except FileNotFoundError:
            return self._Result(error=f"文件不存在: {path}")
        except Exception as e:
            return self._Result(error=str(e))

    def delete_file(self, path: str) -> "_QrclawFileOps._Result":
        try:
            _resolve_path(path).unlink()
            return self._Result()
        except Exception as e:
            return self._Result(error=str(e))

    def move_file(self, old_path: str, new_path: str) -> "_QrclawFileOps._Result":
        try:
            src = _resolve_path(old_path)
            dst = _resolve_path(new_path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            return self._Result()
        except Exception as e:
            return self._Result(error=str(e))


_file_ops_instance = _QrclawFileOps()


# ============================================================================
# 工具实现
# ============================================================================

@register(
    description="读取本地文件的内容，支持分页读取。输出格式：'行号|内容'。大文件请使用 offset 和 limit 参数。",
    args_model=ReadFileArgs,
)
def read_file(path: str, offset: int = _DEFAULT_OFFSET, limit: int = _DEFAULT_LIMIT, task_id: str = "default") -> str:
    """读取文件内容，支持分页、循环检测和去重。"""
    logger.debug(f"读取文件: {path}, offset={offset}, limit={limit}")

    if offset < 1:
        offset = 1
    if limit < 1:
        limit = 1
    if limit > _MAX_LIMIT:
        limit = _MAX_LIMIT

    try:
        if _is_blocked_device(path):
            return f"无法读取 '{path}'：这是一个设备文件，会导致阻塞或无限输出"

        p = _resolve_path(path)
        resolved_str = str(p)

        if _has_binary_extension(resolved_str):
            return f"无法读取二进制文件 '{path}' ({p.suffix})。请使用其他工具处理二进制文件。"

        if p.is_dir():
            return f"错误：{path} 是一个目录，请使用 list_directory 工具查看目录内容"

        # 去重检查（先于循环计数，避免重复 stat）
        dedup_key = (resolved_str, offset, limit)
        task_data = _get_task_tracker(task_id)

        with _read_tracker_lock:
            cached_mtime = task_data.get("dedup", {}).get(dedup_key)

        dedup_hit = False
        if cached_mtime is not None:
            try:
                if os.path.getmtime(resolved_str) == cached_mtime:
                    dedup_hit = True
            except OSError:
                pass

        # 更新循环计数
        read_key = ("read", path, offset, limit)
        with _read_tracker_lock:
            task_data["read_history"].add((path, offset, limit))
            if task_data["last_key"] == read_key:
                task_data["consecutive"] += 1
            else:
                task_data["last_key"] = read_key
                task_data["consecutive"] = 1
            count = task_data["consecutive"]

        # 去重命中（循环检测仍生效）
        if dedup_hit:
            logger.debug(f"去重命中: {path} 未修改")
            if count >= 4:
                return (
                    f"阻断：您已连续读取同一文件区域 {count} 次。"
                    "内容未变化。请停止重新读取并继续执行任务。"
                )
            elif count >= 3:
                return (
                    f"[文件未变更] {path} 自上次读取后未修改，请使用之前读取的内容。\n\n"
                    f"[警告：您已连续读取同一区域 {count} 次，内容未变化，请使用已有信息。]"
                )
            return f"[文件未变更] {path} 自上次读取后未修改。请使用之前读取的内容。"

        # 执行读取
        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"错误：文件 {path} 不是有效的 UTF-8 文本文件"

        lines = content.split("\n")
        total_lines = len(lines)
        start_idx = offset - 1
        end_idx = start_idx + limit

        if start_idx >= total_lines:
            return f"错误：offset {offset} 超出文件行数（文件共 {total_lines} 行）"

        page_lines = lines[start_idx:end_idx]
        truncated = end_idx < total_lines

        result_content = "\n".join(
            f"{i}|{line}" for i, line in enumerate(page_lines, start=offset)
        )

        # 大小限制检查
        content_len = len(result_content)
        max_chars = _get_max_read_chars()
        if content_len > max_chars:
            return (
                f"错误：读取产生 {content_len:,} 个字符，超过安全限制（{max_chars:,} 字符）。"
                f"请使用 offset 和 limit 读取较小的范围。文件共 {total_lines} 行。"
            )

        # 存储 mtime
        with _read_tracker_lock:
            try:
                mtime_now = os.path.getmtime(resolved_str)
                task_data.setdefault("dedup", {})[dedup_key] = mtime_now
                task_data.setdefault("read_timestamps", {})[resolved_str] = mtime_now
            except OSError:
                pass

        # 大文件提示
        file_size = len(content.encode("utf-8"))
        hint = ""
        if file_size > _LARGE_FILE_HINT_BYTES and limit > 200 and truncated:
            hint = (
                f"\n\n[提示：文件较大（{file_size:,} 字节，共 {total_lines} 行）。"
                "建议使用 offset 和 limit 读取需要的部分。]"
            )

        # 循环警告
        warning = ""
        if count >= 3:
            warning = f"\n\n[警告：您已连续读取同一区域 {count} 次，内容未变化，请使用已有信息。]"

        logger.info(f"读取文件成功: {p}, {len(page_lines)}/{total_lines} 行, {content_len} 字符")
        return result_content + hint + warning

    except FileNotFoundError:
        return f"错误：文件不存在 {path}"
    except Exception as e:
        logger.error(f"读取文件失败: {path}, 错误: {e}", exc_info=True)
        return f"错误：{e}"


@register(
    description="把内容写入本地文件，文件不存在会自动创建，已存在则完全覆盖。修改已有文件请优先用 patch 工具。",
    args_model=WriteFileArgs,
    confirm=True,
)
def write_file(path: str, content: str, task_id: str = "default") -> str:
    """写入文件内容，包含敏感路径检查和陈旧检测。"""
    logger.debug(f"写入文件: {path}, 内容长度: {len(content)}")

    sensitive_err = _check_sensitive_path(path)
    if sensitive_err:
        return sensitive_err

    try:
        p = _resolve_path(path)
        stale_warning = _check_file_staleness(path, task_id)

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _update_read_timestamp(path, task_id)

        logger.info(f"写入文件成功: {p}")
        result = f"已写入：{p}"
        if stale_warning:
            result += f"\n{stale_warning}"
        return result

    except Exception as e:
        logger.error(f"写入文件失败: {path}, 错误: {e}", exc_info=True)
        return f"错误：写入失败 {e}"


@register(
    description="列出目录下的文件和子目录，LLM用此工具了解目录结构",
    args_model=ListDirectoryArgs,
)
def list_directory(path: str, task_id: str = "default") -> str:
    logger.debug(f"列出目录: {path}")

    if _is_blocked_device(path):
        return f"错误：{path} 是设备文件，无法列出"

    try:
        p = _resolve_path(path)
        if not p.is_dir():
            return f"错误：{path} 不是一个目录"

        entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        lines = [f"{'[文件]' if e.is_file() else '[目录]'} {e.name}" for e in entries]
        result = "\n".join(lines) if lines else "(空目录)"

        logger.info(f"列出目录成功: {p}, 共 {len(entries)} 项")
        notify_other_tool_call(task_id)
        return result

    except Exception as e:
        logger.error(f"列出目录失败: {path}, 错误: {e}", exc_info=True)
        return f"错误：{e}"


@register(
    description=(
        "对文件进行精确编辑。两种模式：\n"
        "replace（默认）：在单个文件中查找并替换文本，支持模糊匹配（容忍空白/缩进差异）。"
        "比 write_file 更安全，不会覆盖整个文件。\n"
        "patch：应用 V4A 格式多文件补丁，一次调用可同时 Update/Add/Delete/Move 多个文件，"
        "适合重构场景。"
    ),
    args_model=PatchArgs,
    confirm=True,
)
def patch(
    mode: str = "replace",
    path: str = "",
    old_string: str = "",
    new_string: str = "",
    replace_all: bool = False,
    patch_content: str = "",
    task_id: str = "default",
) -> str:
    logger.debug(f"patch: mode={mode}, path={path}")

    if mode == "replace":
        return _patch_replace(path, old_string, new_string, replace_all, task_id)
    elif mode == "patch":
        return _patch_v4a(patch_content, task_id)
    else:
        return f"错误：未知 mode '{mode}'，可选值：replace / patch"


def _patch_replace(path: str, old_string: str, new_string: str, replace_all: bool, task_id: str) -> str:
    if not path:
        return "错误：replace mode 需要提供 path"
    if not old_string:
        return "错误：replace mode 需要提供 old_string"

    sensitive_err = _check_sensitive_path(path)
    if sensitive_err:
        return sensitive_err

    try:
        p = _resolve_path(path)
        if not p.is_file():
            return f"错误：文件不存在 {path}"

        stale_warning = _check_file_staleness(path, task_id)
        content = p.read_text(encoding="utf-8")

        from qrclaw.tools.fuzzy_match import fuzzy_find_and_replace
        new_content, match_count, strategy, error = fuzzy_find_and_replace(
            content, old_string, new_string, replace_all
        )

        if error or match_count == 0:
            return (
                f"错误：未找到匹配文本。{error or ''}\n"
                "[提示：使用 read_file 确认文件当前内容，或 grep_code 定位目标文本。]"
            )

        p.write_text(new_content, encoding="utf-8")
        _update_read_timestamp(path, task_id)
        notify_other_tool_call(task_id)

        logger.info(f"patch replace 成功: {p}, 策略: {strategy}, 替换 {match_count} 处")
        result = f"替换成功：{p}（策略: {strategy}，替换 {match_count} 处）"
        if stale_warning:
            result += f"\n{stale_warning}"
        return result

    except Exception as e:
        logger.error(f"patch replace 失败: {path}, 错误: {e}", exc_info=True)
        return f"错误：替换失败 {e}"


def _patch_v4a(patch_content: str, task_id: str) -> str:
    if not patch_content:
        return "错误：patch mode 需要提供 patch 内容"

    # 敏感路径检查：从 patch 内容中提取所有涉及的文件路径
    _file_pattern = r'^\*\*\*\s+(?:Update|Add|Delete)\s+File:\s*(.+)$'
    for m in re.finditer(_file_pattern, patch_content, re.MULTILINE):
        sensitive_err = _check_sensitive_path(m.group(1).strip())
        if sensitive_err:
            return sensitive_err

    try:
        from qrclaw.tools.patch_parser import parse_v4a_patch, apply_v4a_operations

        operations, parse_error = parse_v4a_patch(patch_content)
        if parse_error:
            return f"错误：解析补丁失败 — {parse_error}"

        # 陈旧检测
        stale_warnings = []
        for op in operations:
            sw = _check_file_staleness(op.file_path, task_id)
            if sw:
                stale_warnings.append(sw)

        result = apply_v4a_operations(operations, _file_ops_instance)

        # 更新所有被修改/创建文件的时间戳
        for f in (result.files_modified or []) + (result.files_created or []):
            _update_read_timestamp(f, task_id)
        notify_other_tool_call(task_id)

        # 构建输出
        lines = []
        if result.success:
            lines.append("补丁应用成功")
        else:
            lines.append(f"补丁应用失败：{result.error}")
        if result.files_created:
            lines.append(f"新建文件：{', '.join(result.files_created)}")
        if result.files_modified:
            lines.append(f"修改文件：{', '.join(result.files_modified)}")
        if result.files_deleted:
            lines.append(f"删除文件：{', '.join(result.files_deleted)}")
        if result.diff:
            lines.append(f"\n{result.diff}")
        lines.extend(stale_warnings)

        logger.info(
            f"patch v4a: success={result.success}, "
            f"created={result.files_created}, modified={result.files_modified}, "
            f"deleted={result.files_deleted}"
        )
        return "\n".join(lines)

    except Exception as e:
        logger.error(f"patch v4a 失败: {e}", exc_info=True)
        return f"错误：补丁应用失败 {e}"


@register(
    description=(
        "在代码文件中搜索文本模式，返回匹配的文件名、行号和内容。"
        "支持正则表达式。用于查找函数定义、变量引用、import 语句等。"
        "比 run_shell 执行 grep 更安全、更方便。"
    ),
    args_model=GrepCodeArgs,
)
def grep_code(pattern: str, path: str = ".", include: str = "", limit: int = 30, offset: int = 0, task_id: str = "default") -> str:
    logger.debug(f"grep_code: pattern={pattern}, path={path}, include={include}")

    # 连续搜索循环检测
    search_key = ("search", pattern, path, include, limit, offset)
    task_data = _get_task_tracker(task_id)
    with _read_tracker_lock:
        if task_data["last_key"] == search_key:
            task_data["consecutive"] += 1
        else:
            task_data["last_key"] = search_key
            task_data["consecutive"] = 1
        count = task_data["consecutive"]

    if count >= 4:
        return (
            f"阻断：您已连续执行相同搜索 {count} 次。"
            "结果未变化。请停止重复搜索并继续执行任务。"
        )

    try:
        p = _resolve_path(path)
        if not p.exists():
            return f"错误：路径不存在 {path}"

        rg_path = _find_executable("rg")
        if rg_path:
            cmd = [rg_path, "--line-number", f"--max-count={limit}", "--no-heading"]
            if include:
                cmd += ["--glob", include]
            cmd += [pattern, str(p)]
        else:
            cmd = ["grep", "-rn", f"--max-count={limit}"]
            if include:
                cmd += ["--include", include]
            cmd += [pattern, str(p)]

        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(p) if p.is_dir() else str(p.parent),
        )

        output = proc.stdout.strip()
        if not output:
            return "(无匹配结果)"

        result_lines = output.split("\n")

        if offset > 0:
            result_lines = result_lines[offset:]

        truncated = len(result_lines) > limit
        if truncated:
            output = "\n".join(result_lines[:limit])
            output += f"\n\n[结果已截断。使用 offset={offset + limit} 查看更多，或缩小搜索范围。]"
        else:
            output = "\n".join(result_lines)

        if count >= 3:
            output += f"\n\n[警告：您已连续执行相同搜索 {count} 次，结果未变化，请使用已有信息。]"

        logger.info(f"grep_code 成功: {len(result_lines)} 条匹配")
        return output

    except subprocess.TimeoutExpired:
        return "错误：搜索超时（30秒），请缩小搜索范围"
    except Exception as e:
        logger.error(f"grep_code 失败: {e}", exc_info=True)
        return f"错误：搜索失败 {e}"


def _find_executable(name: str) -> str | None:
    import shutil
    return shutil.which(name)
