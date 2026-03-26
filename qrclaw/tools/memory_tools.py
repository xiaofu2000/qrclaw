from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.memory import LongTermMemory
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.memory")

class WriteMemoryArgs(BaseModel):
    content: str = Field(description="要记录的重要信息，使用 Markdown 格式")
    title: str = Field(description="可选的标题，用于分类和组织记忆", default="")

class ReadMemoryArgs(BaseModel):
    pass  # 不需要参数，读取全部记忆

@register(description="写入中期记忆，仅用于记录用户偏好、项目配置等需要跨会话复用的信息，任务结果、调研报告等不要写入", args_model=WriteMemoryArgs)
def write_memory(content: str, title: str = "") -> str:
    """
    写入中期记忆

    Args:
        content: 要记录的内容（Markdown 格式）
        title: 可选的标题

    Returns:
        str: 操作结果
    """
    logger.debug(f"写入中期记忆: {title or '无标题'}")
    try:
        from qrclaw.agent import get_workspace
        from qrclaw.workspace import Workspace
        ws = get_workspace() or Workspace("default")
        memory = LongTermMemory(ws.memory_file)
        success = memory.append(content, title if title else None)

        if success:
            result = f"已写入中期记忆: {title if title else '无标题'}"
            logger.info(result)
            return result
        else:
            error_msg = "写入中期记忆失败"
            logger.error(error_msg)
            return error_msg
    except Exception as e:
        error_msg = f"错误：写入中期记忆失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg

@register(description="读取中期记忆，查看之前记录的重要信息", args_model=ReadMemoryArgs)
def read_memory() -> str:
    """
    读取中期记忆

    Returns:
        str: 记忆内容（Markdown 格式）
    """
    logger.debug("读取中期记忆")
    try:
        from qrclaw.agent import get_workspace
        from qrclaw.workspace import Workspace
        ws = get_workspace() or Workspace("default")
        memory = LongTermMemory(ws.memory_file)
        content = memory.load()

        if not content or content.strip() == "# QRClaw 中期记忆":
            logger.info("中期记忆为空")
            return "中期记忆为空，还没有记录任何信息"

        logger.info(f"读取中期记忆成功: {len(content)} 字符")
        return content
    except Exception as e:
        error_msg = f"错误：读取中期记忆失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg
