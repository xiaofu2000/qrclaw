"""上下文完整链路回归：保存、压缩、取消、计划注入与子任务隔离。"""
import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console

from qrclaw.graph.nodes.react_loop import run_react_loop
from qrclaw.memory.context import context_manager as contexts
from qrclaw.memory.context.session import Session, delete_session
from qrclaw.providers.base import LLMResponse, ToolCall


def workspace_at(path):
    """提供完全位于临时目录的工作区路径。"""
    return SimpleNamespace(
        memory_dir=path / "memory", sessions_dir=path / "sessions", skills_dir=path / "skills",
        agent_file=path / "AGENT.md", heartbeat_file=path / "HEARTBEAT.md",
    )


def test_tool_history_persists_and_compresses_before_next_request(tmp_path, monkeypatch):
    """工具结果先保存，再在下一次真实模型请求前压缩；最新用户要求保留原文。"""
    session = Session(tmp_path, "tool-history")
    session.add({"role": "user", "content": "请检查项目，不要删除业务功能"})
    monkeypatch.setattr(contexts, "build_system_prompt", lambda **_: "系统提示")
    ctx = contexts.ContextManager(session, workspace_at(tmp_path))
    monkeypatch.setattr(contexts, "COMPRESS_THRESHOLD", 200)
    monkeypatch.setattr("qrclaw.memory.compression.compressor.COMPRESS_RECENT_MAX_TOKENS", 30)
    requests, summaries = [], []

    def summarize(messages, **kwargs):
        """观察压缩之前的磁盘记录，确保工具完整落盘且摘要包含调用参数。"""
        persisted = json.loads(session._path.read_text())
        assert [m["role"] for m in persisted] == ["user", "assistant", "tool"]
        summaries.append(messages[0]["content"])
        return LLMResponse(content="已读取文件，关键结论：工具结果需要复用")

    monkeypatch.setattr("qrclaw.llm_service.LLMService.chat", lambda _, *a, **k: summarize(*a, **k))

    def chat(messages, **kwargs):
        requests.append(messages)
        if len(requests) == 1:
            return LLMResponse(content="读取配置", finish_reason="tool_calls", tool_calls=[ToolCall("t1", "read_file", '{"path":"/project/config.py"}')])
        assert messages[1]["content"].startswith("[SUMMARY]")
        assert "不要删除业务功能" in messages[2]["content"]
        return LLMResponse(content="检查完成")

    assert run_react_loop([], [], session=session, context_manager=ctx, silent=True,
                          llm_service=SimpleNamespace(chat=chat), on_tool_call=lambda *_: "大量工具结果 " * 1500) == "检查完成"
    assert len(requests) == 2 and len(summaries) == 1
    assert "read_file" in summaries[0] and "/project/config.py" in summaries[0]
    assert "大量工具结果" in summaries[0]
    assert Session(tmp_path, "tool-history").messages == session.messages
    assert all(message.get("uuid") for message in session.messages)


def test_cancellation_preserves_completed_and_pending_tool_results(tmp_path):
    """中途取消仍保留已完成工具结果，为未完成工具提供明确的中断结果。"""
    from qrclaw.execution.context import RunCancelled
    session = Session(tmp_path, "cancel")
    session.add({"role": "user", "content": "执行两个工具"})
    response = LLMResponse(content="", finish_reason="tool_calls", tool_calls=[ToolCall("a", "first", "{}"), ToolCall("b", "second", "{}")])

    def run_tool(name, _):
        if name == "second":
            raise RunCancelled("用户取消")
        return "第一个工具已完成"

    with pytest.raises(RunCancelled):
        run_react_loop(list(session.messages), [], session=session, silent=True,
                       llm_service=SimpleNamespace(chat=lambda **_: response), on_tool_call=run_tool)
    restored = Session(tmp_path, "cancel")
    assert [m["role"] for m in restored.messages] == ["user", "assistant", "tool", "tool"]
    assert [m["tool_call_id"] for m in restored.messages[-2:]] == ["a", "b"]
    assert restored.messages[-2]["content"] == "第一个工具已完成"
    assert "中断" in restored.messages[-1]["content"]


@pytest.mark.parametrize("contents", ["{", "{}", '[{"role":"user","content":123}]', '["bad"]'])
def test_corrupt_session_is_never_silently_overwritten(tmp_path, contents):
    """损坏会话必须拒绝加载，文件保持原样。"""
    path = tmp_path / "corrupt.json"
    path.write_text(contents)
    with pytest.raises(ValueError, match="保留原文件"):
        Session(tmp_path, "corrupt")
    assert path.read_text() == contents


