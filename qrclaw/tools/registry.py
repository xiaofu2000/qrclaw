import json
from typing import Type
from pydantic import BaseModel

# 存所有注册的工具
# 结构：{ "工具名": {"fn": 函数本身, "model": Pydantic模型, "schema": 给LLM看的描述} }
_tools: dict = {}


def register(description: str, args_model: Type[BaseModel], confirm: bool = False):
    """
    装饰器：把函数注册成一个工具。

    confirm=True 表示执行前需要用户确认（高风险工具）
    """
    def decorator(fn):
        schema = _build_schema(fn.__name__, description, args_model)
        _tools[fn.__name__] = {
            "fn": fn,
            "model": args_model,
            "schema": schema,
            "confirm": confirm,
        }
        return fn
    return decorator


def need_confirm(name: str) -> bool:
    """判断工具是否需要用户确认"""
    return _tools.get(name, {}).get("confirm", False)


def _build_schema(name: str, description: str, args_model: Type[BaseModel]) -> dict:
    """从 Pydantic 模型生成 OpenAI Tool Schema"""
    # Pydantic v2 用 model_json_schema()
    pydantic_schema = args_model.model_json_schema()

    # 去掉 Pydantic 自动生成的 title 字段，LLM 不需要它
    properties = {
        k: {pk: pv for pk, pv in v.items() if pk != "title"}
        for k, v in pydantic_schema.get("properties", {}).items()
    }

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": pydantic_schema.get("required", []),
            },
        },
    }


def get_schemas() -> list[dict]:
    """返回所有工具的 schema 列表，发给 LLM 用"""
    return [item["schema"] for item in _tools.values()]


def execute(name: str, arguments: str) -> str:
    """
    执行工具。
    用 Pydantic 模型校验参数，不合法直接报错，不会传脏数据给工具函数。
    """
    if name not in _tools:
        return f"错误：找不到工具 {name}"

    try:
        raw_args = json.loads(arguments)
        # 用 Pydantic 校验并解析参数
        validated = _tools[name]["model"](**raw_args)
        return _tools[name]["fn"](**validated.model_dump())
    except json.JSONDecodeError:
        return f"错误：参数不是合法的 JSON"
    except Exception as e:
        return f"错误：参数校验失败 {e}"
