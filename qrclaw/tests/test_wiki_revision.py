"""来源、用户确认、撤销、重建、并发及 Web 接口的完整记忆链路测试。"""
from types import SimpleNamespace
from unittest.mock import MagicMock
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import re
import threading
import time

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from qrclaw.memory.wiki import WikiMemory
from qrclaw.memory.wiki.page import WikiPage, wiki_links
from qrclaw.memory.wiki.extraction import ExtractionRunner
from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema, WikiPageSchema, ConsolidatePageSchema
from qrclaw.memory.wiki.extraction.config import ExtractionConfig
from qrclaw.server.app import create_app
from qrclaw.server.session_service import SessionService
from qrclaw.server.wiki_service import WikiService
from qrclaw.workspace import Workspace
from qrclaw.tools.wiki_tools import read_wiki_content, reset_wiki_reads


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    """禁止真实计时器和外部模型，只在测试中明确推动工作队列。"""
    memory = WikiMemory.for_workspace(tmp_path / "memory")
    worker = memory.repair_worker
    monkeypatch.setattr(worker, "resume", lambda: None)
    worker.config = ExtractionConfig(idle_seconds=0, cooldown_seconds=0)
    worker.runner.llm_analyzer = MagicMock()
    worker.runner.llm_analyzer.consolidate.return_value = ConsolidatePageSchema(content="有效事实", tags=[], related=[])
    reset_wiki_reads()
    yield memory
    worker.close()


def tracked(wiki, name="偏好", body="喜欢中文", session="会话", message="u1"):
    """直接建立真实来源页面，避免只测试模拟的来源元数据。"""
    sources = wiki.provenance.capture([{"uuid": message, "role": "user", "content": body}], session)
    return wiki.save_page(name, body, description="用户偏好", tags=["偏好"], source_ids=[s["id"] for s in sources]), sources


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("action", ["edit", "delete"])
def test_old_agent_request_cannot_restore_later_web_removal(wiki, tmp_path, monkeypatch, queued, action):
    """实际 Agent 和 API 排队请求都按接收顺序处理后来的用户撤销。"""
    import qrclaw.agent as agent
    from qrclaw.server import routes
    workspace = Workspace(_root=tmp_path)
    session = SessionService(workspace).create_session()
    page, _ = tracked(wiki, body="旧邮箱 old@example.test")
    integration = agent._runner.react_loop._get_memory_integration(workspace, session)
    analyzer = MagicMock()
    def analyze(_index, text):
        """只使用模型实际收到的真实来源 ID。"""
        ids = re.findall(r"\[来源 ([a-f0-9]+)；user；", text)
        return ExtractionSchema(needs_update=True, pages=[WikiPageSchema(
            action="append", name=page.name, content="旧邮箱 old@example.test", source_ids=ids)])
    analyzer.analyze.side_effect = analyze
    analyzer.consolidate.return_value = ConsolidatePageSchema(content="旧邮箱 old@example.test")
    integration.extractor._llm_analyzer = analyzer
    def remove_fact():
        """模拟 Web 中较晚确认的整页替换或删除。"""
        if action == "edit":
            wiki.provenance.manual_edit(page.name, page.revision, "只保留电话", "", [], [])
        else:
            wiki.delete_page(page.name)
    def execute(**_kwargs):
        if not queued:
            remove_fact()
        assert integration.extractor.write_now("记住旧邮箱") == 0
        integration.extractor.session.add({"role": "assistant", "content": "旧邮箱的中间说明"})
        return "完成"
    monkeypatch.setattr(agent, "_init_mcp", lambda: False)
    monkeypatch.setattr(agent._runner, "run", execute)
    try:
        if queued:
            accepted = threading.Event()
            original_start = integration.on_run_start
            def start():
                original_start()
                accepted.set()
            monkeypatch.setattr(integration, "on_run_start", start)
            client = TestClient(create_app(workspace), client=("127.0.0.1", 50000))
            with ThreadPoolExecutor(max_workers=1) as pool:
                routes._agent_run_lock.acquire()
                try:
                    future = pool.submit(client.post, "/v1/chat/completions", json={
                        "session_id": session.session_id, "messages": [{"role": "user", "content": "记住旧邮箱"}]})
                    assert accepted.wait(5)
                    remove_fact()
                finally:
                    routes._agent_run_lock.release()
                assert future.result(timeout=5).status_code == 200
            client.close()
        else:
            assert agent.run("记住旧邮箱", session, Console(file=StringIO()), workspace) == "完成"
        saved = wiki.get_page(page.name)
        if action == "delete":
            assert saved is None
        else:
            assert saved.content == "只保留电话"
        analyzer.consolidate.assert_not_called()
        state = wiki.provenance.read()
        orders = [s["order"] for s in state["sources"].values() if s.get("session_id") == session.session_id]
        assert len(set(orders)) == 1
    finally:
        integration.close()


