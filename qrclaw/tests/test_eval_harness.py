"""回归评测集（evals/）与 GitHub Actions CI 配置的完整性及离线全量门禁测试。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from evals.__main__ import main as evals_cli_main
from evals.runner import DATASET_ORDER, EvalRunner, load_datasets, run_evaluations
from evals.schemas import EvalReport
from evals.scorers import (
    score_keyword_groups,
    score_plan_dag,
    score_summary_retention,
    score_tool_pair_integrity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_eval_datasets_schema_and_handler_coverage():
    """校验四大评测维度数据集 Schema 合法、ID 唯一且全部绑定真实处理器。"""
    datasets = load_datasets()
    assert tuple(d.category for d in datasets) == DATASET_ORDER

    counts = {d.category: len(d.cases) for d in datasets}
    assert counts == {
        "memory_extraction": 6,
        "memory_provenance_and_retrieval": 5,
        "context_compression": 4,
        "agent_routing_and_tools": 5,
    }
    total_cases = sum(counts.values())
    assert 18 <= total_cases <= 20

    runner = EvalRunner(mode="offline")
    for dataset in datasets:
        for case in dataset.cases:
            assert case.scenario in runner._dispatch, f"用例 {case.id} 的场景 {case.scenario} 未注册执行器"


def test_scorers_detect_dag_cycles_orphan_tools_and_fact_gaps():
    """校验评分器能准确识别循环依赖 DAG、孤立 tool 消息和摘要事实缺失。"""
    cyclic_steps = [
        SimpleNamespace(id="1", description="步骤A", depends_on=["2"]),
        SimpleNamespace(id="2", description="步骤B", depends_on=["1"]),
    ]
    dag_checks, _ = score_plan_dag(cyclic_steps, min_steps=2, require_parallel=False)
    by_name = {c.name: c for c in dag_checks}
    assert not by_name["plan_dag_acyclic_and_valid_refs"].passed

    orphan_messages = [
        {"role": "assistant", "content": "[SUMMARY] 旧摘要"},
        {"role": "tool", "tool_call_id": "missing_call", "content": "孤立结果"},
        {"role": "user", "content": "继续"},
    ]
    pair_checks = {c.name: c for c in score_tool_pair_integrity(orphan_messages)}
    assert not pair_checks["no_leading_orphan_tool"].passed
    assert not pair_checks["all_tool_results_paired"].passed

    ratio, missing = score_keyword_groups("已启用 FastAPI 与 pytest", [["FastAPI"], ["PostgreSQL"]])
    assert ratio == 0.5
    assert missing == ["PostgreSQL"]

    sum_checks, metrics = score_summary_retention(
        "[SUMMARY]\n【用户信息】研发\n【关键结论】使用 8765 端口",
        sections=["【用户信息】", "【已完成任务】", "【关键结论】", "【待处理事项】"],
        fact_groups=[["8765"], ["QRCLAW_API_TOKEN"]],
        min_section_coverage=0.75,
        min_fact_retention=0.85,
    )
    assert metrics["section_coverage"] == 0.5
    assert metrics["fact_retention_rate"] == 0.5
    assert any(not c.passed for c in sum_checks)


def test_full_offline_evaluation_suite_scores_100_percent(tmp_path):
    """执行全套 20 个离线回归评测场景，断言综合得分 = 100% 且正确落盘 JSON 报告。"""
    report_file = tmp_path / "offline_report.json"
    report = run_evaluations(mode="offline", output_path=report_file, quiet=True)

    failed_details = [
        (c.id, c.error, [(chk.name, chk.detail) for chk in c.checks if not chk.passed])
        for c in report.cases
        if not c.passed
    ]
    assert not failed_details, f"离线评测存在未通过用例: {failed_details}"
    assert report.total_cases == 20
    assert report.passed_cases == 20
    assert report.pass_rate == 1.0
    assert report.weighted_score == 1.0
    assert report_file.exists()

    persisted = EvalReport.model_validate_json(report_file.read_text(encoding="utf-8"))
    assert persisted.weighted_score == 1.0
    assert len(persisted.categories) == 4


def test_eval_cli_filtering_and_fail_under_gate(tmp_path):
    """校验 CLI 的分类/单条筛选及 --fail-under 门禁退出码语义。"""
    out_json = tmp_path / "filtered.json"
    rc_ok = evals_cli_main([
        "--mode", "offline",
        "--category", "context_compression",
        "--case", "ctx_comp_03_summary_fact_retention",
        "--output", str(out_json),
        "--fail-under", "1.0",
        "--quiet",
    ])
    assert rc_ok == 0
    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["total_cases"] == 1
    assert payload["cases"][0]["id"] == "ctx_comp_03_summary_fact_retention"

    # 模拟低分报告触发 CI 门禁非零退出码
    low_report = EvalReport.model_validate({**payload, "weighted_score": 0.80})
    with patch("evals.runner.run_evaluations", return_value=low_report):
        rc_fail = evals_cli_main(["--mode", "offline", "--fail-under", "0.95", "--quiet"])
    assert rc_fail == 1

    assert evals_cli_main(["--mode", "offline", "--category", "nonexistent", "--quiet"]) == 2
    assert evals_cli_main(["--mode", "offline", "--fail-under", "1.5", "--quiet"]) == 2


def test_github_actions_ci_workflow_is_valid():
    """校验 .github/workflows/ci.yml 语法合法且包含三大并行任务与离线评测门禁。"""
    ci_path = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.exists()
    workflow = yaml.safe_load(ci_path.read_text(encoding="utf-8"))

    assert "concurrency" in workflow
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert set(workflow["jobs"].keys()) == {"backend", "web", "website"}

    backend_runs = "\n".join(
        step.get("run", "") for step in workflow["jobs"]["backend"]["steps"]
    )
    assert "pytest -q" in backend_runs
    assert "python -m evals --mode offline --fail-under 0.95" in backend_runs
