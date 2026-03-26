from .registry import get_tool_schemas, get_tool_func, register

# 导入所有工具模块以触发 @register 注册机制
from . import filesystem
from . import shell
from . import web
from . import memory_tools
from . import planning
from . import skills
from . import review_memory
from . import spawn_agent
from . import wait_agents

__all__ = [
    "get_tool_schemas",
    "get_tool_func",
    "register"
]