def test_atomic_save_failure_keeps_disk_and_memory(tmp_path, monkeypatch):
    """替换失败不损坏历史，也不把未保存的新消息留在内存。"""
    session = Session(tmp_path, "atomic")
    session.add({"role": "user", "content": "保留原历史"})
    before, data = session.messages, session._path.read_bytes()
    monkeypatch.setattr("qrclaw.memory.context.session.os.replace", Mock(side_effect=OSError("磁盘失败")))
    with pytest.raises(OSError):
        session.add({"role": "assistant", "content": "未保存"})
    assert session.messages is before and session._path.read_bytes() == data
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("session_id", ["../outside", "/outside", "a/b", "a\\b", ""])
def test_session_identifiers_cannot_escape_directory(tmp_path, session_id):
    """加载与删除共用会话 ID 校验。"""
    with pytest.raises(ValueError):
        Session(tmp_path, session_id)
    with pytest.raises(ValueError):
        delete_session(session_id, tmp_path)


@pytest.mark.parametrize("step_count", [1, 2])
def test_router_plan_passes_wiki_to_serial_parallel_and_final(tmp_path, monkeypatch, step_count):
    """经过真实 Router 和 GraphRunner，Wiki 正文能到达每个子任务及最终整合。"""
    from qrclaw.graph.nodes import router
    from qrclaw.graph.nodes.wiki_query import WikiQueryResult
    from qrclaw.graph.runner import GraphRunner
    session = Session(tmp_path / "sessions", "plan")
    session.add({"role": "user", "content": "完成计划"})
    ctx = contexts.init_context_manager(session, workspace_at(tmp_path))
    monkeypatch.setattr(contexts, "build_system_prompt", lambda **kw: kw["wiki_context"])
    result = router.RouteSchema(thought="需要分步", route="plan", goal="测试目标", project_path="/project",
                                steps=[router.PlanStepSchema(id=i, description=f"步骤 {i}") for i in range(1, step_count + 1)])
    client = Mock()
    client.chat.completions.create.return_value = result
    monkeypatch.setattr(router, "_get_instructor_client", lambda: client)
    monkeypatch.setattr(router.WikiQueryNode, "run", lambda *a, **k: WikiQueryResult([], "Wiki 关键配置正文"))
    runner = GraphRunner()
    runner.plan_executor.replanner.run = lambda: None
    tasks = []

    def child(task, *args, **kwargs):
        tasks.append(task)
        assert "Wiki 关键配置正文" in task and "/project" in task
        return "步骤结果", None

    def final(*args, **kwargs):
        assert ctx.plan_state is not None
        assert "Wiki 关键配置正文" in ctx.build_messages("react")[0]["content"]
        return "计划完成"

    runner.react_loop.run = final
    assert runner.run("完成计划", session, Console(file=StringIO()), workspace_at(tmp_path), run_sub_agent_fn=child) == "计划完成"
    assert len(tasks) == step_count and ctx.plan_state is None


def test_sub_agent_restores_thread_context_on_initialization_failure(tmp_path, monkeypatch):
    """子会话初始化失败也恢复父会话、工作区、执行上下文和深度。"""
    from qrclaw import agent
    parent = Session(tmp_path, "parent")
    workspace = workspace_at(tmp_path)
    ctx = contexts.init_context_manager(parent, workspace)
    agent.set_session(parent)
    agent.set_workspace(workspace)
    agent.set_agent_depth(0)
    execution = object()
    agent.set_execution_context(execution)
    monkeypatch.setattr(agent, "Session", Mock(side_effect=OSError("初始化失败")))
    with pytest.raises(OSError):
        agent.run_sub_agent("任务", workspace, "child")
    assert agent.get_session() is parent and agent.get_workspace() is workspace
    assert agent.get_execution_context() is execution and agent.get_agent_depth() == 0
    assert contexts.get_context_manager() is ctx
    agent.set_execution_context(None)


def test_request_budget_counts_tools_and_current_model(tmp_path, monkeypatch):
    """预算包含工具定义，分词器随当前模型变化，忽略旧响应的使用量。"""
    from qrclaw.memory import token_utils
    from qrclaw.providers import provider
    seen = []
    monkeypatch.setattr(token_utils.litellm, "token_counter", lambda **kw: seen.append(kw) or (10000 if kw.get("tools") else 10))
    monkeypatch.setattr(provider, "_model", "current-model")
    session = Session(tmp_path, "budget")
    session.add({"role": "user", "content": "当前要求"})
    session.prompt_tokens = 999999
    monkeypatch.setattr(contexts, "build_system_prompt", lambda **_: "系统")
    monkeypatch.setattr(contexts, "_MODEL_MAX_TOKENS", 1000)
    ctx = contexts.ContextManager(session, workspace_at(tmp_path))
    assert ctx.build_messages("react")
    with pytest.raises(ValueError, match="预算"):
        ctx.build_messages("react", tools=[{"type": "function", "function": {"name": "large"}}])
    assert all(call["model"] == "current-model" for call in seen)
    assert any(call.get("tools") for call in seen)


