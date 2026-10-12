"""自动记忆的闲置、冷却、持久化恢复与原始正文保护回归测试。"""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from qrclaw.graph.nodes.memory_extraction import MemoryExtractionIntegration, MemoryExtractionNode
from qrclaw.graph.runner import GraphRunner
from qrclaw.memory.compression.compressor import summarize
from qrclaw.memory.context.session import Session
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, WikiPageSchema, ConsolidatePageSchema
from qrclaw.memory.wiki.wiki_memory import WikiMemory
from qrclaw.server.app import create_app
from qrclaw.server.session_service import SessionService
from qrclaw.workspace import Workspace


@pytest.fixture(autouse=True)
def provide_fake_source_references(monkeypatch):
    """旧假模型迁移到来源协议；未知或显式来源保持原样供独立测试检查。"""
    import re
    from qrclaw.memory.wiki.extraction.runner import ExtractionRunner
    original = ExtractionRunner.analyze_with_llm
    def analyze(runner, data):
        result = original(runner, data)
        sources = re.findall(r"\[来源 ([a-f0-9]+)；user；", data["messages_text"])
        if result and sources:
            for page in result.pages:
                if not page.source_ids:
                    page.source_ids = sources
        return result
    monkeypatch.setattr(ExtractionRunner, "analyze_with_llm", analyze)


class ManualTimer:
    """用可手动触发的计时器验证时间边界，无需等待真实的几分钟。"""
    timers = []

    def __init__(self, interval, function):
        """保留间隔和回调，供测试推进虚拟时间。"""
        self.interval = interval
        self.function = function
        self.cancelled = False
        self.timers.append(self)

    def start(self):
        """等待测试显式触发。"""

    def cancel(self):
        """模拟取消尚未执行的闲置任务。"""
        self.cancelled = True

    def fire(self):
        """只有未取消的任务可以启动后台处理。"""
        if not self.cancelled:
            self.function()


class ImmediateThread:
    """同步执行后台回调，单独的真实线程测试负责覆盖并发。"""

    def __init__(self, target, **_kwargs):
        """记录工作内容，保留线程接口。"""
        self.target = target
        self.running = False

    def start(self):
        """立即执行一次批次，保证测试观察到完成状态。"""
        self.running = True
        try:
            self.target()
        finally:
            self.running = False

    def is_alive(self):
        """返回当前工作状态。"""
        return self.running


@pytest.fixture
def virtual_time(monkeypatch):
    """控制闲置和冷却计时，同时隔离测试使用的后台调度。"""
    clock = SimpleNamespace(now=1000.0)
    ManualTimer.timers = []
    monkeypatch.setattr("qrclaw.graph.nodes.memory_extraction.time.time", lambda: clock.now)
    monkeypatch.setattr("qrclaw.graph.nodes.memory_extraction.Timer", ManualTimer)
    monkeypatch.setattr("qrclaw.graph.nodes.memory_extraction.Thread", ImmediateThread)
    monkeypatch.setattr("qrclaw.agent._runner", GraphRunner())
    return clock


def _turns(count, start=0):
    """生成短小、具有稳定消息 ID 的完整对话。"""
    return [message for i in range(start, start + count) for message in (
        {"role": "user", "content": f"用户偏好{i}", "uuid": f"u{i}"},
        {"role": "assistant", "content": f"已确认偏好{i}", "uuid": f"a{i}"},
    )]


def _analyzer(needs_update=True):
    """只返回确定的记忆结果，测试不调用外部模型。"""
    analyzer = MagicMock()
    analyzer.analyze.return_value = ExtractionSchema(needs_update=needs_update, pages=[
        WikiPageSchema(action="create", name="偏好", content="喜欢中文回复")
    ] if needs_update else [])
    analyzer.consolidate.return_value = ConsolidatePageSchema(content="喜欢中文回复")
    return analyzer


