"""QRClaw 数据目录：默认 ~/.qrclaw，可用环境变量 QRCLAW_HOME 指向别处（测试用临时目录隔离本机数据）。"""
import os
from pathlib import Path


def qrclaw_home() -> Path:
    return Path(os.environ.get("QRCLAW_HOME") or Path.home() / ".qrclaw").expanduser()
