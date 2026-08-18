"""
Memory Extraction 节点测试

测试阈值触发、LLM 集成、批量写入
"""
import pytest
import os
from unittest.mock import MagicMock, patch
# LongTermMemory 已迁移到 wiki 系统，测试使用 MockMemory
from qrclaw.graph.nodes.memory_extraction import (
    MemoryExtractionNode,
    MemoryExtractionIntegration,
)
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, WikiPageSchema


class MockMessage:
    """模拟消息对象"""
    def __init__(self, role: str, content: str, uuid: str = None):
        self.type = role
        self.role = role
        self.content = content
        self.uuid = uuid or f"msg-{hash(content) % 10000}"


class MockMemory:
    """模拟记忆存储"""
    def __init__(self):
        self.entries = []
        self.saved = []
        self.index = MagicMock()
        self.index.all_entries.return_value = []

    def save_entry(self, name, description, content, memory_type):
        self.saved.append({
            "name": name,
            "description": description,
            "content": content,
            "memory_type": memory_type,
        })
        return True

    def list_entries(self, memory_type=None):
        if memory_type:
            return [e for e in self.entries if e.get("type") == memory_type]
        return self.entries

    def save_page(self, name, content, description="", tags=None, related=None):
        """记录新建 Wiki 页面。"""
        self.saved.append({
            "name": name,
            "description": description,
            "content": content,
            "tags": tags or [],
            "related": related or [],
        })
        return True

    def append_page(self, name, content, tags=None, related=None):
        """记录追加 Wiki 页面。"""
        return self.save_page(name, content, tags=tags, related=related)

    def fuzzy_find_name(self, name):
        return None


class TestShouldExtract:
    """测试 should_extract 阈值判断"""

    def test_below_init_threshold(self):
        """测试：未达到初始化阈值，不触发"""
        config = ExtractionConfig(
            minimum_message_tokens_to_init=500,
            minimum_tokens_between_update=200,
        )
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, config)

        messages = [MockMessage("user", "Hello world")]
        result = extractor.should_extract(messages, token_count=100)

        assert result is False
        assert extractor._is_initialized is False

    def test_reaches_init_threshold(self):
        """测试：达到初始化阈值，触发"""
        config = ExtractionConfig(
            minimum_message_tokens_to_init=500,
            minimum_tokens_between_update=200,
        )
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, config)

        # 直接传入 token_count=500，与阈值相等，触发
        messages = [MockMessage("user", "Hello " * 125)]
        result = extractor.should_extract(messages, token_count=500)

        assert result is True
        assert extractor._is_initialized is True

    def test_below_update_interval(self):
        """测试：Token 增长不足，不触发"""
        config = ExtractionConfig(
            minimum_message_tokens_to_init=100,
            minimum_tokens_between_update=200,
        )
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, config)

        # 先初始化
        extractor._is_initialized = True
        extractor._tokens_at_last_extraction = 500

        messages = [MockMessage("user", "Hello")]
        result = extractor.should_extract(messages, token_count=650)  # 只增长150

        assert result is False

    def test_sufficient_token_growth(self):
        """测试：Token 增长足够，触发"""
        config = ExtractionConfig(
            minimum_message_tokens_to_init=100,
            minimum_tokens_between_update=200,
            tool_calls_between_updates=0,  # 跳过工具调用检查
        )
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, config)

        # 先初始化
        extractor._is_initialized = True
        extractor._tokens_at_last_extraction = 500

        # 传入 token_count=800，增长 300，满足阈值
        messages = [MockMessage("user", "Task " * 100)]
        result = extractor.should_extract(messages, token_count=800)

        assert result is True


class TestCheckAndExtract:
    """测试 check_and_extract 方法"""

    def test_no_trigger_when_threshold_not_met(self):
        """测试：阈值不满足时不触发"""
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory)

        messages = [MockMessage("user", "Short message")]
        result = extractor.check_and_extract(messages, token_count=100)

        assert result is False
        assert extractor.get_pending_count() == 0

    def test_trigger_and_queue_extraction(self):
        """测试：触发后加入队列"""
        mock_memory = MockMemory()
        config = ExtractionConfig(
            minimum_message_tokens_to_init=100,
            minimum_tokens_between_update=50,
            tool_calls_between_updates=0,  # 不检查工具调用
        )
        extractor = MemoryExtractionNode(mock_memory, config)

        messages = [
            MockMessage("user", "Task description " * 50),
            MockMessage("assistant", "I'll help with that"),
        ]
        result = extractor.check_and_extract(messages, token_count=200)

        assert result is True
        assert extractor.get_pending_count() == 1

    def test_updates_state_on_trigger(self):
        """测试：触发后更新状态"""
        mock_memory = MockMemory()
        config = ExtractionConfig(
            minimum_message_tokens_to_init=100,
            minimum_tokens_between_update=50,
            tool_calls_between_updates=0,
        )
        extractor = MemoryExtractionNode(mock_memory, config)

        messages = [MockMessage("user", "Task " * 50)]
        extractor.check_and_extract(messages, token_count=200)

        assert extractor._tokens_at_last_extraction == 200
        assert extractor._is_initialized is True


