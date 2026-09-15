"""在每轮结束时提取新增对话，写入成功后才持久化检查点。"""
from qrclaw.memory.context.session import Session
from qrclaw.memory.token_utils import count_messages_tokens
from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.runner import ExtractionRunner


class MemoryExtractionNode:
    """同步完成记忆提取，避免临时执行器的后台队列丢失任务。"""

    def __init__(self, memory: WikiMemory, config: ExtractionConfig = None, llm_analyzer=None):
        """绑定工作区存储；运行进度由 Session 的消息检查点保存。"""
        self.config = config or ExtractionConfig()
        self.runner = ExtractionRunner(memory, self.config, llm_analyzer)

    def check_and_extract(self, session: Session) -> bool:
        """检查新增消息阈值，提取成功后标记最后一条消息，失败保留重试范围。"""
        messages = session.messages
        checkpoint = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("memory_extracted")), -1)
        new_messages = messages[checkpoint + 1:]
        threshold = self.config.minimum_tokens_between_update if checkpoint >= 0 else self.config.minimum_message_tokens_to_init
        if not new_messages or count_messages_tokens(new_messages) < threshold:
            return False
        if new_messages[-1].get("tool_calls"):
            return False
        data = self.runner.trigger_extraction(new_messages)
        self._write(data)
        # 检查点与消息一起原子保存，Web 每轮新建 Session 也能继续增量提取。
        updated = [{k: v for k, v in message.items() if k != "memory_extracted"} for message in messages]
        updated[-1]["memory_extracted"] = True
        session.replace_messages(updated)
        return True

    def write(self, content: str) -> int:
        """立即处理主动记忆写入，向工具返回真实写入数量。"""
        return self._write({"index_summary": self.runner.build_index_summary(), "messages_text": content})

    def _write(self, data: dict) -> int:
        """分析和写入共用入口；失败交给调用方处理，不吞掉待提取内容。"""
        extraction = self.runner.analyze_with_llm(data)
        if extraction is None:
            raise RuntimeError("记忆分析失败，检查点未前移")
        return self.runner.process_extraction_result(extraction)
