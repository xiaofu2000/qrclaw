"""
日志系统模块

特性：
- 文件日志：按会话 ID 分文件存储，按天轮转，保留指定天数
- 控制台日志：带颜色的 Rich 格式输出
- 敏感信息过滤：自动脱敏 API Key 等
- 灵活配置：通过环境变量控制日志级别和输出方式
- 统一日志目录：~/.qrclaw/logs/
"""

import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler


class SensitiveInfoFilter(logging.Filter):
    """敏感信息过滤器，自动脱敏 API Key 等"""

    # 需要过滤的敏感信息模式
    SENSITIVE_PATTERNS = [
        # OpenAI API Key
        (r'sk-[a-zA-Z0-9]{48,}', 'sk-***REDACTED***'),
        # Generic API Keys (key=value format)
        (r'(api[_-]?key\s*=\s*)[\w\-]{20,}', r'\1***REDACTED***'),
        # Bearer tokens
        (r'(Bearer\s+)[\w\-\.]{20,}', r'\1***REDACTED***'),
        # Passwords
        (r'(password\s*=\s*)\S+', r'\1***REDACTED***'),
        # Secrets
        (r'(secret\s*=\s*)\S+', r'\1***REDACTED***'),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """过滤日志记录中的敏感信息"""
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            for pattern, replacement in self.SENSITIVE_PATTERNS:
                record.msg = re.sub(pattern, replacement, record.msg, flags=re.IGNORECASE)

        # 同时检查 args 中的字符串参数
        if hasattr(record, 'args') and record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self._filter_value(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self._filter_value(arg) if isinstance(arg, str) else arg
                    for arg in record.args
                )

        return True

    def _filter_value(self, value: str) -> str:
        """过滤字符串值中的敏感信息"""
        for pattern, replacement in self.SENSITIVE_PATTERNS:
            value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
        return value


class QRClawLogger:
    """QRClaw 日志管理器"""

    def __init__(self):
        self._logger: Optional[logging.Logger] = None
        self._console: Optional[Console] = None

    def setup(
        self,
        session_id: str = "default",
        log_level: str = "INFO",
        log_to_file: bool = True,
        log_to_console: bool = True,
        log_max_days: int = 30,
        console_level: str = "WARNING",
        log_dir: Path = None,
    ):
        """
        设置日志系统

        Args:
            session_id: 会话 ID，用于区分不同会话的日志文件
            log_level: 文件日志级别
            log_to_file: 是否输出到文件
            log_to_console: 是否输出到控制台
            log_max_days: 日志文件保留天数
            console_level: 控制台日志级别
            log_dir: 日志目录（由 Workspace 提供，不传则用默认路径）
        """
        # 日志目录：优先用传入的，否则用默认
        if log_dir is None:
            log_dir = Path.home() / ".qrclaw" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # 获取 root logger
        self._logger = logging.getLogger("qrclaw")
        self._logger.setLevel(logging.DEBUG)  # 设置为最低级别，由 handler 控制实际输出

        # 关闭并清除现有 handlers，避免文件句柄泄漏
        for handler in self._logger.handlers[:]:
            handler.close()
            self._logger.removeHandler(handler)

        # 创建 Rich Console
        self._console = Console()

        # 文件日志格式
        file_format = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 控制台日志格式（Rich 已经自带时间戳，这里只保留关键信息）
        console_format = logging.Formatter('%(message)s')

        # 文件日志 handler（按会话 ID 分文件）
        if log_to_file:
            log_file = log_dir / f"qrclaw-{session_id}.log"
            file_handler = TimedRotatingFileHandler(
                filename=str(log_file),
                when='midnight',
                interval=1,
                backupCount=log_max_days,
                encoding='utf-8'
            )
            file_handler.setLevel(getattr(logging, log_level.upper()))
            file_handler.setFormatter(file_format)
            file_handler.addFilter(SensitiveInfoFilter())
            self._logger.addHandler(file_handler)

        # 控制台日志 handler（使用 Rich）
        if log_to_console:
            console_handler = RichHandler(
                console=self._console,
                show_path=True,
                show_time=True,
                rich_tracebacks=True,
                tracebacks_show_locals=True
            )
            console_handler.setLevel(getattr(logging, console_level.upper()))
            console_handler.setFormatter(console_format)
            console_handler.addFilter(SensitiveInfoFilter())
            self._logger.addHandler(console_handler)


        # 静默 root logger，防止第三方库日志通过 root handler 泄漏到终端
        root_logger = logging.getLogger()
        root_logger.handlers.clear()
        root_logger.setLevel(logging.WARNING)

        # 静默常见的"话多"第三方库
        for _lib in [
            "httpx", "httpcore",
            "urllib3", "urllib3.connectionpool",
            "LiteLLM", "LiteLLM Router",
            "openai", "anthropic",
            "asyncio", "aiohttp",
        ]:
            logging.getLogger(_lib).setLevel(logging.WARNING)

        # 记录初始化日志
        self._logger.info("=" * 60)
        self._logger.info(f"QRClaw 日志系统初始化完成 (会话: {session_id})")
        self._logger.info(f"日志级别: {log_level} (文件) / {console_level} (控制台)")
        self._logger.info(f"日志目录: {log_dir}")
        self._logger.info(f"日志文件: qrclaw-{session_id}.log")
        self._logger.info(f"文件日志: {'启用' if log_to_file else '禁用'}")
        self._logger.info(f"控制台日志: {'启用' if log_to_console else '禁用'}")
        self._logger.info(f"日志保留: {log_max_days} 天")
        self._logger.info("=" * 60)

    def get_logger(self, name: str = "qrclaw") -> logging.Logger:
        """
        获取 logger 实例。

        直接返回 logging 注册表中的 logger，不触发任何初始化。
        handler 由 setup() 统一管理，调用方无需关心。
        """
        return logging.getLogger(name)

    @property
    def console(self) -> Console:
        """获取 Rich Console 实例"""
        if self._console is None:
            self._console = Console()
        return self._console


# 全局日志管理器实例
_logger_manager = QRClawLogger()


def setup_logger(
    session_id: str = "default",
    log_level: str = "INFO",
    log_to_file: bool = True,
    log_to_console: bool = True,
    log_max_days: int = 30,
    console_level: str = "WARNING",
    log_dir: Path = None,
):
    """
    设置日志系统（全局函数）

    Args:
        session_id: 会话 ID，用于区分不同会话的日志文件
        log_level: 文件日志级别
        log_to_file: 是否输出到文件
        log_to_console: 是否输出到控制台
        log_max_days: 日志文件保留天数
        console_level: 控制台日志级别
        log_dir: 日志目录（由 Workspace 提供）
    """
    _logger_manager.setup(
        session_id=session_id,
        log_level=log_level,
        log_to_file=log_to_file,
        log_to_console=log_to_console,
        log_max_days=log_max_days,
        console_level=console_level,
        log_dir=log_dir,
    )


def get_logger(name: str = "qrclaw") -> logging.Logger:
    """
    获取 logger 实例（全局函数）

    Args:
        name: logger 名称，默认为 'qrclaw'

    Returns:
        logging.Logger: logger 实例
    """
    return _logger_manager.get_logger(name)


def get_console() -> Console:
    """获取 Rich Console 实例"""
    return _logger_manager.console
