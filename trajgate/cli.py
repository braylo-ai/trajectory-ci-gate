"""trajgate CLI.

  trajgate check --golden golden/ --runs runs/ [--report report.md] [--json out.json] [--min-score 1.0]

Pairs every run file (<task_id>.json) with golden/<task_id>.yaml, grades it,
writes a Markdown report (for a PR comment / job summary) and exits 1 on any
failure, so CI blocks the merge.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .grader import grade
from .model import GoldenDAG, Trajectory


def check(golden_dir: Path, runs_dir: Path):
    reports, problems = [], []
    goldens = {p.stem: p for p in sorted(golden_dir.glob("*.y*ml"))}
    runs = {p.stem: p for p in sorted(runs_dir.glob("*.json"))}
    for task_id, gpath in goldens.items():
        if task_id not in runs:
            problems.append(f"no trajectory recorded for golden task `{task_id}`")
            continue
        reports.append(grade(Trajectory.load(runs[task_id]), GoldenDAG.load(gpath)))
    for task_id in runs:
        if task_id not in goldens:
            problems.append(f"trajectory `{task_id}` has no golden DAG (ungated)")
    return reports, problems


def render_markdown(reports, problems) -> str:
    n_pass = sum(r.passed for r in reports)
    status = "PASS" if n_pass == len(reports) and not problems else "FAIL"
    lines = [
        f"## Trajectory gate: {status} ({n_pass}/{len(reports)} tasks)",
        "",
        "| Task | Result | Score | Deviations |",
        "|---|---|---|---|",
    ]
    for r in reports:
        dev = "<br>".join(f"`{d.kind}` {d.message}" for d in r.deviations) or "-"
        lines.append(
            f"| {r.task_id} | {'pass' if r.passed else 'FAIL'} | {r.score:.2f} | {dev} |"
        )
    if problems:
        lines += ["", "**Suite problems:**"] + [f"- {p}" for p in problems]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="trajgate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--golden", required=True, type=Path)
    c.add_argument("--runs", required=True, type=Path)
    c.add_argument("--report", type=Path)
    c.add_argument("--json", type=Path)
    c.add_argument(
        "--min-score",
        type=float,
        default=1.0,
        help="fail if any task's score is below this (default: every deviation fails)",
    )
    args = ap.parse_args(argv)
    reports, problems = check(args.golden, args.runs)
    md = render_markdown(reports, problems)
    print(md)
    if args.report:
        args.report.write_text(md)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(md)
    if args.json:
        args.json.write_text(
            json.dumps(
                {"problems": problems, "reports": [r.to_dict() for r in reports]},
                indent=2,
            )
        )
    failed = bool(problems) or any(not r.passed and r.score < args.min_score for r in reports)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
