"""
配置管理模块

统一管理用户配置，所有配置文件存放在 ~/.qrclaw/ 目录下：
- ~/.qrclaw/config        用户配置（API Key 等）
- ~/.qrclaw/MEMORY.md     中期记忆
- ~/.qrclaw/sessions/     会话历史
- ~/.qrclaw/logs/         日志文件
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.config_manager")

# 配置目录
CONFIG_DIR = Path.home() / ".qrclaw"
CONFIG_FILE = CONFIG_DIR / "config"

# 默认配置
DEFAULT_CONFIG = """# QRClaw 配置文件
# 配置说明：https://github.com/fu-qingrong/qrclaw

# ── Agent 配置 ───────────────────────────────────────
AGENT_NAME=QRClaw
MAX_ITERATIONS=50

# ── LLM 渠道配置 ─────────────────────────────────────
# 支持以下渠道，修改 LLM_PROVIDER 切换：
#
#   openai  —— OpenAI 官方 或 任何兼容 OpenAI 接口的服务
#              （如 DeepSeek、通义千问、本地 Ollama 等）
#              需要配置：OPENAI_API_KEY、OPENAI_MODEL
#              可选配置：OPENAI_BASE_URL（不填则走 OpenAI 官方）
#
#   vertex  —— Google Vertex AI（使用 Express API Key）
#              需要配置：OPENAI_API_KEY（填 Vertex Express Key）、OPENAI_MODEL
#
LLM_PROVIDER=openai

OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o
OPENAI_BASE_URL=
MODEL_MAX_TOKENS=128000

# ── Tavily API（网页搜索）────────────────────────────
TAVILY_API_KEY=

# ── 日志配置 ─────────────────────────────────────────
LOG_LEVEL=INFO
LOG_MAX_DAYS=30
LOG_TO_FILE=true
LOG_TO_CONSOLE=true
LOG_CONSOLE_LEVEL=WARNING
"""


def ensure_config_dir():
    """确保配置目录存在"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger.debug(f"配置目录: {CONFIG_DIR}")


def migrate_from_env():
    """从项目目录的 .env 迁移配置到 ~/.qrclaw/config"""
    # 查找项目目录下的 .env 文件
    project_env = Path.cwd() / ".env"
    
    if not project_env.exists():
        return False
    
    logger.info(f"发现项目目录下的 .env 文件: {project_env}")
    
    # 读取 .env 内容
    try:
        env_content = project_env.read_text(encoding="utf-8")
        
        # 写入到 ~/.qrclaw/config
        ensure_config_dir()
        CONFIG_FILE.write_text(env_content, encoding="utf-8")
        
        logger.info(f"已迁移配置到: {CONFIG_FILE}")
        logger.info("建议删除项目目录下的 .env 文件，避免误提交到 git")
        
        return True
    except Exception as e:
        logger.error(f"迁移配置失败: {e}", exc_info=True)
        return False


def init_config():
    """
    初始化配置文件
    
    1. 如果 ~/.qrclaw/config 不存在，尝试从 .env 迁移
    2. 如果都没有，创建默认配置
    """
    ensure_config_dir()
    
    if CONFIG_FILE.exists():
        logger.info(f"使用配置文件: {CONFIG_FILE}")
        return
    
    # 尝试从 .env 迁移
    if migrate_from_env():
        return
    
    # 创建默认配置
    CONFIG_FILE.write_text(DEFAULT_CONFIG, encoding="utf-8")
    logger.info(f"创建默认配置文件: {CONFIG_FILE}")
    logger.info("请编辑配置文件，填入你的 API Key")


def load_config():
    """
    加载配置
    
    优先级：
    1. ~/.qrclaw/config（用户配置）
    2. .env（兼容旧版本）
    3. 环境变量
    """
    # 加载 ~/.qrclaw/config
    if CONFIG_FILE.exists():
        load_dotenv(CONFIG_FILE)
        logger.debug(f"已加载配置: {CONFIG_FILE}")
    
    # 兼容：加载项目目录下的 .env
    project_env = Path.cwd() / ".env"
    if project_env.exists():
        load_dotenv(project_env, override=False)
        logger.debug(f"已加载配置: {project_env}")


def get_config_path() -> Path:
    """获取配置文件路径"""
    return CONFIG_FILE


def get_config_dir() -> Path:
    """获取配置目录路径"""
    return CONFIG_DIR