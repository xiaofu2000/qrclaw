"""
OpenClaw 兼容层

创建兼容的路径结构，让 OpenClaw 的 skill 能在 QRClaw 中直接运行。

OpenClaw 路径结构：
  ~/.openclaw/agents/main/sessions/
  ~/.openclaw/agents/main/memory/
  ~/.openclaw/agents/main/skills/

QRClaw 路径结构：
  ~/.qrclaw/sessions/
  ~/.qrclaw/memory/
  ~/.qrclaw/skills/

兼容方案：
  创建 ~/.qrclaw/agents/main -> ~/.qrclaw/ (软链接)
  这样访问 ~/.qrclaw/agents/main/sessions/ = ~/.qrclaw/sessions/
"""

import os
import sys
from pathlib import Path
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.compat")

# QRClaw 根目录
QRCLAW_ROOT = Path.home() / ".qrclaw"


def create_compatibility_layer():
    """
    创建 OpenClaw 兼容层
    
    在 ~/.qrclaw/ 下创建 OpenClaw 兼容的路径结构：
    - ~/.qrclaw/agents/main -> ~/.qrclaw/
    
    这样 OpenClaw 的 skill 访问 agents/main/xxx 时，实际上访问 QRClaw 根目录下的 xxx
    """
    # 确保 QRClaw 根目录存在
    QRCLAW_ROOT.mkdir(parents=True, exist_ok=True)
    
    # 目标：创建 ~/.qrclaw/agents/main -> ~/.qrclaw/
    agents_main = QRCLAW_ROOT / "agents" / "main"
    
    # 如果已经存在且是正确的软链接，跳过
    if agents_main.is_symlink():
        target = os.readlink(agents_main)
        if target == str(QRCLAW_ROOT):
            logger.debug(f"兼容路径已存在: {agents_main} -> {target}")
            return
        else:
            # 指向了错误的位置，删除重建
            logger.warning(f"兼容路径指向错误: {agents_main} -> {target}")
            agents_main.unlink()
    
    # 如果存在真实目录，删除它
    if agents_main.exists() and not agents_main.is_symlink():
        logger.warning(f"存在真实目录，删除: {agents_main}")
        import shutil
        shutil.rmtree(agents_main)
    
    try:
        # 确保父目录存在
        agents_main.parent.mkdir(parents=True, exist_ok=True)
        
        # 创建软链接（兼容 Unix 和 Windows）
        if sys.platform == "win32":
            # Windows: 创建 Junction（目录链接）
            import subprocess
            subprocess.run(
                ["mklink", "/J", str(agents_main.absolute()), str(QRCLAW_ROOT.absolute())],
                shell=True,
                check=True,
                capture_output=True
            )
            logger.info(f"创建 Windows Junction: {agents_main} -> {QRCLAW_ROOT}")
        else:
            # Unix/Linux/Mac: 创建符号链接
            agents_main.symlink_to(QRCLAW_ROOT.absolute())
            logger.info(f"创建软链接: {agents_main} -> {QRCLAW_ROOT}")
    
    except Exception as e:
        logger.warning(f"创建兼容路径失败: {agents_main}, 错误: {e}")
        # 如果创建失败，尝试创建真实目录
        try:
            if not agents_main.exists():
                agents_main.mkdir(parents=True, exist_ok=True)
                logger.info(f"创建真实目录: {agents_main}")
        except Exception as e2:
            logger.error(f"创建真实目录也失败: {agents_main}, 错误: {e2}")


def ensure_openclaw_compatibility():
    """
    确保 OpenClaw 兼容性
    
    在 QRClaw 启动时调用，创建必要的兼容路径。
    """
    try:
        create_compatibility_layer()
        logger.debug("OpenClaw 兼容层创建完成")
    except Exception as e:
        logger.warning(f"创建 OpenClaw 兼容层失败: {e}", exc_info=True)


def check_compatibility():
    """
    检查兼容层是否正确
    
    Returns:
        bool: 兼容层是否正确
    """
    agents_main = QRCLAW_ROOT / "agents" / "main"
    
    if not agents_main.exists():
        return False
    
    if agents_main.is_symlink():
        target = os.readlink(agents_main)
        return target == str(QRCLAW_ROOT)
    
    # 如果是真实目录，也算兼容（某些系统无法创建软链接）
    return agents_main.is_dir()


if __name__ == "__main__":
    # 测试兼容层
    print("创建 OpenClaw 兼容层...")
    ensure_openclaw_compatibility()
    print("完成！")
    
    # 检查结果
    print("\n兼容路径检查：")
    agents_main = QRCLAW_ROOT / "agents" / "main"
    
    if agents_main.exists():
        if agents_main.is_symlink():
            target = os.readlink(agents_main)
            print(f"✅ {agents_main} -> {target}")
        else:
            print(f"✅ {agents_main} (真实目录)")
    else:
        print(f"❌ {agents_main} (不存在)")
    
    # 测试访问
    print("\n路径访问测试：")
    test_paths = [
        "agents/main/sessions",
        "agents/main/memory",
        "agents/main/skills",
    ]
    
    for test_path in test_paths:
        full_path = QRCLAW_ROOT / test_path
        if full_path.exists():
            print(f"✅ {full_path} 存在")
        else:
            print(f"⚠️  {full_path} 不存在（这是正常的，如果相关功能未被使用）")