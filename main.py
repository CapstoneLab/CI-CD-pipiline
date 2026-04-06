from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.callback import (
    build_callback_payload,
    collect_logs,
    post_callback_with_retry,
    save_callback_delivery_result,
    save_callback_payload,
)
from app.orchestrator import LocalOrchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local CI engine MVP")
    parser.add_argument("--job-id", default="", help="External job id from caller")
    parser.add_argument("--repo", required=True, help="Git repository URL")
    parser.add_argument("--branch", default="main", help="Branch name (default: main)")
    parser.add_argument(
        "--workflow",
        default="",
        help=(
            "Path to workflow YAML. Relative paths are resolved against cloned repository first, "
            "then engine root"
        ),
    )
    parser.add_argument("--callback-url", default="", help="Windows callback API URL")
    parser.add_argument("--callback-token", default="", help="Shared callback auth token")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_dir = Path(__file__).resolve().parent
    branch = args.branch or "main"

    orchestrator = LocalOrchestrator(base_dir=base_dir)
    pipeline_run, run_dir = orchestrator.run(
        repo_url=args.repo,
        branch=branch,
        workflow_path=args.workflow or None,
    )

    callback_url = args.callback_url.strip()
    callback_token = args.callback_token.strip()
    if callback_url:
        job_id = args.job_id.strip() or pipeline_run.run_id
        logs = collect_logs(run_dir)
        payload = build_callback_payload(
            job_id=job_id,
            repo_url=args.repo,
            branch=branch,
            pipeline_run=pipeline_run,
            logs=logs,
        )

        callback_result_path = save_callback_payload(run_dir, payload)

        if callback_token:
            delivered, detail = post_callback_with_retry(
                callback_url=callback_url,
                callback_token=callback_token,
                payload=payload,
            )
            save_callback_delivery_result(
                run_dir,
                {
                    "delivered": delivered,
                    "callback_url": callback_url,
                    **detail,
                },
            )

            if delivered:
                print(f"callback delivered to {callback_url}")
            else:
                print("callback delivery failed after retries")
                print(f"local callback payload: {callback_result_path}")
        else:
            save_callback_delivery_result(
                run_dir,
                {
                    "delivered": False,
                    "callback_url": callback_url,
                    "attempts": 0,
                    "error": "missing callback token",
                    "http_status": None,
                },
            )
            print("callback skipped: callback token is missing")
            print(f"local callback payload: {callback_result_path}")

    print("\n=== Pipeline Result ===")
    print(f"run_id: {pipeline_run.run_id}")
    print(f"status: {pipeline_run.status}")
    print(f"result file: {run_dir / 'pipeline_result.json'}")

    for step in pipeline_run.steps:
        print(f"- {step.step_name}: {step.status} ({step.summary_message or 'no message'})")

    return 0 if pipeline_run.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
