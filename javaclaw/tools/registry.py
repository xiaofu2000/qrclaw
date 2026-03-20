import json

# 存所有注册的工具
# 结构：{ "工具名": {"fn": 函数本身, "schema": 给LLM看的描述} }
_tools: dict = {}


def register(schema: dict):
    """
    装饰器：把函数注册成一个工具。

    用法：
        @register(schema={...})
        def my_tool(path: str) -> str:
            ...
    """
    def decorator(fn):
        _tools[fn.__name__] = {
            "fn": fn,
            "schema": schema,
        }
        return fn
    return decorator


def get_schemas() -> list[dict]:
    """返回所有工具的 schema 列表，发给 LLM 用"""
    return [item["schema"] for item in _tools.values()]


def execute(name: str, arguments: str) -> str:
    """
    执行工具。
    name: 工具名
    arguments: LLM 返回的参数，是 JSON 字符串，需要先解析
    """
    if name not in _tools:
        return f"错误：找不到工具 {name}"

    args = json.loads(arguments)  # JSON 字符串 → dict
    return _tools[name]["fn"](**args)