def test_idle_batch_waits_and_new_request_resets_timer(tmp_path, virtual_time):
    """三轮短对话等待闲置，新请求期间禁止旧计时器启动写入。"""
    analyzer = _analyzer()
    node = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    integration = MemoryExtractionIntegration(node)
    messages = _turns(3)
    integration.on_react_loop_end(SimpleNamespace(messages=messages))
    old_timer = integration._timer
    assert old_timer.interval == 120
    assert node.get_stats()["pending_turns"] == 3
    assert analyzer.analyze.call_count == 0

    virtual_time.now = 1050
    integration.on_run_start()
    old_timer.fire()
    integration._start_worker()  # 即使旧回调已经开始，也要检查正在运行的主请求。
    analyzer.analyze.assert_not_called()
    virtual_time.now = 1060
    messages += _turns(1, 3)
    integration.on_react_loop_end(SimpleNamespace(messages=messages))
    assert integration._timer.interval == 120
    virtual_time.now = 1180
    integration._timer.fire()
    assert analyzer.analyze.call_count == 1
    text = analyzer.analyze.call_args.args[1]
    assert all(f"用户偏好{i}" in text for i in range(4))
    assert node.get_pending_count() == 0
    integration.close()


def test_automatic_batches_obey_five_minute_cooldown(tmp_path, virtual_time):
    """闲置已满足时仍等待冷却，并且无需更新也推进进度。"""
    analyzer = _analyzer(False)
    node = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    integration = MemoryExtractionIntegration(node)
    messages = _turns(3)
    integration.on_react_loop_end(SimpleNamespace(messages=messages))
    virtual_time.now = 1120
    integration._timer.fire()
    assert node.get_stats()["total_extractions"] == 1

    virtual_time.now = 1130
    messages += _turns(3, 3)
    integration.on_react_loop_end(SimpleNamespace(messages=messages))
    assert integration._timer.interval == 290
    virtual_time.now = 1250
    integration._start_worker()
    assert analyzer.analyze.call_count == 1
    virtual_time.now = 1420
    integration._timer.fire()
    assert analyzer.analyze.call_count == 2
    assert "用户偏好0" not in analyzer.analyze.call_args.args[1]
    integration.close()


def test_long_conversation_and_tool_filter_share_same_body(tmp_path, virtual_time):
    """长工具结果、参数、系统及执行报告既不推动阈值，也不送去提取。"""
    analyzer = _analyzer()
    node = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer,
        config=ExtractionConfig(minimum_message_tokens_to_init=10, minimum_tokens_between_update=10))
    excluded = "不能提取" * 10000
    messages = [
        {"role": "system", "content": excluded},
        {"role": "assistant", "content": excluded, "tool_calls": [{"function": {"arguments": excluded}}]},
        {"role": "tool", "content": excluded},
        {"role": "assistant", "content": [{"type": "tool_use", "input": excluded}]},
        {"role": "assistant", "kind": "execution_report", "content": excluded},
        {"role": "assistant", "content": "[SUMMARY]" + excluded},
        {"role": "user", "uuid": "u", "content": "有效事实"},
        {"role": "assistant", "uuid": "a", "content": "已经确认"},
    ]
    assert not node.check_and_extract(messages, 999999)
    assert node.get_stats()["pending_tokens"] == 2
    messages[-1]["content"] = "确" * 30
    assert node.check_and_extract(messages, 0)
    assert node.flush_pending() == 1
    text = analyzer.analyze.call_args.args[1]
    assert "有效事实" in text
    assert "不能提取" not in text
    assert "[tool]" not in text and "[system]" not in text and "[SUMMARY]" not in text


@pytest.mark.parametrize("needs_update", [False, True])
def test_completed_progress_survives_restart(tmp_path, virtual_time, needs_update):
    """成功保存或无需更新都留下进度，恢复会话不会再次分析原消息。"""
    workspace = Workspace(_root=tmp_path)
    session = Session(workspace.sessions_dir, resume=False)
    for message in _turns(3):
        session.add(message)
    analyzer = _analyzer(needs_update)
    node = MemoryExtractionNode(WikiMemory(workspace.memory_dir), llm_analyzer=analyzer, session=session)
    node.collect(session.messages)
    node.flush_pending()

    resumed_analyzer = _analyzer()
    resumed = MemoryExtractionNode(WikiMemory(workspace.memory_dir), llm_analyzer=resumed_analyzer,
        session=Session(workspace.sessions_dir, session.session_id))
    assert not resumed.check_and_extract(session.messages, 100000)
    assert resumed.get_pending_count() == 0
    assert resumed.flush_pending() == 0
    resumed_analyzer.analyze.assert_not_called()
    assert resumed.get_stats()["total_extractions"] == 1


