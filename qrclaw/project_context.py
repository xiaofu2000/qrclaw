"""
ProjectContext —— 运行时项目上下文

与 Workspace（agent 内部存储路径）解耦，专门管理"用户当前操作的目标项目路径"。

设计参考 Claude Code 的 AsyncLocalStorage<CWD> 模式，
在 Python 中通过 threading.local 实现线程级隔离，
确保并行子 agent 各自持有正确的项目路径，互不干扰。

用法：
    # 主 agent 启动时
    set_project_path("/Users/xxx/my-project")

    # 任意位置获取
    path = get_project_path()  # → "/Users/xxx/my-project"
"""
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.project_context")

_thread_local = threading.local()


@dataclass
class ProjectContext:
    """
    运行时项目上下文，线程级隔离。

    Attributes:
        project_path: 用户当前操作的目标项目路径（绝对路径）。
                      为 None 时回退到 os.getcwd()。
        additional_paths: 用户在会话中提及的其他项目路径列表。
    """
    project_path: str | None = None
    additional_paths: list[str] = field(default_factory=list)

    @property
    def effective_cwd(self) -> str:
        """获取有效的工作目录，优先 project_path，回退 os.getcwd()。"""
        return self.project_path or os.getcwd()


def set_project_context(ctx: ProjectContext) -> None:
    """设置当前线程的 ProjectContext。"""
    _thread_local.project_context = ctx
    logger.debug(f"设置 ProjectContext: project_path={ctx.project_path}")


def get_project_context() -> ProjectContext:
    """
    获取当前线程的 ProjectContext。
    未初始化时返回默认实例（project_path=None，回退到 os.getcwd()）。
    """
    ctx = getattr(_thread_local, "project_context", None)
    if ctx is None:
        ctx = ProjectContext()
        _thread_local.project_context = ctx
    return ctx


# ── 便捷函数 ──────────────────────────────────────────────────────────

def set_project_path(path: str | None) -> None:
    """设置当前线程的项目路径（便捷入口）。"""
    ctx = get_project_context()
    ctx.project_path = str(Path(path).resolve()) if path else None
    logger.info(f"项目路径已设置: {ctx.project_path}")


def get_project_path() -> str:
    """获取当前线程的有效工作目录（便捷入口）。"""
    return get_project_context().effective_cwd


MAX_ADDITIONAL_PATHS = 3


def add_additional_path(path: str) -> None:
    """添加一个额外的项目路径。超过上限时淘汰最早的（FIFO）。"""
    ctx = get_project_context()
    resolved = str(Path(path).resolve())

    # 已存在则跳过
    if resolved in ctx.additional_paths:
        logger.debug(f"路径已存在，跳过: {resolved}")
        return

    # 与主路径相同则跳过
    if ctx.project_path and resolved == ctx.project_path:
        logger.debug(f"与主路径相同，跳过: {resolved}")
        return

    ctx.additional_paths.append(resolved)

    # FIFO 淘汰
    if len(ctx.additional_paths) > MAX_ADDITIONAL_PATHS:
        evicted = ctx.additional_paths.pop(0)
        logger.info(f"额外路径已满({MAX_ADDITIONAL_PATHS})，淘汰最早的: {evicted}")

    logger.debug(f"添加额外项目路径: {resolved}")


def get_all_project_paths() -> list[str]:
    """获取所有项目路径（主路径 + 额外路径）。"""
    ctx = get_project_context()
    paths = []
    if ctx.project_path:
        paths.append(ctx.project_path)
    paths.extend(p for p in ctx.additional_paths if p not in paths)
    return paths
