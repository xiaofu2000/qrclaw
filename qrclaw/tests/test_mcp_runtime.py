"""验证 MCP 按需发现、真实 stdio 调用和连接清理。"""

import json
import sys
from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

import qrclaw.config as config
import qrclaw.tools  # noqa: F401
from qrclaw.agent import _cleanup_mcp, _init_mcp
from qrclaw.graph.nodes.react_loop import run_react_loop
from qrclaw.mcp_integration.client import MCPManager, _MCPConnection
from qrclaw.mcp_integration.bridge import get_active_manager, register_mcp_tools, unregister_mcp_tools
from qrclaw.prompt_builder import _build_tooling_section, build_system_prompt
from qrclaw.providers.base import LLMResponse, ToolCall
from qrclaw.tools.registry import _tools, execute, get_schemas, get_schemas_for_sub_agent


@pytest.fixture
def stdio_mcp(temp_dir, monkeypatch):
    """用 170 个工具的 SDK 服务验证真实传输，不访问用户配置的服务。"""
    server = temp_dir / "echo_server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "from pydantic import BaseModel\n"
        "mcp = FastMCP('test', instructions='提供文本回显与嵌套数据验证；需要核对文本或数据结构时使用。')\n"
        "class Payload(BaseModel):\n"
        "    value: int\n"
        "    note: str | None = None\n"
        "@mcp.tool()\n"
        "def echo(text: str, count: int = 1) -> str:\n"
        "    return 'echo:' + text * count\n"
        "@mcp.tool()\n"
        "def nested(payload: Payload) -> str:\n"
        "    return payload.model_dump_json()\n"
        "@mcp.tool()\n"
        "def no_args() -> str:\n"
        "    return '无参数调用成功'\n"
        "@mcp.tool()\n"
        "def fail() -> str:\n"
        "    raise ValueError('测试服务拒绝执行')\n"
        "def filler(value: str) -> str:\n"
        "    return value\n"
        "for index in range(166):\n"
        "    mcp.add_tool(filler, name=f'unused_{index}', description='普通占位工具')\n"
        "mcp.run()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "MCP_ENABLED", True)
    monkeypatch.setattr(config, "MCP_SERVERS", [{
        "transport": "stdio", "command": sys.executable, "args": [str(server)],
    }])

    assert _init_mcp()
    try:
        yield get_active_manager()
    finally:
        _cleanup_mcp()
    assert not any(name.startswith("mcp__") for name in _tools)


def test_stdio_mcp_tool_lifecycle(stdio_mcp):
    """大量工具只暴露两个入口，搜索结果受限，调用复用原连接。"""
    assert len(stdio_mcp.tool_names) == 170
    assert stdio_mcp.has_tool('echo')
    assert stdio_mcp.has_tool('search_tools')
    assert stdio_mcp.has_tool('server-0__echo')
    names = {schema['function']['name'] for schema in get_schemas()}
    assert {name for name in names if name.startswith('mcp__')} == {
        'mcp__search_tools', 'mcp__call_tool',
    }
    assert not any(schema['function']['name'].startswith('mcp__')
                   for schema in get_schemas_for_sub_agent())
    assert 'unused_0' not in _build_tooling_section(list(names))
    prompt = build_system_prompt()
    assert 'MCP 能力与使用时机' in prompt
    assert '提供文本回显与嵌套数据验证' in prompt
    assert 'unused_0' in prompt and 'unused_165' in prompt
    assert 'inputSchema' not in stdio_mcp.get_capability_summary()
    assert 'MCP 能力与使用时机' not in build_system_prompt(is_sub_agent=True)

    found = json.loads(execute('mcp__search_tools', '{"query":"server-0__echo"}'))
    assert len(found) <= 5
    assert found[0]['name'] == 'server-0__echo'
    assert found[0]['inputSchema']['required'] == ['text']
    assert execute('mcp__call_tool', json.dumps({
        'name': found[0]['name'], 'arguments': {'text': 'hello'},
    })) == 'echo:hello'
    assert len(json.loads(execute('mcp__search_tools', '{"query":"unused"}'))) == 5
    assert json.loads(execute('mcp__search_tools', '{"query":"找不到的工具"}')) == []
    assert json.loads(execute('mcp__search_tools', '{"query":""}')) == []


