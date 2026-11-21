"""仓库级 pytest 配置：在收集任何测试、导入任何 qrclaw 模块之前隔离本机数据。

配置、会话、记忆和日志都落在临时目录；本机 shell 里导出的 QRClaw 环境变量（如 LITELLM_MODEL）也不影响测试。
放在仓库根目录，qrclaw/tests 以外的测试（如 sandbox/tests）同样生效。
"""
import os
import tempfile

os.environ["QRCLAW_HOME"] = tempfile.mkdtemp(prefix="qrclaw-test-home-")

from qrclaw.config_manager import ENV_OVERRIDES  # noqa: E402

for _name in ENV_OVERRIDES:
    os.environ.pop(_name, None)
