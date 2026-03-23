"""
wait_agents 工具

等待所有后台子 agent 完成，收集并返回结果。
使用 thread.join() 阻塞等待，不轮询，零 CPU 浪费。
"""
from pydantic import BaseModel, Field
from qrclaw.tools.registry import register
from qrclaw.logger import get_logger

logger = get_logger("qrclaw.tools.wait_agents")


class WaitAgentsArgs(BaseModel):
    agent_ids: list[str] = Field(
        default_factory=list,
        description="要等待的子 agent ID 列表，不填则等待所有正在运行的子 agent"
    )


@register(
    description="等待后台子 agent 完成并收集结果。不填 agent_ids 则等待全部。",
    args_model=WaitAgentsArgs,
)
def wait_agents(agent_ids: list[str] = None) -> str:
    """
    阻塞等待子 agent 完成，使用 thread.join() 零 CPU 等待。

    Args:
        agent_ids: 要等待的子 agent ID 列表，空则等待全部
    Returns:
        str: 所有子 agent 的执行结果汇总
    """
    from qrclaw.tools.spawn_agent import get_task_pool, get_task_pool_lock

    task_pool = get_task_pool()
    lock = get_task_pool_lock()

    with lock:
        if not task_pool:
            return "没有正在运行的子 agent"

        # 确定要等待的 agent
        if agent_ids:
            targets = {aid: task_pool[aid] for aid in agent_ids if aid in task_pool}
            not_found = [aid for aid in agent_ids if aid not in task_pool]
            if not_found:
                logger.warning(f"未找到子 agent: {not_found}")
        else:
            targets = dict(task_pool)

        # 只等 running 状态的
        to_wait = {aid: t for aid, t in targets.items() if t["status"] == "running"}
        already_done = {aid: t for aid, t in targets.items() if t["status"] != "running"}

    logger.info(f"等待子 agent 完成: {list(to_wait.keys())}")

    # thread.join() 阻塞等待，线程跑完自动唤醒，零 CPU 消耗
    for agent_id, task in to_wait.items():
        logger.info(f"等待子 agent: {agent_id}")
        task["thread"].join()
        logger.info(f"子 agent {agent_id} 已完成")

    # 汇总所有结果
    lines = ["## 子 agent 执行结果汇总", ""]

    with lock:
        all_targets = {**already_done, **{aid: task_pool[aid] for aid in to_wait}}

    for agent_id, task in all_targets.items():
        status_label = "✅ 完成" if task["status"] == "done" else "❌ 出错"
        lines.append(f"### [{status_label}] {agent_id}")
        lines.append("")
        lines.append(task["result"] or "无结果")
        lines.append("")

    # 清空已完成的任务
    with lock:
        for agent_id in all_targets:
            if task_pool.get(agent_id, {}).get("status") != "running":
                task_pool.pop(agent_id, None)

    logger.info("所有子 agent 结果已收集，任务池已清理")
    return "\n".join(lines)
