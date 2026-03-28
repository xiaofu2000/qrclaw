from .registry import get_schemas, execute, register

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
from . import agent_tools

__all__ = [
    "get_schemas",
    "execute",
    "register"
]