def test_delete_recreate_preserves_revision_and_rejects_old_consolidation(wiki):
    """相同正文和元数据的重建也必须阻止旧快照通过 CAS。"""
    old, sources = tracked(wiki)
    analyzer = MagicMock()
    def recreate(**_kwargs):
        wiki.delete_page(old.name)
        fresh = wiki.provenance.capture([{"uuid": "fresh", "role": "user", "content": old.content}], "新会话")
        new = wiki.save_page(old.name, old.content, old.description, old.tags, old.related,
            explicit=True, started_at=time.time(), source_ids=[fresh[0]["id"]])
        assert new.revision > old.revision
        return ConsolidatePageSchema(content="旧后台任务覆盖")
    analyzer.consolidate.side_effect = recreate
    proposal = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(action="append", name=old.name,
        content="新增", source_ids=[sources[0]["id"]])])
    with pytest.raises(RuntimeError):
        ExtractionRunner(wiki, llm_analyzer=analyzer).process_extraction_result(proposal, sources=sources)
    saved = wiki.get_page(old.name)
    assert saved.content == old.content and saved.source_ids != old.source_ids
    previous_revision = saved.revision
    wiki.delete_page(old.name)
    rebuilt = WikiMemory(wiki.memory_dir).save_page(old.name, "再次建立", explicit=True, started_at=time.time())
    assert rebuilt.revision > previous_revision


@pytest.mark.parametrize("action", ["delete", "edit", "delete_message"])
def test_failed_source_withdrawal_keeps_original_chat(wiki, tmp_path, monkeypatch, action):
    """撤销首次落盘失败时，聊天文件和有效来源都保持原样。"""
    service = SessionService(Workspace(_root=tmp_path))
    session = service.create_session()
    session.add({"uuid": "u1", "role": "user", "content": "旧邮箱"})
    page, sources = tracked(wiki, body="旧邮箱", session=session.session_id)
    before = session._path.read_bytes()
    monkeypatch.setattr(wiki.provenance, "write", MagicMock(side_effect=OSError("来源磁盘写入失败")))
    with pytest.raises(OSError):
        if action == "delete":
            service.delete_session(session.session_id)
        else:
            service.rewrite_latest_user_turn(session, "u1", "新邮箱" if action == "edit" else None)
    assert session._path.read_bytes() == before
    assert session.messages[0]["content"] == "旧邮箱"
    assert wiki.provenance.read()["sources"][sources[0]["id"]]["state"] == "valid"
    assert wiki.retrievable(wiki.get_page(page.name))


@pytest.mark.parametrize("action", ["delete", "edit", "delete_message"])
@pytest.mark.parametrize("stage", ["withdrawal", "file"])
def test_persisted_chat_change_recovers_after_interruption(wiki, tmp_path, monkeypatch, action, stage):
    """意图落盘后中断，旧正文不再可用，启动恢复完成文件及来源进度。"""
    import qrclaw.agent as agent
    service = SessionService(Workspace(_root=tmp_path))
    session = service.create_session()
    session.add({"uuid": "u1", "role": "user", "content": "旧邮箱"})
    page, sources = tracked(wiki, body="旧邮箱", session=session.session_id)
    save = wiki.save_page
    write = wiki.provenance.write
    if stage == "withdrawal":
        monkeypatch.setattr(wiki, "save_page", MagicMock(side_effect=OSError("排队后进程中断")))
    else:
        def fail_acknowledgement(state):
            """模拟聊天文件已修改、完成回执尚未持久化时中断。"""
            if not state["session_changes"]:
                raise OSError("聊天已修改后进程中断")
            write(state)
        monkeypatch.setattr(wiki.provenance, "write", fail_acknowledgement)
    with pytest.raises(OSError):
        if action == "delete":
            service.delete_session(session.session_id)
        else:
            service.rewrite_latest_user_turn(session, "u1", "新邮箱" if action == "edit" else None)
    state = wiki.provenance.read()
    assert session.session_id in state["session_changes"]
    assert "body" not in state["sources"][sources[0]["id"]]
    assert not wiki.retrievable(wiki.get_page(page.name))
    assert wiki.provenance.capture(session.messages, session.session_id, allow_renew=True) == []
    monkeypatch.setattr(wiki, "save_page", save)
    monkeypatch.setattr(wiki.provenance, "write", write)
    agent._runner.react_loop.stop_memory_extractions(service.workspace)
    with TestClient(create_app(service.workspace), client=("127.0.0.1", 50000)):
        assert wiki.provenance.read()["session_changes"] == {}
        if action == "delete":
            assert not session._path.exists()
        else:
            restored = service.get_session(session.session_id)
            if action == "delete_message":
                assert restored.messages == []
            else:
                assert restored.messages[0]["content"] == "新邮箱"
        valid = [s for s in wiki.provenance.read()["sources"].values() if s.get("state") == "valid"]
        assert all(s["body"] != "旧邮箱" for s in valid)
        assert any(s["body"] == "新邮箱" for s in valid) == (action == "edit")


