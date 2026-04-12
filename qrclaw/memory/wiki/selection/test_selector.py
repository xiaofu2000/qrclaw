"""
WikiPageSelector 测试

运行方式：
    cd /Users/fuqingrong/Documents/agent开发/qrclaw/qrclaw
    python -m qrclaw.memory.wiki.selection.test_selector
"""

import sys
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from qrclaw.memory.wiki.selection import WikiPageSelector
from qrclaw.memory.wiki.selection.wiki_selector import _format_conversation_history
from qrclaw.memory.wiki import WikiMemory


def test_format_conversation_history():
    """测试对话历史格式化"""
    messages = [
        {"role": "system", "content": "你是一个助手"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮助你的吗？"},
        {"role": "tool", "content": '{"name": "read_file", "result": "..."}'},
        {"role": "assistant", "tool_calls": [{"id": "call_1", "name": "read_file", "arguments": "{}"}]},
        {"role": "user", "content": "我想了解 QRClaw 的架构"},
        {"role": "assistant", "content": "QRClaw 是一个 AI Agent 开发框架..."},
        {"role": "user", "content": "那记忆系统呢？"},
    ]

    # 测试默认 3 轮
    result = _format_conversation_history(messages, keep_rounds=3)
    print("=== 测试 1: 默认 3 轮 ===")
    print(result)
    print()

    # 验证过滤效果 - 最近的3轮包含用户和助手消息
    assert "**用户**" in result
    assert "**助手**" in result
    assert "QRClaw" in result
    assert "tool" not in result.lower()  # 工具调用信息已过滤
    print("✓ 工具调用信息已过滤")
    print()

    # 测试只保留最近对话
    result2 = _format_conversation_history(messages, keep_rounds=2)
    print("=== 测试 2: 最近 2 轮 ===")
    print(result2)
    print()

    # 测试空消息
    result3 = _format_conversation_history([])
    assert "无" in result3
    print("✓ 空消息处理正确")
    print()


def test_wiki_page_selector():
    """测试 WikiPageSelector"""
    # 使用测试目录
    test_dir = Path("/tmp/test_wiki_selector")
    test_dir.mkdir(parents=True, exist_ok=True)

    wiki = WikiMemory(test_dir)

    # 先创建一些测试页面
    wiki.save_page(
        name="QRClaw 项目架构",
        content="QRClaw 是一个 AI Agent 开发框架，包含 Memory 系统、工具系统等核心组件。",
        description="QRClaw AI Agent 开发框架的整体架构设计",
        tags=["架构", "核心"],
    )
    wiki.save_page(
        name="Memory 系统设计",
        content="Memory 系统采用三层架构：短期记忆、中期记忆、长期记忆。",
        description="Memory 系统三层架构设计",
        tags=["架构", "Memory"],
    )
    wiki.save_page(
        name="Python 基础",
        content="Python 是一种高级编程语言...",
        description="Python 编程基础",
        tags=["编程", "Python"],
    )
    wiki.save_page(
        name="工具系统",
        content="工具系统负责注册和管理各类工具...",
        description="QRClaw 工具系统设计",
        tags=["工具", "系统"],
    )

    print("=== 测试 Wiki 索引 ===")
    print(wiki.load_index())
    print()

    # 创建选择器
    selector = WikiPageSelector(wiki)

    # 模拟对话上下文
    test_messages = [
        {"role": "system", "content": "你是一个 AI Agent 开发助手"},
        {"role": "user", "content": "帮我了解一下 QRClaw 项目"},
        {"role": "assistant", "content": "QRClaw 是一个模块化的 AI Agent 开发框架..."},
        {"role": "user", "content": "它的架构是怎样的？"},
        {"role": "assistant", "content": "QRClaw 采用分层架构，包括..."},
    ]

    # 测试选择
    print("=== 测试选择器 ===")
    result = selector.select(
        current_message="Memory 系统是怎么设计的？",
        messages=test_messages,
    )

    print(f"选择理由: {result.reasoning}")
    print(f"选中 {len(result.selected)} 个页面:")
    for page in result.selected:
        print(f"  - {page.name}: {page.reason} (相关性: {page.relevance})")
    print()

    # 测试选择并返回 WikiPage
    print("=== 测试 select_with_pages ===")
    pages = selector.select_with_pages(
        current_message="Memory 系统是怎么设计的？",
        messages=test_messages,
    )
    for page in pages:
        print(f"  - {page.name}: {page.description}")
        print(f"    内容预览: {page.content[:50]}...")

    # 清理测试目录
    import shutil
    shutil.rmtree(test_dir)
    print()
    print("✓ 测试完成")


if __name__ == "__main__":
    print("=" * 60)
    print("WikiPageSelector 功能测试")
    print("=" * 60)
    print()

    test_format_conversation_history()
    test_wiki_page_selector()

    print()
    print("=" * 60)
    print("所有测试通过！")
    print("=" * 60)