def test_failed_batch_survives_restart_and_waits_for_cooldown(tmp_path, virtual_time):
    """失败不丢正文，重启后不能立即重复调用模型。"""
    analyzer = _analyzer()
    analyzer.analyze.side_effect = RuntimeError("模型暂不可用")
    node = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    node.collect(_turns(3))
    assert node.flush_pending() == 0
    recovered = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=_analyzer())
    assert recovered.get_pending_count() == 1
    assert recovered.next_delay() == 300
    virtual_time.now = 1300
    assert recovered.should_extract()
    assert recovered.flush_pending() == 1
    assert recovered.get_pending_count() == 0


def test_manual_memory_ignores_automatic_threshold_and_cooldown(tmp_path, virtual_time):
    """主动写入立即处理，不等待三轮或闲置，也不消费自动批次。"""
    analyzer = _analyzer()
    node = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    node.collect(_turns(1))
    node.indexer.update(lambda state: setattr(state, "last_attempt_time", virtual_time.now))
    assert node.next_delay() is None
    assert node.write_now("请记住我喜欢中文回复") == 1
    assert node.get_pending_count() == 1
    assert node.get_stats()["total_extractions"] == 0


def test_compression_preserves_original_pending_body(tmp_path, virtual_time):
    """压缩前持久化原文，恢复后分析一次原文，不重复分析摘要。"""
    workspace = Workspace(_root=tmp_path)
    session = Session(workspace.sessions_dir, resume=False)
    for message in _turns(3):
        session.add(message)
    session.add({"role": "tool", "content": "工具机密结果不进入记忆"})
    node = MemoryExtractionNode(WikiMemory(workspace.memory_dir), llm_analyzer=_analyzer(), session=session)
    original = list(session.messages)
    with patch("qrclaw.agent.get_extractor", return_value=node), patch("qrclaw.agent.is_sub_agent", return_value=False), \
         patch("qrclaw.memory.compression.compressor._pick_recent", return_value=(original[-3:], original[:-3])), \
         patch("qrclaw.llm_service.get_llm_service", return_value=SimpleNamespace(chat=lambda *_: SimpleNamespace(content="压缩后的概括"))):
        assert summarize(session)
    assert len(node.indexer.get_state().pending_messages) == 6
    assert node.indexer.get_state().preserved_message_ids

    analyzer = _analyzer()
    restored = MemoryExtractionNode(WikiMemory(workspace.memory_dir), llm_analyzer=analyzer,
        session=Session(workspace.sessions_dir, session.session_id))
    restored.collect(restored.session.messages)
    assert restored.flush_pending() == 1
    text = analyzer.analyze.call_args.args[1]
    assert all(f"用户偏好{i}" in text for i in range(3))
    assert "[SUMMARY]" not in text and "工具机密" not in text
    restored.collect(restored.session.messages)
    assert restored.get_pending_count() == 0


def test_two_nodes_cannot_extract_same_batch_twice(tmp_path):
    """文件工作锁让同一会话的两个节点共享进度，只有一个实际调用模型。"""
    analyzer = _analyzer()
    first = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    first.collect(_turns(3))
    second = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda node: node.flush_pending(), [first, second]))
    assert sorted(results) == [0, 1]
    assert analyzer.analyze.call_count == 1


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_changed_session_rejects_inflight_memory(tmp_path, virtual_time, change):
    """编辑、删除会话发生在模型合并期间时，不允许旧结果写进 Wiki。"""
    workspace = Workspace(_root=tmp_path)
    service = SessionService(workspace)
    session = service.create_session()
    for message in _turns(1):
        session.add(message)
    wiki = WikiMemory(workspace.memory_dir)
    wiki.save_page("偏好", "旧正文")
    analyzer = _analyzer()
    node = MemoryExtractionNode(wiki, llm_analyzer=analyzer, session=session)
    node.collect(session.messages)

    def consolidate(**_kwargs):
        """模拟模型调用期间用户撤销或更正来源对话。"""
        if change == "delete":
            service.delete_session(session.session_id)
        else:
            service.rewrite_latest_user_turn(session, "u0", "更正后的偏好")
        return ConsolidatePageSchema(content="不应该写入的旧结果")

    analyzer.consolidate.side_effect = consolidate
    assert node.flush_pending() == 0
    assert wiki.get_page("偏好").content == "旧正文"
    if change == "delete":
        assert not session._path.exists()
        assert not node.indexer.state_file.exists()
    else:
        assert [m["content"] for m in node.indexer.get_state().pending_messages] == ["更正后的偏好"]


