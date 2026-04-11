import json
from typing import Type
from pydantic import BaseModel
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.registry")

# 存所有注册的工具
_tools: dict = {}


# ── Agent 类型常量 ────────────────────────────────────────────────────────────

class AgentType:
    MAIN    = "main"       # 主 Agent，完整工具集
    SUB     = "sub"        # 子 Agent，执行类工具
    MEMORY  = "memory"     # 记忆 Agent，只读/写 Wiki


# ── 各 Agent 默认工具白名单（工具名列表，None 表示继承全量）──────────────────

_AGENT_TOOL_WHITELIST: dict[str, list[str] | None] = {
    AgentType.MAIN: None,  # 全量
    AgentType.SUB: [
        "read_file", "write_file", "list_directory",
        "run_shell", "web_search", "web_fetch",
        "use_skill",
    ],
    AgentType.MEMORY: [
        "read_wiki_page",
        "submit_memory_result",
    ],
}


def register(
    description: str,
    args_model: Type[BaseModel],
    confirm: bool = False,
    agents: list[str] | None = None,
):
    """
    装饰器：把函数注册成一个工具。

    Args:
        description: 工具描述
        args_model: Pydantic 参数模型
        confirm: True 表示执行前需要用户确认
        agents: 可见的 AgentType 列表，None 表示所有 Agent 可见
    """
    def decorator(fn):
        schema = _build_schema(fn.__name__, description, args_model)
        _tools[fn.__name__] = {
            "fn": fn,
            "model": args_model,
            "schema": schema,
            "confirm": confirm,
            "agents": agents,  # None = 所有 Agent 可见
        }
        logger.debug(f"注册工具: {fn.__name__} (agents={agents}, 需要确认: {confirm})")
        return fn
    return decorator


def need_confirm(name: str) -> bool:
    """判断工具是否需要用户确认"""
    return _tools.get(name, {}).get("confirm", False)


def _resolve_refs(schema: dict, defs: dict) -> dict:
    """递归展开 $ref 并清理 title，Gemini 不支持 $ref"""
    if "$ref" in schema:
        ref_name = schema["$ref"].split("/")[-1]
        resolved = _resolve_refs(defs.get(ref_name, {}), defs)
        other = {k: v for k, v in schema.items() if k != "$ref"}
        return {**resolved, **other}
    result = {}
    for k, v in schema.items():
        if k in ("$defs", "title"):
            continue
        elif isinstance(v, dict):
            result[k] = _resolve_refs(v, defs)
        elif isinstance(v, list):
            result[k] = [_resolve_refs(i, defs) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


def _build_schema(name: str, description: str, args_model: Type[BaseModel]) -> dict:
    """从 Pydantic 模型生成 OpenAI Tool Schema，兼容 Gemini"""
    pydantic_schema = args_model.model_json_schema()
    defs = pydantic_schema.get("$defs", {})

    resolved = _resolve_refs(pydantic_schema, defs)
    properties = dict(resolved.get("properties", {}))

    # 无参数工具：给一个空 parameters，兼容 MiniMax 等要求 parameters 必填的 API
    if not properties:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": {}},
            },
        }

    required = pydantic_schema.get("required", [])
    parameters: dict = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def get_schemas(agent_type: str = AgentType.MAIN) -> list[dict]:
    """返回指定 Agent 类型可见的工具 schema 列表"""
    whitelist = _AGENT_TOOL_WHITELIST.get(agent_type)
    schemas = []
    for name, item in _tools.items():
        tool_agents = item.get("agents")
        # 工具级过滤：tool 指定了 agents，且当前 agent_type 不在其中
        if tool_agents is not None and agent_type not in tool_agents:
            continue
        # Agent 白名单过滤：白名单存在，且工具名不在白名单中
        if whitelist is not None and name not in whitelist:
            continue
        schemas.append(item["schema"])
    logger.debug(f"获取工具 schemas [{agent_type}]，共 {len(schemas)} 个工具")
    return schemas


def get_schemas_for_sub_agent() -> list[dict]:
    return get_schemas(AgentType.SUB)


def get_schemas_for_memory_agent() -> list[dict]:
    return get_schemas(AgentType.MEMORY)


def execute(name: str, arguments: str) -> str:
    """执行工具，用 Pydantic 模型校验参数"""
    if name not in _tools:
        error_msg = f"错误：找不到工具 {name}"
        logger.error(error_msg)
        return error_msg

    try:
        raw_args = json.loads(arguments)
        logger.debug(f"工具 {name} 原始参数: {raw_args}")

        # 用 Pydantic 校验并解析参数
        validated = _tools[name]["model"](**raw_args)
        validated_args = validated.model_dump()
        logger.debug(f"工具 {name} 校验后参数: {validated_args}")

        # 执行工具
        result = _tools[name]["fn"](**validated_args)
        logger.info(f"工具 {name} 执行成功")

        return result
    except json.JSONDecodeError as e:
        error_msg = f"错误：参数不是合法的 JSON: {e}"
        logger.error(error_msg)
        return error_msg
    except Exception as e:
        error_msg = f"错误：参数校验失败 {e}"
        logger.error(error_msg, exc_info=True)
        return error_msg