"""
pytest 配置文件

提供测试 fixtures 和通用配置。
"""
import pytest
import tempfile
from pathlib import Path


@pytest.fixture
def temp_dir():
    """临时目录 fixture，测试结束后自动清理"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_file(temp_dir):
    """临时文件 fixture"""
    file_path = temp_dir / "test.txt"
    file_path.write_text("hello world", encoding="utf-8")
    return file_path