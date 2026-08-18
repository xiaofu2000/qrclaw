"""配置管理模块测试。"""

import stat

from qrclaw import config_manager


def test_write_config_restricts_sensitive_file_permissions(tmp_path, monkeypatch):
    """配置目录和文件必须只允许当前用户访问。"""
    config_dir = tmp_path / ".qrclaw"
    config_file = config_dir / "config.yaml"
    monkeypatch.setattr(config_manager, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_manager, "CONFIG_FILE", config_file)

    config_manager.set_config("llm.api_key", "test-secret")

    assert stat.S_IMODE(config_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(config_file.stat().st_mode) == 0o600
