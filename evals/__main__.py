"""命令行入口：支持 `uv run python -m evals` 运行离线回归门禁或真实大模型对比评测。"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile


def _parse_csv_set(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    items = {part.strip() for raw in values for part in raw.split(",") if part.strip()}
    return items or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="QRClaw 记忆与 Agent 效果回归评测集（支持 offline 门禁与 live 真实模型跑分）",
    )
    parser.add_argument(
        "--mode",
        choices=("offline", "live"),
        default="offline",
        help="运行模式：offline 使用高保真脚本化模型（默认，无需 API Key）；live 调用当前配置的真实大模型",
    )
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="仅运行指定评测维度（可多次传入或逗号分隔，如 memory_extraction）",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="仅运行指定用例 ID（可多次传入或逗号分隔，如 mem_ext_01_coding_and_lang_pref）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="将结构化评测报告保存为 JSON 文件（例如 evals/reports/latest.json）",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="最低综合加权得分门禁（0.0~1.0，例如 0.95 或 1.0；低于该分数时返回非 0 退出码）",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式，不打印终端表格（适合纯脚本解析 JSON 报告）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.fail_under is not None and not (0.0 <= args.fail_under <= 1.0):
        print("错误：--fail-under 阈值必须在 0.0 到 1.0 之间（如 0.95 或 1.0）。", file=sys.stderr)
        return 2

    boot_temp_home: str | None = None
    if args.mode == "offline" and "QRCLAW_HOME" not in os.environ:
        tmp_base = "/tmp" if os.path.isdir("/tmp") else None
        boot_temp_home = tempfile.mkdtemp(prefix="qrclaw-eval-boot-", dir=tmp_base)
        os.environ["QRCLAW_HOME"] = boot_temp_home

    try:
        from evals.runner import DATASET_ORDER, run_evaluations

        categories = _parse_csv_set(args.categories)
        if categories:
            unknown = categories - set(DATASET_ORDER)
            if unknown:
                print(
                    f"错误：未知的评测维度 {sorted(unknown)}，可选值：{list(DATASET_ORDER)}",
                    file=sys.stderr,
                )
                return 2

        case_ids = _parse_csv_set(args.cases)
        report = run_evaluations(
            mode=args.mode,
            categories=categories,
            case_ids=case_ids,
            output_path=args.output,
            quiet=args.quiet,
        )

        if report.total_cases == 0:
            print("错误：筛选条件未匹配到任何评测用例。", file=sys.stderr)
            return 2

        if args.fail_under is not None and report.weighted_score + 1e-9 < args.fail_under:
            print(
                f"评测未达门禁阈值：综合加权得分 {report.weighted_score:.4f} < 要求阈值 {args.fail_under:.4f}",
                file=sys.stderr,
            )
            return 1
        return 0
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    finally:
        if boot_temp_home:
            shutil.rmtree(boot_temp_home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