def test_mcp_call_validates_nested_schema_and_preserves_errors(stdio_mcp):
    """完整 schema 校验覆盖嵌套引用、必填字段和类型，远端错误保持失败。"""
    found = json.loads(execute('mcp__search_tools', '{"query":"server-0__nested"}'))
    assert found[0]['inputSchema']['required'] == ['payload']
    result = execute('mcp__call_tool', json.dumps({
        'name': found[0]['name'], 'arguments': {'payload': {'value': 7, 'note': None}},
    }))
    assert json.loads(result) == {'value': 7, 'note': None}
    for arguments in ({}, {'text': 'hello', 'count': '错误类型'}):
        error = execute('mcp__call_tool', json.dumps({
            'name': 'server-0__echo', 'arguments': arguments,
        }))
        assert error.startswith('错误：')
        assert 'MCP 工具参数无效' in error
        assert 'Failed validating' not in error
    assert execute('mcp__call_tool', '{"name":"server-0__no_args"}') == '无参数调用成功'
    assert execute('mcp__call_tool', '{"name":"server-0__fail"}').startswith('错误：')
    assert execute('mcp__call_tool', '{"name":"不存在"}').startswith('错误：')
    assert execute('mcp__call_tool', '{"name":"call_tool"}').startswith('错误：')


def test_react_loop_keeps_only_mcp_entry_points_in_every_request(stdio_mcp):
    """三轮真实工具循环始终只传两个 MCP 入口，发现的 schema 留在结果中。"""
    requests = []

    class FakeLLM:
        """只替换模型回答，保留搜索、调用和会话消息的真实执行路径。"""

        def chat(self, messages, tools):
            """根据上一轮工具结果生成下一步调用。"""
            requests.append([schema['function']['name'] for schema in tools
                             if schema['function']['name'].startswith('mcp__')])
            if len(requests) == 1:
                call = ToolCall('search', 'mcp__search_tools', '{"query":"server-0__echo"}')
            elif len(requests) == 2:
                found = json.loads(messages[-1]['content'])
                call = ToolCall('call', 'mcp__call_tool', json.dumps({
                    'name': found[0]['name'], 'arguments': {'text': 'hello'},
                }))
            else:
                assert messages[-1]['content'] == 'echo:hello'
                return LLMResponse(content='完成')
            return LLMResponse(content='', tool_calls=[call], finish_reason='tool_calls')

    result = run_react_loop(
        messages=[{'role': 'user', 'content': '用 MCP 回显 hello'}],
        tools=get_schemas(), on_tool_call=execute, llm_service=FakeLLM(), silent=True,
    )
    assert result == '完成'
    assert requests == [['mcp__search_tools', 'mcp__call_tool']] * 3


def test_ordinary_chat_does_not_search_mcp(stdio_mcp):
    """没有工具调用的普通回复不会触发自动检索。"""
    tool_call = Mock()
    result = run_react_loop(
        messages=[{'role': 'user', 'content': '你好'}], tools=get_schemas(),
        on_tool_call=tool_call, silent=True,
        llm_service=SimpleNamespace(chat=Mock(return_value=LLMResponse(content='你好'))),
    )
    assert result == '你好'
    tool_call.assert_not_called()


