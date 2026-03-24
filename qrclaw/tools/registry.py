import json
from typing import Type
from pydantic import BaseModel
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.registry")

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
        logger.debug(f"注册工具: {fn.__name__} (需要确认: {confirm})")
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

    # 某些 LLM API（如 Gemini 兼容层）不接受空 properties，无参数工具省略 parameters 字段
    if not properties:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
            },
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
    schemas = [item["schema"] for item in _tools.values()]
    logger.debug(f"获取工具 schemas，共 {len(schemas)} 个工具")
    return schemas


def execute(name: str, arguments: str) -> str:
    """
    执行工具。
    用 Pydantic 模型校验参数，不合法直接报错，不会传脏数据给工具函数。
    """
    if name not in _tools:
        error_msg = f"错误：找不到工具 {name}"
        logger.error(error_msg)
        return error_msg

    try:
        raw_args = json.loads(arguments)
        logger.debug(f"工具 {name} 原始参数: {raw_args}")

        # 用 Pydantic 校验并解析参数
        validated = _tools[name]["model"](**raw_args)
        logger.debug(f"工具 {name} 校验后参数: {validated.model_dump()}")

        # 执行工具
        result = _tools[name]["fn"](**validated.model_dump())
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