def test_consolidation_prompt_includes_page_and_proposal_metadata(wiki):
    """验证真正提交给模型的提示词包含旧页和本次提议的全部元数据。"""
    from qrclaw.memory.wiki.extraction.llm import WikiLLMAnalyzer
    page = wiki.save_page("配置", "正文", "独有摘要", ["独有标签甲"], ["关联项目乙"])
    llm = MagicMock()
    llm.structured.return_value = ConsolidatePageSchema(content="正文及新增", tags=[], related=[])
    analyzer = WikiLLMAnalyzer(llm, ExtractionConfig())
    proposal = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(action="append", name=page.name,
        content="新增", description="新增摘要", tags=["新增标签丙"], related=["新增关联丁"])])
    assert ExtractionRunner(wiki, llm_analyzer=analyzer).process_extraction_result(proposal) == 1
    prompt = llm.structured.call_args.kwargs["messages"][0]["content"]
    assert all(text in prompt for text in ["独有摘要", "独有标签甲", "关联项目乙", "新增摘要", "新增标签丙", "新增关联丁"])
    assert wiki.get_page(page.name).tags == [] and wiki.get_page(page.name).related == []


@pytest.mark.parametrize("manual", [False, True])
def test_repair_passes_valid_metadata_without_reviving_withdrawn_metadata(wiki, manual):
    """重建只使用有效用户确认或独立旧基线的元数据。"""
    page = wiki.save_page("配置", "有效正文", "确认摘要", ["独有标签甲"], ["关联项目乙"])
    if manual:
        page = wiki.provenance.manual_edit(page.name, page.revision, page.content, page.description, page.tags, page.related)
    else:
        wiki.provenance.baseline(page)
    wiki.provenance.queue(page.name)
    analyzer = wiki.repair_worker.runner.llm_analyzer
    analyzer.consolidate.return_value = ConsolidatePageSchema(content=page.content)
    assert wiki.repair_worker.process_due(force=True) == 1
    evidence = analyzer.consolidate.call_args.kwargs["new_content"]
    assert all(text in evidence for text in ["确认摘要", "独有标签甲", "关联项目乙"])
    rebuilt = wiki.get_page(page.name)
    assert rebuilt.tags == page.tags and rebuilt.related == page.related


def test_repair_omitted_metadata_does_not_keep_withdrawn_tags(wiki):
    """模型漏字段时，也不能沿用失效页面中无独立证据的标签与关联。"""
    page, _ = tracked(wiki, body="旧事实")
    valid = wiki.provenance.capture([{"uuid": "other", "role": "user", "content": "有效事实"}], "其他会话")
    wiki.save_page(page.name, "混合正文", tags=["撤销标签"], related=["撤销关联"],
        source_ids=page.source_ids + [valid[0]["id"]])
    wiki.provenance.invalidate("会话")
    wiki.repair_worker.runner.llm_analyzer.consolidate.return_value = ConsolidatePageSchema(content="有效事实")
    assert wiki.repair_worker.process_due(force=True) == 1
    saved = wiki.get_page(page.name)
    assert saved.tags == [] and saved.related == []


def test_yaml_and_legacy_roundtrip(wiki):
    page = wiki.save_page("配置: 中文", "正文 [[关联#章节|别名]]", description="说明: 多行\n继续",
                          tags=["a,b", "[标签]"], related=["关联"])
    restored = wiki.get_page(page.name)
    assert restored.content == page.content
    assert restored.description == page.description
    assert restored.tags == ["a,b", "[标签]"]
    assert restored.revision == 1
    assert restored.status == "legacy"
    legacy = WikiPage.from_markdown("---\nname: 旧页\ndescription: 中文: 说明\ntags: [旧, 标签]\n---\n正文")
    assert legacy.description == "中文: 说明"
    assert legacy.source_ids == []
    assert wiki_links("[[关联#章节|别名]] [[关联]] [[另一页|显示]]") == ["关联", "另一页"]
    with pytest.raises(ValueError):
        WikiPage.from_markdown("---\nrevision: nope\n---\n正文")


