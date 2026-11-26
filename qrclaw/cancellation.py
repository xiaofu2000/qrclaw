"""
任务取消协调模块

提供统一的取消异常、可追踪子进程与级联子任务的 CancelToken、
会话级取消注册表以及线程上下文辅助函数。
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from typing import Iterator

from qrclaw.logger import get_logger

logger = get_logger("qrclaw.cancellation")

DEFAULT_CANCEL_MESSAGE = "任务已由用户停止"


class TaskCancelledError(Exception):
    """用户主动停止任务时抛出的控制流异常，必须穿透工具与循环层。"""

    def __init__(self, message: str = DEFAULT_CANCEL_MESSAGE):
        super().__init__(message)


def terminate_process_tree(proc: subprocess.Popen, grace_seconds: float = 0.15) -> None:
    """安全终止子进程及其关联进程组（先 SIGTERM 短暂等待，超时后 SIGKILL）。"""
    if proc.poll() is not None:
        return

    pgid: int | None = None
    if os.name != "nt":
        try:
            candidate = os.getpgid(proc.pid)
            if candidate != os.getpgrp():
                pgid = candidate
        except OSError:
            pgid = None

    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            try:
                proc.terminate()
            except OSError:
                pass
    else:
        try:
            proc.terminate()
        except OSError:
            pass

    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=0.15)
    except subprocess.TimeoutExpired:
        pass


class CancelToken:
    """线程安全的取消令牌，支持子进程终止与父子任务级联取消。"""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._procs: set[subprocess.Popen] = set()
        self._children: set[CancelToken] = set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def check(self) -> None:
        if self._event.is_set():
            raise TaskCancelledError()

    def spawn_child(self) -> "CancelToken":
        """创建级联响应当前令牌取消信号的子令牌。"""
        child = CancelToken()
        with self._lock:
            if self._event.is_set():
                child.cancel()
            else:
                self._children.add(child)
        return child

    def detach_child(self, child: "CancelToken") -> None:
        with self._lock:
            self._children.discard(child)

    def register_process(self, proc: subprocess.Popen) -> None:
        """登记运行中的子进程；若当前已处于取消状态则立即终止并抛异常。"""
        already_cancelled = False
        with self._lock:
            if self._event.is_set():
                already_cancelled = True
            else:
                self._procs.add(proc)
        if already_cancelled:
            terminate_process_tree(proc)
            raise TaskCancelledError()

    def unregister_process(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._procs.discard(proc)

    @contextmanager
    def track_process(self, proc: subprocess.Popen) -> Iterator[subprocess.Popen]:
        self.register_process(proc)
        try:
            yield proc
        finally:
            self.unregister_process(proc)

    def cancel(self) -> bool:
        """置位取消事件，立即终止关联子进程组并级联取消所有子令牌。"""
        with self._lock:
            first_time = not self._event.is_set()
            self._event.set()
            procs = list(self._procs)
            children = list(self._children)

        for proc in procs:
            try:
                terminate_process_tree(proc)
            except Exception as exc:
                logger.warning("终止关联子进程失败 (pid=%s): %s", getattr(proc, "pid", "?"), exc)

        for child in children:
            child.cancel()

        return first_time


_thread_local = threading.local()
_registry_lock = threading.RLock()
_session_tokens: dict[tuple[str, str], CancelToken] = {}
_sub_tokens: dict[tuple[str, str], set[CancelToken]] = {}


def _session_key(sessions_dir: Path, session_id: str) -> tuple[str, str]:
    return (str(Path(sessions_dir).resolve()), session_id)


@contextmanager
def bind_cancel_token(token: CancelToken | None) -> Iterator[CancelToken | None]:
    """在当前线程上下文中绑定 CancelToken，退出时恢复原值。"""
    previous = getattr(_thread_local, "token", None)
    _thread_local.token = token
    try:
        yield token
    finally:
        _thread_local.token = previous


def get_cancel_token() -> CancelToken | None:
    """获取当前线程绑定的 CancelToken。"""
    return getattr(_thread_local, "token", None)


def check_cancelled() -> None:
    """检查当前线程是否已被取消，若是则立即抛出 TaskCancelledError。"""
    token = get_cancel_token()
    if token is not None and token.is_cancelled():
        raise TaskCancelledError()


def interruptible_sleep(seconds: float) -> None:
    """可被当前线程 CancelToken 立即唤醒中断的等待。"""
    check_cancelled()
    if seconds <= 0:
        return
    token = get_cancel_token()
    if token is not None:
        if token.wait(seconds):
            raise TaskCancelledError()
    else:
        time.sleep(seconds)


def register_session_token(
    sessions_dir: Path,
    session_id: str,
    token: CancelToken | None = None,
) -> CancelToken:
    """按 (sessions_dir_resolved, session_id) 注册活跃主会话的 CancelToken。"""
    active = token or CancelToken()
    key = _session_key(sessions_dir, session_id)
    with _registry_lock:
        _session_tokens[key] = active
    return active


def unregister_session_token(
    sessions_dir: Path,
    session_id: str,
    token: CancelToken | None = None,
) -> None:
    """注销主会话的 CancelToken；传入 token 时仅在引用一致时移除。"""
    key = _session_key(sessions_dir, session_id)
    with _registry_lock:
        current = _session_tokens.get(key)
        if current is not None and (token is None or current is token):
            _session_tokens.pop(key, None)


def get_session_token(sessions_dir: Path, session_id: str) -> CancelToken | None:
    """查询指定主会话当前注册的 CancelToken。"""
    if not session_id:
        return None
    key = _session_key(sessions_dir, session_id)
    with _registry_lock:
        return _session_tokens.get(key)


def register_sub_agent_token(
    sessions_dir: Path,
    parent_session_id: str,
    parent_token: CancelToken | None = None,
) -> tuple[CancelToken, CancelToken | None]:
    """为父会话的关联子任务创建并注册级联 CancelToken。"""
    owner = parent_token or get_cancel_token() or (
        get_session_token(sessions_dir, parent_session_id) if parent_session_id else None
    )
    child = owner.spawn_child() if owner is not None else CancelToken()
    if parent_session_id:
        key = _session_key(sessions_dir, parent_session_id)
        with _registry_lock:
            # 若创建瞬间父会话刚被取消或替换，确保同步状态
            latest_parent = _session_tokens.get(key)
            if latest_parent is not None and latest_parent.is_cancelled():
                child.cancel()
            _sub_tokens.setdefault(key, set()).add(child)
    return child, owner


def unregister_sub_agent_token(
    sessions_dir: Path,
    parent_session_id: str,
    child_token: CancelToken,
    parent_token: CancelToken | None = None,
) -> None:
    """注销子任务 CancelToken 并解除父子引用。"""
    if parent_token is not None:
        parent_token.detach_child(child_token)
    if parent_session_id:
        key = _session_key(sessions_dir, parent_session_id)
        with _registry_lock:
            bucket = _sub_tokens.get(key)
            if bucket is not None:
                bucket.discard(child_token)
                if not bucket:
                    _sub_tokens.pop(key, None)


def cancel_session_run(sessions_dir: Path, session_id: str) -> bool:
    """触发指定主会话及其全部关联子任务的取消信号。"""
    key = _session_key(sessions_dir, session_id)
    with _registry_lock:
        main_token = _session_tokens.get(key)
        children = list(_sub_tokens.get(key, ()))

    if main_token is None and not children:
        return False

    if main_token is not None:
        main_token.cancel()
    for child in children:
        child.cancel()
    return True
