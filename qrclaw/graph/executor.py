"""
执行引擎工具函数

提供拓扑排序相关工具：
- _topological_layers: 一次性计算所有层（调试用）
- get_next_layer: 从剩余步骤中取出当前可执行的一层（供 Replanner 循环使用）
"""
from qrclaw.logger import get_logger
from .nodes.router import PlanStep

logger = get_logger("qrclaw.graph.executor")


def _topological_layers(steps: list[PlanStep]) -> list[list[PlanStep]]:
    """
    拓扑排序：把步骤按依赖关系分成若干层。
    同一层内的步骤互不依赖，可以并行执行。
    层与层之间串行，后一层依赖前一层全部完成。

    Returns:
        layers: [[step1, step3], [step2], [step5], ...]
    Raises:
        ValueError: 存在循环依赖时
    """
    step_map = {s.id: s for s in steps}
    in_degree = {s.id: len(s.depends_on) for s in steps}
    layers = []
    remaining = set(s.id for s in steps)

    while remaining:
        ready_ids = [sid for sid in remaining if in_degree[sid] == 0]
        if not ready_ids:
            raise ValueError(
                f"Plan 存在循环依赖，无法执行的步骤: "
                f"{[step_map[sid].description for sid in remaining]}"
            )
        layer = [step_map[sid] for sid in sorted(ready_ids)]
        layers.append(layer)
        for sid in ready_ids:
            remaining.remove(sid)
            for other_id in remaining:
                if sid in step_map[other_id].depends_on:
                    in_degree[other_id] -= 1

    return layers


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