def test_server_startup_resumes_idle_pending_batch(tmp_path, virtual_time):
    """真实 FastAPI 生命周期恢复尚未到期的任务，退出时取消计时。"""
    workspace = Workspace(_root=tmp_path)
    session = Session(workspace.sessions_dir, resume=False)
    for message in _turns(3):
        session.add(message)
    node = MemoryExtractionNode(WikiMemory(workspace.memory_dir), session=session)
    node.collect(session.messages)
    virtual_time.now = 1100
    result = _analyzer().analyze.return_value
    result.pages[0].source_ids = [s["id"] for s in node.memory.provenance.capture(session.messages, session.session_id) if s["role"] == "user"]
    with patch("qrclaw.memory.wiki.extraction.runner.ExtractionRunner.analyze_with_llm", return_value=result) as analyze:
        with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
            assert client.get("/health").status_code == 200
            timer = ManualTimer.timers[-1]
            assert timer.interval == 20
            virtual_time.now = 1120
            timer.fire()
            assert analyze.call_count == 1
            assert WikiMemory(workspace.memory_dir).get_page("偏好") is not None
    assert node.get_pending_count() == 0


def test_invalid_progress_is_not_silently_overwritten(tmp_path):
    """损坏进度必须保留，不能回退为空状态后重新提取全部历史。"""
    node = MemoryExtractionNode(WikiMemory(tmp_path))
    node.indexer.state_file.write_text("{损坏", encoding="utf-8")
    with pytest.raises(ValueError, match="进度损坏"):
        node.collect(_turns(3))
    assert node.indexer.state_file.read_text(encoding="utf-8") == "{损坏"


def test_progress_atomic_write_failure_keeps_previous_snapshot(tmp_path):
    """替换文件失败时原有进度及待处理内容不被破坏。"""
    node = MemoryExtractionNode(WikiMemory(tmp_path))
    node.collect(_turns(1))
    original = node.indexer.state_file.read_bytes()
    with patch("pathlib.Path.replace", side_effect=OSError("磁盘暂不可写")):
        with pytest.raises(OSError):
            node.collect(_turns(2))
    assert node.indexer.state_file.read_bytes() == original
    assert not list(node.indexer.state_file.parent.glob("*.tmp"))


@pytest.mark.parametrize("values", [{"minimum_turns": 0}, {"idle_seconds": -1}, {"cooldown_seconds": -1},
                                    {"idle_seconds": float("inf")}, {"cooldown_seconds": float("nan")}])
def test_invalid_batch_config_is_rejected(values):
    """无效时间和轮次配置不能形成连续调用模型的循环。"""
    with pytest.raises(ValueError):
        ExtractionConfig(**values)


def test_old_timer_callback_cannot_replace_new_timer(tmp_path, virtual_time):
    """已取消的计时器即使回调在等待锁，也不能覆盖新计时器。"""
    node = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=_analyzer())
    integration = MemoryExtractionIntegration(node)
    integration.on_react_loop_end(SimpleNamespace(messages=_turns(3)))
    old = integration._timer
    virtual_time.now = 1050
    integration.on_run_start()
    integration.on_react_loop_end(SimpleNamespace(messages=_turns(4)))
    current = integration._timer
    integration._start_worker(old)
    assert integration._timer is current
    node._llm_analyzer.analyze.assert_not_called()
    integration.close()


def test_cleared_session_stops_idle_worker(tmp_path, virtual_time):
    """CLI 清空会话后取消待提取内容，不能循环启动空任务。"""
    workspace = Workspace(_root=tmp_path)
    session = Session(workspace.sessions_dir, resume=False)
    for message in _turns(3):
        session.add(message)
    analyzer = _analyzer()
    node = MemoryExtractionNode(WikiMemory(workspace.memory_dir), llm_analyzer=analyzer, session=session)
    integration = MemoryExtractionIntegration(node)
    integration.on_react_loop_end(session)
    session.clear()
    virtual_time.now = 1120
    integration._timer.fire()
    assert integration._closed
    assert not node.indexer.state_file.exists()
    analyzer.analyze.assert_not_called()


