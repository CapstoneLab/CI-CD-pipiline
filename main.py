from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.orchestrator import LocalOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local CI engine MVP")
    parser.add_argument("--repo", required=True, help="Git repository URL")
    parser.add_argument("--branch", default="", help="Branch name (optional)")
    parser.add_argument(
        "--workflow",
        default="",
        help=(
            "Path to workflow YAML. Relative paths are resolved against cloned repository first, "
            "then engine root"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent

    orchestrator = LocalOrchestrator(base_dir=base_dir)
    pipeline_run, run_dir = orchestrator.run(
        repo_url=args.repo,
        branch=args.branch or None,
        workflow_path=args.workflow or None,
    )

    print("\n=== Pipeline Result ===")
    print(f"run_id: {pipeline_run.run_id}")
    print(f"status: {pipeline_run.status}")
    print(f"result file: {run_dir / 'pipeline_result.json'}")

    for step in pipeline_run.steps:
        print(f"- {step.step_name}: {step.status} ({step.summary_message or 'no message'})")

    return 0 if pipeline_run.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