@pytest.mark.asyncio
async def test_catalog_pagination_filtering_and_same_named_tools(monkeypatch):
    """分页工具都可发现，白名单不可绕过，不同服务的同名工具分别路由。"""
    manager = MCPManager()
    sessions = {}

    async def connect(label, _cfg):
        """替换传输层，记录每个服务实际收到的调用。"""
        session = AsyncMock()
        sessions[label] = session
        session.list_tools.side_effect = [
            ListToolsResult(tools=[Tool(name='blocked', inputSchema={'type': 'object'})],
                            nextCursor='page-2'),
            ListToolsResult(tools=[Tool(name='echo', inputSchema={
                'type': 'object',
                '$defs': {'Payload': {
                    'type': 'object', 'properties': {'value': {'type': 'integer'}},
                    'required': ['value'],
                }},
                'properties': {'payload': {'$ref': '#/$defs/Payload'}},
            })]),
        ]
        session.call_tool.return_value = CallToolResult(content=[TextContent(type='text', text=label)])
        return _MCPConnection(label=label, exit_stack=AsyncExitStack(), session=session)

    monkeypatch.setattr(manager, '_connect_stdio', connect)
    await manager.connect_all([
        {'label': 'first', 'include_tools': ['echo']},
        {'label': 'second', 'exclude_tools': ['blocked']},
    ])
    try:
        assert len(manager.get_tool_schemas()) == 2
        assert manager.has_tool('first__echo')
        assert not manager.has_tool('echo')
        found = json.loads(await manager.call_tool('search_tools', {'query': 'echo'}))
        assert {tool['name'] for tool in found} == {'first__echo', 'second__echo'}
        # FastMCP 展开展示用的简单引用；调用仍按原始 schema 校验。
        assert all(tool['inputSchema']['properties']['payload']['properties']['value'] ==
                   {'type': 'integer'} for tool in found)
        with pytest.raises(Exception, match='not of type'):
            await manager.call_tool('call_tool', {
                'name': 'first__echo', 'arguments': {'payload': {'value': '错误类型'}},
            })
        sessions['first'].call_tool.assert_not_awaited()
        assert json.loads(await manager.call_tool('search_tools', {'query': 'blocked'})) == []
        for label, session in sessions.items():
            assert await manager.call_tool('call_tool', {'name': f'{label}__echo'}) == label
            session.call_tool.assert_awaited_once_with('echo', {})
            assert session.list_tools.await_args_list[1].kwargs == {'cursor': 'page-2'}
        with pytest.raises(KeyError):
            await manager.call_tool('echo', {})
        with pytest.raises(Exception, match='Unknown tool'):
            await manager.call_tool('call_tool', {'name': 'first__blocked'})
        assert all(session.call_tool.await_count == 1 for session in sessions.values())
    finally:
        await manager.disconnect_all()
    assert manager.get_tool_schemas() == []


@pytest.mark.asyncio
async def test_empty_allowlist_exposes_no_entry_points(monkeypatch):
    """显式空白名单保留禁用语义，不能通过调用入口访问原工具。"""
    manager = MCPManager()
    session = AsyncMock()
    session.list_tools.return_value = ListToolsResult(tools=[
        Tool(name='echo', inputSchema={'type': 'object'}),
    ])
    monkeypatch.setattr(manager, '_connect_stdio', AsyncMock(return_value=_MCPConnection(
        label='test', exit_stack=AsyncExitStack(), session=session,
    )))
    await manager.connect_all([{'label': 'test', 'include_tools': []}])
    try:
        assert manager.get_tool_schemas() == []
        assert manager.get_capability_summary() == ''
        assert not manager.has_tool('call_tool')
        with pytest.raises(Exception, match='Unknown tool'):
            await manager.call_tool('call_tool', {'name': 'test__echo'})
        session.call_tool.assert_not_awaited()
    finally:
        await manager.disconnect_all()


