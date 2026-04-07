from __future__ import annotations

import argparse
import json
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
        logs = collect_logs(run_dir, pipeline_run=pipeline_run)
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
    result_file = run_dir / "pipeline_result.json"
    output_run_id = pipeline_run.run_id
    output_status = pipeline_run.status
    output_steps: list[dict[str, str]] = []

    try:
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            output_run_id = str(payload.get("run_id") or output_run_id)
            output_status = str(payload.get("status") or output_status)

        steps = payload.get("steps", [])
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                output_steps.append(
                    {
                        "step_name": str(step.get("step_name") or "unknown"),
                        "status": str(step.get("status") or "unknown"),
                        "summary": str(step.get("summary_message") or "no message"),
                    }
                )
    except Exception:
        output_steps = []

    print(f"run_id: {output_run_id}")
    print(f"status: {output_status}")
    print(f"result file: {result_file}")

    if output_steps:
        for step in output_steps:
            print(f"- {step['step_name']}: {step['status']} ({step['summary']})")
    else:
        for step in pipeline_run.steps:
            print(f"- {step.step_name}: {step.status} ({step.summary_message or 'no message'})")

    return 0 if pipeline_run.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