def test_abrupt_exit_recovers_missing_tool_result(tmp_path):
    """进程直接退出时，恢复出的会话仍满足工具调用配对要求。"""
    session = Session(tmp_path, "abrupt")
    session.add({"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
        {"id": "b", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
    ]})
    session.add({"role": "tool", "tool_call_id": "a", "content": "已保存结果"})
    resumed = Session(tmp_path, "abrupt")
    resumed.add({"role": "user", "content": "继续"})
    assert resumed.messages[1]["content"] == "已保存结果"
    assert resumed.messages[2]["tool_call_id"] == "b" and "中断" in resumed.messages[2]["content"]
    assert len(Session(tmp_path, "abrupt").messages) == 4


def test_semantic_wiki_recall_without_literal_query_match(tmp_path, monkeypatch):
    """自然语言问题没有完整命中页面子串时，仍然允许语义选择。"""
    from qrclaw.memory.wiki import WikiMemory
    from qrclaw.memory.wiki.selection.schemas import SelectionResult, SelectedPage
    wiki = WikiMemory(tmp_path / "semantic")
    wiki.save_page("编码偏好", "要求用中文日志")
    select = Mock(return_value=SelectionResult(selected=[SelectedPage(name="编码偏好", reason="与提问相关", relevance=1)]))
    monkeypatch.setattr("qrclaw.memory.wiki.selection.WikiPageSelector.select", select)
    pages = wiki.select_relevant_pages("我以前对日志格式有什么要求？", [])
    assert pages[0].name == "编码偏好"
    select.assert_called_once()


def test_every_react_node_run_finishes_extraction(tmp_path, monkeypatch):
    """每轮使用新 ContextManager 和节点，仍完成提取并跨恢复保持检查点。"""
    from qrclaw.graph.nodes.react_loop import ReactLoopNode
    from qrclaw.memory.wiki.extraction.schemas import ExtractionSchema
    monkeypatch.setenv("MEMORY_INIT_THRESHOLD", "1")
    monkeypatch.setenv("MEMORY_UPDATE_INTERVAL", "1")
    monkeypatch.setattr("qrclaw.llm_service.LLMService.chat", lambda *a, **k: LLMResponse(content="最终回答"))
    analyze = Mock(return_value=ExtractionSchema(needs_update=False, pages=[]))
    monkeypatch.setattr("qrclaw.memory.wiki.extraction.llm.WikiLLMAnalyzer.analyze", analyze)
    monkeypatch.setattr(contexts, "build_system_prompt", lambda **_: "系统")
    for turn in range(2):
        session = Session(tmp_path / "sessions", "turns")
        session.add({"role": "user", "content": f"第 {turn} 轮新要求"})
        contexts.init_context_manager(session, workspace_at(tmp_path))
        assert ReactLoopNode().run(session, Console(file=StringIO()), workspace_at(tmp_path)) == "最终回答"
        assert session.messages[-1]["memory_extracted"]
    assert analyze.call_count == 2
    assert "第 0 轮" not in analyze.call_args.args[1]


@pytest.mark.parametrize("role", ["router", "replanner"])
def test_router_and_replanner_compress_without_losing_instructions(tmp_path, monkeypatch, role):
    """路由与重规划也检查实际输入；摘要战报不修改原始步骤结果和任务指令。"""
    from qrclaw.graph.nodes.router import PlanStep
    from qrclaw.memory.context.step_result import StepResult
    monkeypatch.setattr(contexts, "COMPRESS_THRESHOLD", 100)
    monkeypatch.setattr("qrclaw.memory.compression.compressor.COMPRESS_RECENT_MAX_TOKENS", 10)
    monkeypatch.setattr("qrclaw.llm_service.LLMService.chat", lambda *a, **k: LLMResponse(content="保留关键结论"))
    session = Session(tmp_path, role)
    session.add({"role": "assistant", "content": "旧的结果 " * 500})
    session.add({"role": "user", "content": "当前用户要求"})
    ctx = contexts.ContextManager(session, workspace_at(tmp_path))
    if role == "router":
        messages = ctx.build_messages(role, route_instruction="只返回 JSON")
        assert messages[0]["role"] == "system"
        assert messages[-1]["content"] == "只返回 JSON"
        assert messages[-2]["content"] == "当前用户要求"
    else:
        ctx.set_plan("必须完成的目标", [PlanStep("2", "后续任务")], "/project")
        result = StepResult("1", "前置步骤", "大量步骤结果 " * 500)
        ctx.add_step_result(result)
        messages = ctx.build_messages(role, replanner_instruction="判断是否完成")
        assert all(text in messages[0]["content"] for text in ["必须完成的目标", "/project", "后续任务"])
        assert messages[-1]["content"] == "判断是否完成"
        assert ctx.plan_state.past_steps[0] is result
    assert "保留关键结论" in str(messages)


def test_provider_strips_session_metadata():
    """会话 UUID 和提取检查点不得发送给模型 API。"""
    from qrclaw.providers.litellm_provider import LiteLLMProvider
    assert LiteLLMProvider._sanitize([{
        "role": "assistant", "content": "回答", "uuid": "id", "memory_extracted": True,
    }]) == [{"role": "assistant", "content": "回答"}]