@pytest.mark.asyncio
async def test_capability_summary_uses_connected_allowed_tools_and_stays_small(monkeypatch):
    """任意服务都能生成简介，配置可覆盖说明，过滤工具与大参数不进入提示词。"""
    manager = MCPManager()

    async def connect(label, _cfg):
        """只替换传输，能力提取、搜索注册和提示词构建使用真实实现。"""
        if label == 'offline':
            raise ConnectionError('测试连接不可用')
        tools = [Tool(name='blocked', description='隐藏的工具描述', inputSchema={'type': 'object'})]
        tools.extend(Tool(name=f'lookup_{index}', description='查询库存状态 ' + '说明' * 300,
                          inputSchema={'type': 'object', 'description': '参数内容不应注入' * 500})
                     for index in range(170))
        session = AsyncMock()
        session.list_tools.return_value = ListToolsResult(tools=tools)
        return _MCPConnection(label=label, exit_stack=AsyncExitStack(), session=session,
                              instructions='服务原始说明' if label == 'inventory' else '')

    monkeypatch.setattr(manager, '_connect_stdio', connect)
    await manager.connect_all([
        {'label': 'inventory', 'description': '核对商品库存时使用。' + '库存说明' * 1000,
         'exclude_tools': ['blocked']},
        {'label': 'warehouse', 'include_tools': ['lookup_0']},
        {'label': 'disabled', 'include_tools': []},
        {'label': 'offline'},
    ])
    try:
        await register_mcp_tools(manager)
        summary = manager.get_capability_summary()
        assert '核对商品库存时使用' in summary
        assert '服务原始说明' not in summary
        assert 'warehouse' in summary and '查询库存状态' in summary
        assert all(f'lookup_{index}：' in summary for index in range(170))
        assert len(summary) < 18000
        prompt = build_system_prompt()
        assert summary in prompt
        for hidden in ('blocked', '隐藏的工具描述', 'disabled', 'offline', '参数内容不应注入', 'codegraph'):
            assert hidden not in prompt
        assert [s['function']['name'] for s in get_schemas() if s['function']['name'].startswith('mcp__')] == [
            'mcp__search_tools', 'mcp__call_tool',
        ]
    finally:
        await unregister_mcp_tools()
        await manager.disconnect_all()
    assert manager.get_capability_summary() == ''
    assert 'MCP 能力与使用时机' not in build_system_prompt()


@pytest.mark.asyncio
async def test_search_ranks_tail_capability_above_generic_matches(monkeypatch):
    """后排工具按用途排到前面，能力目录、搜索和调用均不受前三项限制。"""
    manager = MCPManager()
    session = AsyncMock()
    session.list_tools.return_value = ListToolsResult(tools=[
        *(Tool(name=f'status_{index}', description='Read service health status.',
               inputSchema={'type': 'object'}) for index in range(20)),
        Tool(name='parcel_status', description='Track parcel delivery shipment status.',
             inputSchema={'type': 'object', 'properties': {'tracking_number': {'type': 'string'}},
                          'required': ['tracking_number']}),
    ])
    session.call_tool.return_value = CallToolResult(content=[TextContent(type='text', text='已签收')])
    monkeypatch.setattr(manager, '_connect_stdio', AsyncMock(return_value=_MCPConnection(
        label='shipping', exit_stack=AsyncExitStack(), session=session,
    )))
    await manager.connect_all([{'label': 'shipping'}])
    try:
        summary = manager.get_capability_summary()
        assert 'parcel_status：Track parcel delivery shipment status.' in summary
        assert 'tracking_number' not in summary
        found = json.loads(await manager.call_tool('search_tools', {
            'query': 'parcel delivery shipment status',
        }))
        assert len(found) == 5
        assert found[0]['name'] == 'shipping__parcel_status'
        assert found[0]['inputSchema']['required'] == ['tracking_number']
        assert await manager.call_tool('call_tool', {
            'name': found[0]['name'], 'arguments': {'tracking_number': 'TEST-123'},
        }) == '已签收'
        session.call_tool.assert_awaited_once_with('parcel_status', {'tracking_number': 'TEST-123'})
        assert [tool['function']['name'] for tool in manager.get_tool_schemas()] == ['search_tools', 'call_tool']
    finally:
        await manager.disconnect_all()
