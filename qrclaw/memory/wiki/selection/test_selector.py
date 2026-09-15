"""Wiki 页面选择器的离线回归测试。"""

from types import SimpleNamespace

from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.selection import WikiPageSelector
from qrclaw.memory.wiki.selection.wiki_selector import _format_conversation_history


def test_format_conversation_history():
    """只保留最近的有效对话，忽略系统和工具消息。"""
    messages = [
        {"role": "system", "content": "系统提示"},
        {"role": "user", "content": "较早的问题"},
        {"role": "tool", "content": "工具输出"},
        {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
        {"role": "assistant", "content": "QRClaw 架构"},
        {"role": "user", "content": "记忆系统呢？"},
    ]
    result = _format_conversation_history(messages, keep_rounds=2)
    assert "**用户**" in result and "**助手**" in result
    assert "QRClaw" in result and "记忆系统" in result
    assert "较早的问题" in result and "工具输出" not in result
    assert "较早的问题" not in _format_conversation_history(messages, keep_rounds=1)
    assert "无" in _format_conversation_history([])


def test_wiki_page_selector(tmp_path, monkeypatch):
    """使用固定模型响应验证选页和正文加载，临时目录由 pytest 清理。"""
    wiki = WikiMemory(tmp_path)
    wiki.save_page(name="Memory 系统", content="记忆系统的实现", tags=["架构"])
    response = SimpleNamespace(
        content='{"selected": [{"name": "Memory 系统", "reason": "与问题相关", "relevance": 0.9}]}'
    )
    monkeypatch.setattr(
        "qrclaw.memory.wiki.selection.wiki_selector.get_llm_service",
        lambda: SimpleNamespace(chat=lambda _: response),
    )
    selector = WikiPageSelector(wiki)
    result = selector.select("记忆系统如何工作？", [])
    assert result.selected[0].name == "Memory 系统"
    assert (
        selector.select_with_pages("记忆系统如何工作？", [])[0].content
        == "记忆系统的实现"
    )
