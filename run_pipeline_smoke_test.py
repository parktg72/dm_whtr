#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the DM_WHTR synthetic smoke pipeline as one reproducible command.

The command intentionally runs unittest discovery before the final
validate_cohort_output.py gate because some tests regenerate cohort CSVs. The
QA gate therefore checks the exact files consumed by downstream Cox analysis.
If the final cohort QA gate fails, execution stops and analysis is not run.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class SmokeStep:
    label: str
    command: list[str]


def build_command_plan(python_executable: str | None = None, skip_analysis: bool = False) -> list[SmokeStep]:
    """Return the ordered synthetic smoke-test command plan."""
    py = python_executable or sys.executable
    steps = [
        SmokeStep("Generate synthetic SQLite database", [py, "generate_synthetic_db.py"]),
        SmokeStep("Build analytical cohort", [py, "build_cohort.py"]),
        SmokeStep("Run full unittest discovery", [py, "-m", "unittest", "discover", "-v"]),
        SmokeStep(
            "Validate final cohort QA gate",
            [
                py,
                "validate_cohort_output.py",
                "--cohort",
                "data/cohort_analytical.csv",
                "--lag1y",
                "data/cohort_analytical_lag1y.csv",
            ],
        ),
    ]
    if not skip_analysis:
        steps.append(SmokeStep("Run trajectory and Cox analysis", [py, "analyze_trajectories.py"]))
    return steps


def run_steps(steps: Sequence[SmokeStep], cwd: Path) -> None:
    """Run smoke steps sequentially, stopping on the first failure."""
    for index, step in enumerate(steps, start=1):
        print("\n" + "=" * 80)
        print(f"[{index}/{len(steps)}] {step.label}")
        print("$ " + " ".join(step.command))
        print("=" * 80)
        subprocess.run(step.command, cwd=str(cwd), check=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the DM_WHTR synthetic DB -> cohort -> tests -> final QA -> analysis smoke pipeline."
    )
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Stop after DB/cohort generation, full unittest discovery, and final cohort QA.",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable to use for each pipeline step. Defaults to the current interpreter.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    project_dir = Path(__file__).resolve().parent
    steps = build_command_plan(python_executable=args.python, skip_analysis=args.skip_analysis)
    try:
        run_steps(steps, project_dir)
    except subprocess.CalledProcessError as exc:
        print(f"\n[FAIL] Step failed with exit code {exc.returncode}: {' '.join(exc.cmd)}", file=sys.stderr)
        return exc.returncode or 1
    print("\n[OK] DM_WHTR synthetic smoke pipeline completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