def test_capture_excludes_tools_and_summaries(wiki):
    sources = wiki.provenance.capture([
        {"uuid": "u", "role": "user", "content": "有效"},
        {"role": "tool", "content": "机密工具结果"},
        {"role": "assistant", "content": "参数", "tool_calls": [{"id": "x"}]},
        {"role": "assistant", "content": "[SUMMARY]摘要"},
        {"role": "assistant", "content": "报告", "kind": "execution_report"}], "s")
    assert [s["body"] for s in sources] == ["有效"]
    assert "机密" not in wiki.provenance.path.read_text()


@pytest.mark.parametrize("bad_ids", [["编造来源"], []])
def test_model_must_use_actual_source_ids(wiki, bad_ids):
    _, sources = tracked(wiki)
    result = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(action="create", name="新页", content="事实", source_ids=bad_ids)])
    with pytest.raises(RuntimeError):
        ExtractionRunner(wiki).process_extraction_result(result, sources=sources, batch_id="批次")
    assert wiki.get_page("新页") is None


def test_assistant_alone_cannot_establish_user_fact(wiki):
    sources = wiki.provenance.capture([{"uuid": "a", "role": "assistant", "content": "猜测用户喜欢英文"}], "s")
    result = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(action="create", name="猜测", content="喜欢英文", source_ids=[sources[0]["id"]])])
    with pytest.raises(RuntimeError):
        ExtractionRunner(wiki).process_extraction_result(result, sources=sources)
    assert wiki.get_page("猜测") is None


def test_withdrawal_purges_body_and_hides_old_page(wiki):
    page, sources = tracked(wiki)
    wiki.provenance.invalidate("会话", {"u1"})
    assert wiki.get_page(page.name).status == "pending"
    assert wiki.list_pages() == []
    assert "喜欢中文" not in wiki.load_index()
    assert "content" not in read_wiki_content(wiki, page.name)
    detail = WikiService(wiki.memory_dir).get_page(page.name)
    assert detail["sources"][0]["state"] == "withdrawn"
    assert "body" not in detail["sources"][0]
    assert sources[0]["id"] in wiki.provenance.path.read_text()
    assert "喜欢中文" not in wiki.provenance.path.read_text()
    assert wiki.repair_worker.process_due(force=True) == 1
    assert wiki.get_page(page.name).status == "inactive"
    wiki.repair_worker.runner.llm_analyzer.consolidate.assert_not_called()


def test_multisource_rebuild_uses_only_valid_evidence(wiki):
    page, sources = tracked(wiki, body="过期端口8765")
    other = wiki.provenance.capture([{"uuid": "u2", "role": "user", "content": "喜欢中文"}], "另一会话")
    wiki.save_page(page.name, "过期端口8765，喜欢中文", source_ids=[sources[0]["id"], other[0]["id"]])
    wiki.provenance.invalidate("会话")
    analyzer = wiki.repair_worker.runner.llm_analyzer
    analyzer.consolidate.return_value = ConsolidatePageSchema(content="喜欢中文", tags=[], related=[])
    assert wiki.repair_worker.process_due(force=True) == 1
    args = analyzer.consolidate.call_args.kwargs
    assert args["existing_content"] == ""
    assert "8765" not in args["new_content"]
    assert "喜欢中文" in args["new_content"]
    saved = wiki.get_page(page.name)
    assert saved.source_ids == [other[0]["id"]]
    assert saved.tags == [] and saved.related == []
    assert wiki.retrievable(saved)


def test_manual_confirmation_survives_chat_deletion(wiki):
    page, sources = tracked(wiki)
    saved = wiki.provenance.manual_edit(page.name, page.revision, "只保留中文回复", "确认偏好", [], [])
    assert saved.revision > page.revision
    assert saved.source_ids != page.source_ids
    with pytest.raises(RuntimeError):
        wiki.provenance.manual_edit(page.name, page.revision, "旧草稿", "", [], [])
    wiki.provenance.invalidate("会话")
    assert wiki.retrievable(wiki.get_page(page.name))
    assert WikiService(wiki.memory_dir).get_page(page.name)["sources"][0]["kind"] == "manual"
    result = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(action="append", name=page.name, content="旧内容", source_ids=[sources[0]["id"]])])
    assert ExtractionRunner(wiki).process_extraction_result(result, sources=sources, batch_id="旧批次") == 0
    assert wiki.get_page(page.name).content == "只保留中文回复"


@pytest.mark.parametrize("action", ["delete", "edit", "invalidate"])
def test_changes_during_repair_reject_stale_result(wiki, action):
    page, _ = tracked(wiki)
    wiki.provenance.queue(page.name)
    def generate(**kwargs):
        latest = wiki.get_page(page.name)
        if action == "delete":
            wiki.delete_page(page.name)
        elif action == "edit":
            wiki.provenance.manual_edit(page.name, latest.revision, "用户确认新内容", "", [], [])
        else:
            wiki.provenance.invalidate("会话")
        return ConsolidatePageSchema(content="旧模型结果")
    wiki.repair_worker.runner.llm_analyzer.consolidate.side_effect = generate
    assert wiki.repair_worker.process_due(force=True) == 0
    saved = wiki.get_page(page.name)
    if action == "delete":
        assert saved is None
        with pytest.raises(RuntimeError):
            wiki.save_page(page.name, "旧任务恢复")
    elif action == "edit":
        assert saved.content == "用户确认新内容"
    else:
        assert not wiki.retrievable(saved)
        assert wiki.provenance.read()["repairs"][page.name]["status"] == "pending"


