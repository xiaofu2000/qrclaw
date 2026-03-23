"""
心跳机制

定期触发 Agent 执行维护任务，如：
- 审查并清理中期记忆
- 归档旧会话
- 自我反思与学习

Heartbeat.md 格式示例：

# Heartbeat 任务

每隔一段时间（默认 1 小时），Agent 会自动执行以下任务：

## 记忆维护
- 审查 MEMORY.md，清理过时条目
- 合并重复信息
- 保留用户偏好

## 自我反思
- 回顾最近的对话
- 记录学到的新知识
- 总结常见的错误和解决方案
"""
import threading
import time
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
            on_trigger: 触发时的回调函数，接收 heartbeat_file 路径作为参数
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


def get_default_heartbeat_content() -> str:
    """生成默认的 HEARTBEAT.md 内容"""
    return """# Heartbeat 任务

每隔一段时间，Agent 会自动执行以下维护任务。

---

## 记忆维护

- 审查 `MEMORY.md`，清理过时条目
- 合并重复信息
- 保留用户偏好和重要配置

**建议工具**: `review_memory(action='analyze')` 先分析，再决定是否清理

---

## 自我反思

- 回顾最近的对话
- 记录学到的新知识
- 总结常见的错误和解决方案

**建议工具**: `write_memory()` 记录重要发现

---

## 使用说明

1. 修改此文件定义你的心跳任务
2. 心跳触发时，Agent 会读取此文件并执行任务
3. 默认每小时触发一次，可在配置中修改

---

**最后更新**: {time}
""".format(time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))