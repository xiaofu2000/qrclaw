from .registry import get_schemas, get_schemas_for_sub_agent, execute, register

# 导入所有工具模块以触发 @register 注册机制
from . import filesystem
from . import shell
from . import web
from . import memory_tools
from . import skills
from . import review_memory
from . import spawn_agent
from . import wait_agents
from . import agent_tools
from . import add_project_path

__all__ = [
    "get_schemas",
    "get_schemas_for_sub_agent",
    "execute",
    "register"
]