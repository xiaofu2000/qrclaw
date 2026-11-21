"""
心跳机制

按固定间隔用后台子 Agent 执行 HEARTBEAT.md 中列出的任务。
长期记忆（Wiki）由提取与整理流程自动维护，不属于心跳任务。
"""
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.heartbeat")

# 默认心跳间隔（秒）
DEFAULT_INTERVAL = 3600  # 1 小时


class Heartbeat:
    """心跳管理器"""
    
    def __init__(self, interval: int = DEFAULT_INTERVAL):
        self.interval = interval
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_heartbeat: Optional[datetime] = None
        self.on_trigger: Optional[Callable] = None  # 触发时的回调函数
        self._stop_event = threading.Event()
        
    def start(self, on_trigger: Callable = None):
        """
        启动心跳线程。
        
        Args:
            on_trigger: 触发时的回调函数
        """
        if self.running:
            logger.warning("Heartbeat 已在运行中")
            return
            
        self.on_trigger = on_trigger
        self.running = True
        self._stop_event.clear()
        
        self.thread = threading.Thread(
            target=self._run_loop,
            name="heartbeat",
            daemon=True
        )
        self.thread.start()
        logger.info(f"Heartbeat 已启动，间隔 {self.interval} 秒")
        
    def stop(self):
        """停止心跳线程"""
        if not self.running:
            return
            
        self.running = False
        self._stop_event.set()
        
        if self.thread:
            self.thread.join(timeout=5)
            logger.info("Heartbeat 已停止")
            
    def _run_loop(self):
        """心跳主循环"""
        while self.running and not self._stop_event.wait(self.interval):
            try:
                self._do_heartbeat()
            except Exception as e:
                logger.error(f"Heartbeat 执行失败: {e}", exc_info=True)
                
    def _do_heartbeat(self):
        """执行心跳任务"""
        self.last_heartbeat = datetime.now()
        logger.info(f"Heartbeat 触发: {self.last_heartbeat}")
        
        if self.on_trigger:
            try:
                self.on_trigger()
            except Exception as e:
                logger.error(f"Heartbeat 回调执行失败: {e}", exc_info=True)


# 全局心跳实例
_heartbeat: Optional[Heartbeat] = None


def get_heartbeat() -> Optional[Heartbeat]:
    """获取全局心跳实例"""
    return _heartbeat


def start_heartbeat(
    interval: int = DEFAULT_INTERVAL,
    on_trigger: Callable = None
) -> Heartbeat:
    """
    启动全局心跳。
    
    Args:
        interval: 心跳间隔（秒）
        on_trigger: 触发时的回调函数
        
    Returns:
        Heartbeat 实例
    """
    global _heartbeat
    
    if _heartbeat and _heartbeat.running:
        logger.warning("Heartbeat 已在运行，跳过启动")
        return _heartbeat
        
    _heartbeat = Heartbeat(interval=interval)
    _heartbeat.start(on_trigger=on_trigger)
    return _heartbeat


def stop_heartbeat():
    """停止全局心跳"""
    global _heartbeat
    
    if _heartbeat:
        _heartbeat.stop()
        _heartbeat = None


def heartbeat_tasks(content: str) -> str:
    """注释之外没有列表条目时视为没有任务，避免空模板触发模型调用。"""
    body = re.sub(r"<!--.*?-->", "", content, flags=re.S)
    has_task = any(re.match(r"\s*([-*+]|\d+[.)])\s+\S", line) for line in body.splitlines())
    return body.strip() if has_task else ""


def execute_heartbeat_tasks(workspace) -> str:
    """
    执行心跳任务（后台子 agent）。

    Args:
        workspace: 当前工作空间
    Returns:
        str: 执行结果摘要；没有任务时返回空字符串
    """
    from qrclaw.agent import run_sub_agent

    heartbeat_file = workspace.heartbeat_file
    if not heartbeat_file.exists():
        logger.warning("HEARTBEAT.md 不存在，跳过心跳任务")
        return ""

    tasks = heartbeat_tasks(heartbeat_file.read_text(encoding="utf-8"))
    if not tasks:
        logger.info("HEARTBEAT.md 中没有任务，跳过心跳")
        return ""

    task = f"""请执行以下心跳任务。静默执行，完成后简要汇报结果。

{tasks}

注意：
1. 不要打扰用户，静默执行
2. 完成后简要汇报：做了什么、结果如何
"""

    logger.info("心跳任务开始执行")

    try:
        # 创建心跳专用子 agent
        result, _ = run_sub_agent(task, workspace, "heartbeat", sandbox_policy_agent_id=workspace.sandbox_policy_agent_id)
        logger.info(f"心跳任务执行完成: {result[:100]}...")
        return result
    except Exception as e:
        logger.error(f"心跳任务执行失败: {e}", exc_info=True)
        return f"执行失败: {e}"


def get_default_heartbeat_content() -> str:
    """生成默认的 HEARTBEAT.md 内容"""
    return """# Heartbeat 任务

<!--
在下方用列表写出需要定期执行的任务，每行一条，例如：
- 检查某个目录下的测试是否通过，失败时把原因追加到指定文件

没有任务条目时，心跳不会调用模型。
长期记忆（Wiki）由系统自动提取和整理，不需要在这里安排记忆维护。
-->

## 任务

"""