def test_legacy_baseline_is_separate_from_merged_body(wiki):
    original = wiki.save_page("旧配置", "长期有效的旧约束")
    wiki.provenance.baseline(original)
    sources = wiki.provenance.capture([{"uuid": "u", "role": "user", "content": "临时事实"}], "s")
    wiki.save_page(original.name, "长期有效的旧约束和临时事实", source_ids=[sources[0]["id"]])
    wiki.provenance.invalidate("s")
    wiki.repair_worker.process_due(force=True)
    evidence = wiki.repair_worker.runner.llm_analyzer.consolidate.call_args.kwargs["new_content"]
    assert "长期有效的旧约束" in evidence and "临时事实" not in evidence


def test_failed_repair_and_written_receipt_survive_restart(wiki, monkeypatch):
    page, _ = tracked(wiki)
    wiki.provenance.queue(page.name)
    analyzer = wiki.repair_worker.runner.llm_analyzer
    analyzer.consolidate.side_effect = RuntimeError("模型故障")
    assert wiki.repair_worker.process_due(force=True) == 0
    assert wiki.provenance.read()["repairs"][page.name]["status"] == "failed"
    reopened = WikiMemory(wiki.memory_dir)
    assert reopened.provenance.read()["repairs"][page.name]["error"] == "模型故障"
    analyzer.consolidate.side_effect = None
    original_write = wiki.provenance.write
    crash = [True]
    def write(state):
        if state["repairs"][page.name]["status"] == "completed" and crash[0]:
            crash[0] = False
            raise OSError("写页后确认前崩溃")
        original_write(state)
    monkeypatch.setattr(wiki.provenance, "write", write)
    assert wiki.repair_worker.process_due(force=True) == 0
    revision = wiki.get_page(page.name).revision
    calls = analyzer.consolidate.call_count
    assert wiki.repair_worker.process_due(force=True) == 1
    assert wiki.get_page(page.name).revision == revision
    assert analyzer.consolidate.call_count == calls


def test_batch_receipt_skips_completed_page_on_retry(wiki):
    _, sources = tracked(wiki)
    proposal = WikiPageSchema(action="create", name="批次页", content="喜欢中文", source_ids=[sources[0]["id"]])
    runner = ExtractionRunner(wiki)
    result = ExtractionSchema(needs_update=True, pages=[proposal])
    assert runner.process_extraction_result(result, sources=sources, batch_id="batch") == 1
    revision = wiki.get_page("批次页").revision
    proposal.content = "同一批次重试时的另一种表述"
    assert runner.process_extraction_result(result, sources=sources, batch_id="batch") == 0
    assert wiki.get_page("批次页").revision == revision


def test_alias_backlinks_lint_and_read_budget(wiki):
    wiki.save_page("A", "字" * 30000 + "[[B#段落|别名]]")
    wiki.save_page("B", "关联正文 [[A]]")
    assert wiki.backlinks("B") == ["A"]
    assert not [i for i in WikiService(wiki.memory_dir).lint()["issues"] if i["issue_type"] == "broken_link"]
    first = read_wiki_content(wiki, "A")
    assert len(first["content"]) == 6000 and first["next_offset"] == 6000
    assert read_wiki_content(wiki, "A").get("duplicate")
    for offset in (6000, 12000, 18000):
        read_wiki_content(wiki, "A", offset)
    assert "预算" in read_wiki_content(wiki, "B")["error"]


def test_plan_selector_candidate_cap(wiki, monkeypatch):
    for i in range(25):
        wiki.save_page(f"页面{i}", "内容")
    seen = []
    def select(_self, _query, _messages, **kwargs):
        seen.extend(kwargs["candidates"])
        return SimpleNamespace(selected=[])
    monkeypatch.setattr("qrclaw.memory.wiki.selection.WikiPageSelector.select", select)
    assert wiki.select_relevant_pages("无匹配关键词", []) == []
    assert len(seen) == 20


