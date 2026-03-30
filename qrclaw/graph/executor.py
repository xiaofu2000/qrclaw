"""
执行引擎

用拓扑排序解析 Plan 的依赖关系，自动决定哪些步骤并行、哪些串行。

执行规则：
  - 同一层（in_degree 为 0 且前置步骤全完成）的步骤并行 spawn 子 agent
  - 每层全部完成后，才解锁下一层
  - 串行链按层顺序执行

示例 Plan：
  step1（无依赖）─┐
  step3（无依赖）─┼─ 第0层：并行跑 step1/step3/step4
  step4（无依赖）─┘
  step2（依赖1）  ─── 第1层：等step1完成后跑
  step5（依赖2,3,4）── 第2层：等第1层全完成后跑
"""
import threading
from rich.console import Console
from qrclaw.logger import get_logger
from .planner import Plan, PlanStep

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
    # 计算每个步骤的入度
    in_degree = {s.id: len(s.depends_on) for s in steps}
    layers = []

    remaining = set(s.id for s in steps)

    while remaining:
        # 当前可执行的步骤：在 remaining 里且入度为 0
        ready_ids = [sid for sid in remaining if in_degree[sid] == 0]

        if not ready_ids:
            # 有步骤剩余但没有可执行的 → 循环依赖
            raise ValueError(
                f"Plan 存在循环依赖，无法执行的步骤: "
                f"{[step_map[sid].description for sid in remaining]}"
            )

        layer = [step_map[sid] for sid in sorted(ready_ids)]
        layers.append(layer)

        # 从 remaining 移除这一层，并更新入度
        for sid in ready_ids:
            remaining.remove(sid)
            for other_id in remaining:
                if sid in step_map[other_id].depends_on:
                    in_degree[other_id] -= 1

    return layers


def execute_plan(
    plan: Plan,
    console: Console,
    run_step_fn,
) -> dict[int, str]:
    """
    按拓扑顺序执行 Plan：同层并行，跨层串行。

    Args:
        plan: Planner 生成的 Plan 对象
        console: Rich console，用于打印进度
        run_step_fn: 执行单个步骤的函数
            签名: (step: PlanStep, plan: Plan) -> str
            返回步骤的执行结果字符串
    Returns:
        dict[step_id, result_str]: 每个步骤的执行结果
    """
    logger.info(f"开始执行计划: {plan.goal}，共 {len(plan.steps)} 步")

    try:
        layers = _topological_layers(plan.steps)
    except ValueError as e:
        logger.error(f"拓扑排序失败: {e}")
        console.print(f"[red]计划执行失败: {e}[/red]")
        return {}

    results: dict[int, str] = {}
    lock = threading.Lock()

    console.print(f"\n[bold cyan]执行计划：{plan.goal}[/bold cyan]")
    console.print(f"[dim]共 {len(plan.steps)} 步，分 {len(layers)} 层执行[/dim]\n")

    for layer_idx, layer in enumerate(layers):
        if len(layer) == 1:
            # 单个步骤，直接执行，不需要线程
            step = layer[0]
            console.print(
                f"[yellow]→ Step {step.id}[/yellow] {step.description} "
                f"[dim](串行)[/dim]"
            )
            result = run_step_fn(step, plan)
            plan.mark_done(step.id)
            with lock:
                results[step.id] = result
            logger.info(f"Step {step.id} 完成")

        else:
            # 多个步骤，并行执行
            console.print(
                f"[yellow]→ 第 {layer_idx + 1} 层并行[/yellow] "
                f"({len(layer)} 个步骤同时执行)"
            )
            for step in layer:
                console.print(
                    f"  [dim]Step {step.id}:[/dim] {step.description}"
                )

            threads = []
            errors = {}

            def _run(step: PlanStep):
                try:
                    result = run_step_fn(step, plan)
                    plan.mark_done(step.id)
                    with lock:
                        results[step.id] = result
                    logger.info(f"Step {step.id} 并行完成")
                except Exception as e:
                    logger.error(f"Step {step.id} 执行失败: {e}", exc_info=True)
                    with lock:
                        errors[step.id] = str(e)
                        results[step.id] = f"执行失败: {e}"

            for step in layer:
                t = threading.Thread(
                    target=_run,
                    args=(step,),
                    name=f"plan-step-{step.id}",
                    daemon=True,
                )
                threads.append(t)
                t.start()

            # 等待这一层全部完成
            for t in threads:
                t.join()

            if errors:
                failed = [f"Step {sid}" for sid in errors]
                console.print(f"[red]以下步骤执行失败: {', '.join(failed)}[/red]")
                logger.warning(f"层 {layer_idx + 1} 有步骤失败: {errors}")

        console.print()

    logger.info(f"计划执行完成: {plan.goal}")
    return results


def format_results(plan: Plan, results: dict[int, str]) -> str:
    """把执行结果格式化成 LLM 可读的汇总字符串"""
    lines = [f"## 计划执行完成：{plan.goal}", ""]
    for step in plan.steps:
        status = "✅" if step.done else "❌"
        lines.append(f"### {status} Step {step.id}: {step.description}")
        result = results.get(step.id, "无结果")
        # 结果太长时截断，避免 token 爆炸
        if len(result) > 500:
            result = result[:500] + "\n...(已截断，详情见日志)"
        lines.append(result)
        lines.append("")
    return "\n".join(lines)