def test_background_rechecks_cooldown_after_work_lock(tmp_path, virtual_time):
    """另一节点完成批次后，新批次仍受共享的自动冷却约束。"""
    analyzer = _analyzer()
    first = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    first.collect(_turns(3))
    second = MemoryExtractionNode(WikiMemory(tmp_path), llm_analyzer=analyzer)
    virtual_time.now = 1120
    assert first.flush_pending(automatic=True) == 1
    second.collect(_turns(6))
    assert second.flush_pending(automatic=True) == 0
    assert analyzer.analyze.call_count == 1
    assert second.get_pending_count() == 1


@pytest.mark.parametrize("mcp_failure", [False, True])
def test_main_request_controls_memory_lifecycle(tmp_path, virtual_time, mcp_failure):
    """整次请求包含路由与 Plan 阶段，异常退出也解除运行状态。"""
    from rich.console import Console
    from qrclaw import agent
    from io import StringIO

    workspace = Workspace(_root=tmp_path)
    session = Session(workspace.sessions_dir, resume=False)
    for message in _turns(3):
        session.add(message)
    integration = agent._runner.react_loop._get_memory_integration(workspace, session)
    integration.on_react_loop_end(session)
    old_timer = integration._timer
    analyzer = _analyzer()
    integration.extractor._llm_analyzer = analyzer

    def execute(**_kwargs):
        """模拟耗时的路由或 Plan 阶段，期间不允许闲置提取。"""
        virtual_time.now += 200
        integration._start_worker()
        assert integration._busy
        analyzer.analyze.assert_not_called()
        session.add({"role": "assistant", "content": "任务已完成"})
        return "任务已完成"

    with patch.object(agent._runner, "run", side_effect=execute), \
         patch.object(agent, "_init_mcp", side_effect=RuntimeError("MCP 初始化失败") if mcp_failure else None, return_value=False):
        if mcp_failure:
            with pytest.raises(RuntimeError, match="MCP 初始化失败"):
                agent.run("执行任务", session, Console(file=StringIO()), workspace, plan_enabled=True)
        else:
            assert agent.run("执行任务", session, Console(file=StringIO()), workspace, plan_enabled=True) == "任务已完成"
    assert old_timer.cancelled
    assert not integration._busy
    assert integration._timer is not None
    integration.close()


def test_queued_http_request_pauses_idle_extraction(tmp_path, virtual_time, monkeypatch):
    """请求等待全局执行锁时即暂停闲置提取，完成后恢复计时。"""
    import threading
    from qrclaw import agent
    from qrclaw.server import routes

    monkeypatch.setattr("qrclaw.graph.nodes.memory_extraction.Thread", threading.Thread)
    workspace = Workspace(_root=tmp_path)
    session = Session(workspace.sessions_dir, resume=False)
    for message in _turns(3):
        session.add(message)
    integration = agent._runner.react_loop._get_memory_integration(workspace, session)
    analyzer = _analyzer()
    integration.extractor._llm_analyzer = analyzer
    integration.on_react_loop_end(session)
    old_timer = integration._timer
    accepted = threading.Event()
    original_start = integration.on_run_start

    def start():
        """记录 HTTP 请求已经入队，而 Agent 尚未开始执行。"""
        original_start()
        accepted.set()

    def execute(**kwargs):
        """用确定的运行器验证 API 的入队和收尾行为。"""
        assert integration._busy
        kwargs["session"].add({"role": "user", "content": kwargs["user_input"]})
        kwargs["session"].add({"role": "assistant", "content": "已处理更正"})
        return "已处理更正"

    monkeypatch.setattr(integration, "on_run_start", start)
    monkeypatch.setattr(routes, "run_agent", execute)
    client = TestClient(create_app(workspace), client=("127.0.0.1", 50000))
    routes._agent_run_lock.acquire()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(client.post, "/v1/chat/completions", json={
                "session_id": session.session_id, "messages": [{"role": "user", "content": "更正偏好"}],
            })
            try:
                assert accepted.wait(5)
                assert integration._busy and old_timer.cancelled
                virtual_time.now = 1200
                integration._start_worker(old_timer)
                analyzer.analyze.assert_not_called()
            finally:
                routes._agent_run_lock.release()
            assert future.result(timeout=5).status_code == 200
        assert not integration._busy
        assert integration._timer is not None
    finally:
        integration.close()
        client.close()