class TestLLMAnalysis:
    """测试 LLM 分析集成"""

    def test_analyze_with_llm_success(self):
        """测试：LLM 分析成功"""
        analyzer = MagicMock()
        analyzer.analyze.return_value = ExtractionSchema(
            needs_update=True,
            pages=[],
        )
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, llm_analyzer=analyzer)

        result = extractor._get_runner().analyze_with_llm({
            "index_summary": "暂无页面",
            "messages_text": "请分析",
        })

        assert result.needs_update is True
        analyzer.analyze.assert_called_once_with("暂无页面", "请分析")

    def test_analyze_with_llm_no_extraction_needed(self):
        """测试：LLM 判定无需提取"""
        analyzer = MagicMock()
        analyzer.analyze.return_value = ExtractionSchema(needs_update=False, pages=[])
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, llm_analyzer=analyzer)

        result = extractor._get_runner().analyze_with_llm({
            "index_summary": "暂无页面",
            "messages_text": "请分析",
        })

        assert result.needs_update is False

    def test_analyze_with_llm_failure(self):
        """测试：LLM 调用失败"""
        analyzer = MagicMock()
        analyzer.analyze.side_effect = Exception("API Error")
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, llm_analyzer=analyzer)

        result = extractor._get_runner().analyze_with_llm({
            "index_summary": "暂无页面",
            "messages_text": "请分析",
        })

        assert result is None


class TestFlushPending:
    """测试批量写入"""

    def test_flush_pending_success(self):
        """测试：批量写入成功"""
        analyzer = MagicMock()
        analyzer.analyze.return_value = ExtractionSchema(
            needs_update=True,
            pages=[
                WikiPageSchema(
                    action="create",
                    name="用户偏好",
                    description="测试偏好",
                    content="用户偏好简洁风格",
                    tags=["user"],
                    related=[],
                )
            ],
        )

        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory, llm_analyzer=analyzer)

        # 手动加入一个待处理项（Wiki 架构用 tags 替代 memory_type）
        with extractor._pending_lock:
            extractor._pending_extractions.append({
                "index_summary": "测试索引",
                "messages_text": "测试提取",
            })

        count = extractor.flush_pending()

        assert count == 1
        assert len(mock_memory.saved) == 1
        assert mock_memory.saved[0]["name"] is not None


class TestMemoryExtractionIntegration:
    """测试集成类"""

    def test_on_react_loop_end_calls_extractor(self):
        """测试：on_react_loop_end 调用 extractor"""
        mock_memory = MockMemory()
        config = ExtractionConfig(
            minimum_message_tokens_to_init=100,
            minimum_tokens_between_update=50,
            tool_calls_between_updates=0,
        )
        extractor = MemoryExtractionNode(mock_memory, config)

        integration = MemoryExtractionIntegration(extractor)

        # 创建模拟 session - 传入足够长的内容满足阈值
        session = MagicMock()
        session.messages = [MockMessage("user", "Test " * 200)]  # 约 1000 chars

        integration.on_react_loop_end(session, current_round=1)

        assert extractor._is_initialized is True


class TestTokenEstimation:
    """测试 Token 估算"""

    def test_estimate_tokens_text(self):
        """测试：纯文本估算"""
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory)

        messages = [MockMessage("user", "Hello world")]
        tokens = extractor._estimate_tokens(messages)

        assert tokens > 0

    def test_estimate_tokens_mixed_content(self):
        """测试：混合内容估算"""
        mock_memory = MockMemory()
        extractor = MemoryExtractionNode(mock_memory)

        messages = [
            MockMessage("user", "Hello"),
            MagicMock(
                type="assistant",
                role="assistant",
                content=[
                    {"type": "text", "text": "Response text"},
                    {"type": "tool_use", "input": {"key": "value"}},
                ]
            ),
        ]

        tokens = extractor._estimate_tokens(messages)

        assert tokens > 0


class TestExtractionConfig:
    """测试配置"""

    def test_default_values(self):
        """测试：默认配置值合理"""
        config = ExtractionConfig()

        # 验证默认阈值不是原始的 10000/5000
        assert config.minimum_message_tokens_to_init < 10000
        assert config.minimum_tokens_between_update < 5000

    def test_custom_config(self):
        """测试：自定义配置"""
        config = ExtractionConfig(
            minimum_message_tokens_to_init=1000,
            minimum_tokens_between_update=500,
            tool_calls_between_updates=5,
        )

        assert config.minimum_message_tokens_to_init == 1000
        assert config.minimum_tokens_between_update == 500
        assert config.tool_calls_between_updates == 5