def test_web_api_search_edit_delete_and_conflict(wiki, tmp_path):
    page, _ = tracked(wiki)
    workspace = Workspace(_root=tmp_path)
    with TestClient(create_app(workspace), client=("127.0.0.1", 50000)) as client:
        assert client.get("/v1/wiki/pages?q=中文&limit=1").json()["total"] == 1
        detail = client.get("/v1/wiki/pages/偏好").json()
        assert detail["sources"][0]["body"] == "喜欢中文"
        payload = dict(revision=page.revision, content="用户手动确认", description="手动摘要", tags=[], related=[])
        response = client.patch("/v1/wiki/pages/偏好", json=payload)
        assert response.status_code == 200
        assert response.json()["sources"][0]["kind"] == "manual"
        assert client.patch("/v1/wiki/pages/偏好", json=payload).status_code == 409
        assert client.post("/v1/wiki/lint").status_code == 200
        assert client.get("/v1/wiki/pages?offset=-1").status_code == 422
        assert client.delete(f"/v1/wiki/pages/偏好?revision={page.revision}").status_code == 409
        assert client.delete(f"/v1/wiki/pages/偏好?revision={response.json()['revision']}").status_code == 200
        assert client.get("/v1/wiki/pages/偏好").status_code == 404
        assert client.post("/v1/wiki/pages/偏好/repair").status_code == 404
    with pytest.raises(RuntimeError):
        wiki.save_page("偏好", "旧批次")


def test_session_delete_retracts_already_ingested_sources(wiki, tmp_path):
    service = SessionService(Workspace(_root=tmp_path))
    session = service.create_session()
    session.add({"uuid": "u1", "role": "user", "content": "用户长期偏好"})
    page, _ = tracked(wiki, body="用户长期偏好", session=session.session_id)
    assert service.delete_session(session.session_id)
    assert not session._path.exists()
    assert not wiki.retrievable(wiki.get_page(page.name))
    assert "用户长期偏好" not in wiki.provenance.path.read_text()


def test_message_edit_rebuilds_from_replacement_without_three_turn_gate(wiki, tmp_path):
    service = SessionService(Workspace(_root=tmp_path))
    session = service.create_session()
    session.add({"uuid": "u1", "role": "user", "content": "端口8765"})
    page, _ = tracked(wiki, body="端口8765", session=session.session_id)
    service.rewrite_latest_user_turn(session, "u1", "端口9000")
    assert not wiki.retrievable(wiki.get_page(page.name))
    analyzer = wiki.repair_worker.runner.llm_analyzer
    analyzer.consolidate.return_value = ConsolidatePageSchema(content="端口9000", tags=[], related=[])
    assert wiki.repair_worker.process_due(force=True) == 1
    evidence = analyzer.consolidate.call_args.kwargs["new_content"]
    assert "端口9000" in evidence and "8765" not in evidence
    detail = WikiService(wiki.memory_dir).get_page(page.name)
    assert detail["sources"][0]["body"] == "端口9000"
    from qrclaw.agent import _runner
    _runner.react_loop.stop_memory_extractions(service.workspace, session.session_id)


def test_mixed_batch_after_manual_edit_uses_only_new_evidence(wiki, monkeypatch):
    monkeypatch.setattr("qrclaw.memory.wiki.provenance.time.time", lambda: 1000)
    page, old = tracked(wiki, body="已撤销的旧事实")
    confirmed = wiki.provenance.manual_edit(page.name, page.revision, "用户确认中文", "", [], [])
    newer = wiki.provenance.capture([{"uuid": "u2", "role": "user", "content": "现在明确改为英文"}], "会话")
    analyzer = MagicMock()
    analyzer.consolidate.return_value = ConsolidatePageSchema(content="用户确认英文", tags=[], related=[])
    result = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(action="append", name=page.name,
        content="已撤销的旧事实及新内容", source_ids=[old[0]["id"], newer[0]["id"]])])
    assert ExtractionRunner(wiki, llm_analyzer=analyzer).process_extraction_result(result, sources=old + newer) == 1
    evidence = analyzer.consolidate.call_args.kwargs["new_content"]
    assert "现在明确改为英文" in evidence and "已撤销的旧事实" not in evidence
    saved = wiki.get_page(page.name)
    assert saved.source_ids == confirmed.source_ids + [newer[0]["id"]]
    manual = WikiService(wiki.memory_dir).get_page(page.name)["sources"][0]
    assert "-已撤销的旧事实" in manual["diff"] and "+用户确认中文" in manual["diff"]


def test_failed_manual_save_preserves_baseline_and_repair(wiki, monkeypatch):
    page = wiki.save_page("历史页", "历史约束")
    wiki.provenance.baseline(page)
    wiki.provenance.queue(page.name)
    page = wiki.get_page(page.name)
    before = wiki.provenance.read()
    monkeypatch.setattr(wiki, "save_page", MagicMock(side_effect=OSError("磁盘写入失败")))
    with pytest.raises(OSError):
        wiki.provenance.manual_edit(page.name, page.revision, "用户草稿", "", [], [])
    after = wiki.provenance.read()
    assert before["baselines"] == after["baselines"]
    assert before["repairs"] == after["repairs"]
    assert wiki.get_page(page.name).content == "历史约束"


