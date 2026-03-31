"""
执行引擎工具函数

提供拓扑排序相关工具：
- get_next_layer: 从剩余步骤中取出当前可执行的一层（供 Replanner 循环使用）
"""
from qrclaw.logger import get_logger
from .nodes.router import PlanStep

logger = get_logger("qrclaw.graph.executor")


def get_next_layer(steps: list[PlanStep]) -> list[PlanStep]:
    """
    从剩余步骤中取出当前可执行的一层（in_degree 为 0 的步骤）。
    供 Replanner 循环按需调用，每次只算一层。
    """
    if not steps:
        return []
    step_ids = {s.id for s in steps}
    # 只考虑剩余步骤内部的依赖，忽略已完成步骤的 id
    ready = [s for s in steps if all(dep not in step_ids for dep in s.depends_on)]
    return sorted(ready, key=lambda s: s.id)
