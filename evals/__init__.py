"""QRClaw 记忆与 Agent 效果回归评测框架（支持 offline 确定性回归与 live 真实模型对比）。"""

from evals.runner import EvalRunner, load_datasets, run_evaluations
from evals.schemas import CaseReport, CategoryDataset, CategorySummary, CheckItem, EvalReport
from evals.scripted_provider import ScriptedEvalProvider

__all__ = [
    "CaseReport",
    "CategoryDataset",
    "CategorySummary",
    "CheckItem",
    "EvalReport",
    "EvalRunner",
    "ScriptedEvalProvider",
    "load_datasets",
    "run_evaluations",
]