def test_partial_batch_retries_only_unfinished_pages(wiki, monkeypatch):
    _, sources = tracked(wiki)
    pages = [WikiPageSchema(action="create", name=name, content="事实", source_ids=[sources[0]["id"]]) for name in ("成功页", "失败页")]
    result = ExtractionSchema(needs_update=True, pages=pages)
    runner = ExtractionRunner(wiki)
    save = wiki.save_page
    def fail_second(**kwargs):
        if kwargs["name"] == "失败页":
            raise OSError("失败页暂时不可写")
        return save(**kwargs)
    with pytest.raises(RuntimeError):
        runner.process_extraction_result(result, sources=sources, batch_id="同一批次", save_page=fail_second)
    revision = wiki.get_page("成功页").revision
    assert runner.process_extraction_result(result, sources=sources, batch_id="同一批次") == 1
    assert wiki.get_page("成功页").revision == revision
    assert wiki.get_page("失败页") is not None


def test_repair_idle_cooldown_busy_and_restart_schedule(wiki, monkeypatch):
    from qrclaw.memory.wiki.repair import WikiRepairWorker
    now = [1000.0]
    monkeypatch.setattr("qrclaw.memory.wiki.repair.time.time", lambda: now[0])
    page, _ = tracked(wiki)
    wiki.provenance.invalidate("会话")
    worker = WikiRepairWorker(wiki, ExtractionConfig(idle_seconds=120, cooldown_seconds=300))
    assert worker.next_delay() == 120
    now[0] += 119
    assert worker.process_due() == 0
    now[0] += 1
    worker.busy_sessions.add("运行中")
    assert worker.process_due() == 0
    worker.busy_sessions.clear()
    state = wiki.provenance.read()
    state["repairs"][page.name].update(status="processing", last_attempt=now[0])
    wiki.provenance.write(state)
    reopened = WikiRepairWorker(WikiMemory(wiki.memory_dir), worker.config)
    assert reopened.next_delay() == 300
    now[0] += 300
    assert reopened.process_due() == 1
    assert wiki.get_page(page.name).status == "inactive"
    worker.close()
    reopened.close()


def test_corrupt_metadata_and_symlink_are_reported_without_reading(wiki, tmp_path):
    path = wiki.pages_dir / "损坏.md"
    original = "---\nrevision: wrong\n---\n原正文"
    path.write_text(original)
    outside = tmp_path / "私有.md"
    outside.write_text("私人正文")
    (wiki.pages_dir / "越界.md").symlink_to(outside)
    issues = WikiService(wiki.memory_dir).lint()["issues"]
    assert {i["page_name"] for i in issues if i["issue_type"] == "metadata"} == {"损坏", "越界"}
    assert path.read_text() == original
    assert "私人正文" not in str(issues)
    wiki.rebuild_index()
    assert wiki.index.all_entries() == []
    assert path.read_text() == original


def test_deleted_page_accepts_only_new_explicit_request(wiki, monkeypatch):
    import time
    page, sources = tracked(wiki)
    wiki.delete_page(page.name)
    deleted_at = wiki.provenance.read()["deleted_pages"][page.name]
    from datetime import datetime
    runner = ExtractionRunner(wiki)
    result = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(action="create", name=page.name, content="重新明确记住", source_ids=[sources[0]["id"]])])
    runner.started_at = datetime.fromisoformat(deleted_at).timestamp() - 1
    assert runner.process_extraction_result(result, sources=sources, explicit=True) == 0
    runner.started_at = time.time() + 1
    assert runner.process_extraction_result(result, sources=sources, explicit=True) == 1
    assert wiki.retrievable(wiki.get_page(page.name))


def test_plan_read_context_remains_available_to_final_answer(wiki, tmp_path, monkeypatch):
    from qrclaw.memory.context.runtime.manager import ContextManager
    from qrclaw.memory.context.session import Session
    from qrclaw.graph.nodes.router import PlanStep
    workspace = Workspace(_root=tmp_path)
    session = Session(workspace.sessions_dir, resume=False)
    session.ensure_saved()
    ctx = ContextManager(session, workspace)
    ctx.set_plan("配置", [PlanStep("1", "读取配置")])
    ctx.set_wiki_context("已读取端口9000")
    monkeypatch.setattr("qrclaw.memory.context.runtime.manager.build_system_prompt", lambda **kwargs: kwargs["wiki_context"])
    assert ctx.build_messages("react")[0]["content"] == "已读取端口9000"
    ctx.clear_plan()
    assert ctx.build_messages("react")[0]["content"] == "已读取端口9000"
    assert ContextManager(session, workspace).build_messages("react")[0]["content"] == ""


