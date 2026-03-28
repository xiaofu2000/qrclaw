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
            continue  # 去掉 $defs 和所有层级的 title
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

    # 展开 $ref，内联所有引用，同时递归清理 title
    resolved = _resolve_refs(pydantic_schema, defs)

    properties = dict(resolved.get("properties", {}))

    # 无参数工具：省略 parameters 字段（Gemini 不接受空 properties）
    if not properties:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
            },
        }

    required = pydantic_schema.get("required", [])
    parameters: dict = {
        "type": "object",
        "properties": properties,
    }
    # required 为空时省略，避免某些 API 报错
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


def get_schemas() -> list[dict]:
    """返回所有工具的 schema 列表，发给 LLM 用"""
    schemas = [item["schema"] for item in _tools.values()]
    logger.debug(f"获取工具 schemas，共 {len(schemas)} 个工具")
    return schemas


def execute(name: str, arguments: str) -> str:
    """
    执行工具。
    用 Pydantic 模型校验参数，不合法直接报错，不会传脏数据给工具函数。
    同时执行安全切面检查。
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
        validated_args = validated.model_dump()
        logger.debug(f"工具 {name} 校验后参数: {validated_args}")

        # === AOP 安全拦截 (Security Hook) ===
        try:
            # 局部导入避免循环引用
            from qrclaw.agent import get_workspace
            from qrclaw.security import security_manager
            
            # 获取当前上下文的工作空间
            ws = get_workspace()
            
            # 仅当在 agent 运行上下文中时才检查
            # 如果是 CLI 直接调试工具或单元测试，可能没有 workspace，此时视为 Full Access
            if ws:
                security_manager.check_access(
                    agent_id=ws.agent_id,
                    tool_name=name,
                    args=validated_args,
                    workspace_root=ws.root
                )
        except PermissionError as pe:
            # 明确的安全拦截
            error_msg = str(pe)
            logger.warning(error_msg)
            return error_msg
        except ImportError:
            # 可能是环境问题，忽略
            pass
        except Exception as e:
            # 安全检查本身出错，为了安全起见，选择拦截并报错 (Fail Closed)
            error_msg = f"系统错误：执行安全检查时发生异常 ({e})"
            logger.error(error_msg, exc_info=True)
            return error_msg
        # ====================================

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