def test_serial_subagent_does_not_reset_main_wiki_budget(wiki, tmp_path, monkeypatch):
    from io import StringIO
    from rich.console import Console
    import qrclaw.agent as agent
    from qrclaw.memory.context.session import Session
    wiki.save_page("长页", "字" * 30000)
    read_wiki_content(wiki, "长页")
    monkeypatch.setattr(agent, "is_sub_agent", lambda: True)
    monkeypatch.setattr(agent._runner, "run", lambda **kwargs: "子任务完成")
    session = Session(tmp_path / "sessions", resume=False)
    agent.run("串行子任务", session, Console(file=StringIO()), Workspace(_root=tmp_path))
    for offset in (6000, 12000, 18000):
        read_wiki_content(wiki, "长页", offset)
    assert "预算" in read_wiki_content(wiki, "长页", 24000)["error"]


def test_active_memory_uses_current_session_after_restart_or_second_request(wiki, tmp_path):
    from qrclaw.graph.nodes.react_loop import ReactLoopNode
    service = SessionService(Workspace(_root=tmp_path))
    original = service.create_session()
    original.add({"uuid": "old", "role": "user", "content": "旧问题"})
    loop = ReactLoopNode()
    loop._get_memory_integration(service.workspace, original)
    latest = service.get_session(original.session_id)
    latest.add({"uuid": "new", "role": "user", "content": "请记住新配置"})
    sources = wiki.provenance.capture(latest.messages, latest.session_id)
    new_id = next(s["id"] for s in sources if s["message_id"] == "new")
    integration = loop._get_memory_integration(service.workspace, latest)
    analyzer = MagicMock()
    analyzer.analyze.return_value = ExtractionSchema(needs_update=True, pages=[WikiPageSchema(
        action="create", name="新配置", content="新配置", source_ids=[new_id])])
    integration.extractor._llm_analyzer = analyzer
    assert integration.extractor.write_now("请记住新配置") == 1
    assert wiki.get_page("新配置").source_ids == [new_id]
    assert "请记住新配置" in analyzer.analyze.call_args.args[1]
    loop.stop_memory_extractions(service.workspace)


def test_rule_check_is_local_and_semantic_check_reports_only_candidate_pages(wiki, monkeypatch):
    from qrclaw.server.wiki_service import SemanticReport
    wiki.save_page("关联页", "配置约束")
    wiki.save_page("主页面", "[[关联页#章节|别名]] 内容")
    llm = MagicMock()
    monkeypatch.setattr("qrclaw.llm_service.get_llm_service", lambda: llm)
    assert WikiService(wiki.memory_dir).lint()["semantic"] is False
    llm.structured.assert_not_called()
    llm.structured.return_value = SemanticReport(issues=[{"page_name": "主页面", "detail": "明确文本中的疑似冲突"}, {"page_name": "编造页面", "detail": "无依据"}])
    report = WikiService(wiki.memory_dir).lint(semantic=True)
    assert "3000" in report["scope"]
    assert any(i["page_name"] == "主页面" and "疑似" in i["detail"] for i in report["issues"])
    assert not any(i["page_name"] == "编造页面" for i in report["issues"])
    assert wiki.get_page("主页面").revision == 1


def test_status_reports_failed_extraction_without_raw_body(wiki):
    from qrclaw.memory.wiki.extraction.indexer import ExtractionIndexer
    indexer = ExtractionIndexer(wiki.memory_dir, "失败会话")
    def fail(state):
        state.extraction_status = "failed"
        state.last_error = "模型故障"
        state.pending_messages = [{"uuid": "u", "role": "user", "content": "私有来源正文"}]
    indexer.update(fail)
    progress = WikiService(wiki.memory_dir).status()
    assert progress["pending"] == 1
    assert progress["extractions"][0]["status"] == "failed"
    assert progress["extractions"][0]["error"] == "模型故障"
    assert "私有来源正文" not in str(progress)
    indexer.state_file.write_text('{"pending_messages": null}')
    assert WikiService(wiki.memory_dir).status()["extractions"][0]["error"] == "提取进度无法读取"


def test_create_race_preserves_unreadable_file_and_rejects_long_name(wiki):
    original = "---\nrevision: 错误\n---\n保留的原正文"
    path = wiki.pages_dir / "并发创建.md"
    path.write_text(original)
    with pytest.raises(RuntimeError):
        wiki.save_page("并发创建", "旧任务的创建结果", expected_missing=True)
    assert path.read_text() == original
    with pytest.raises(ValueError):
        wiki.save_page("长" * 85, "新正文")
