from __future__ import annotations

from pathlib import Path

from app.models import StepRunResult
from app.scanners.gitleaks_parser import parse_gitleaks_report
from app.utils.executable import resolve_executable
from app.utils.shell import run_command


def run_lightweight_security_scan(repo_dir: Path, log_file: Path, report_file: Path) -> StepRunResult:
    gitleaks_executable = resolve_executable("gitleaks")
    if not gitleaks_executable:
        return StepRunResult(
            status="failed",
            exit_code=127,
            summary_message=(
                "gitleaks not found. Install gitleaks and ensure it is available in PATH"
            ),
        )

    cmd = [
        gitleaks_executable,
        "detect",
        "--source",
        ".",
        "--report-format",
        "json",
        "--report-path",
        str(report_file),
    ]
    result = run_command(command=cmd, cwd=repo_dir, log_file=log_file)

    if result.exit_code not in (0, 1):
        if result.exit_code == 127:
            return StepRunResult(
                status="failed",
                exit_code=result.exit_code,
                summary_message=(
                    "gitleaks not found. Install gitleaks and ensure it is available in PATH"
                ),
            )

        return StepRunResult(
            status="failed",
            exit_code=result.exit_code,
            summary_message="gitleaks execution failed",
        )

    summary, findings = parse_gitleaks_report(report_file)
    found_count = len(findings)

    if found_count > 0:
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=(
                f"gitleaks found {found_count} potential secret(s) (non-blocking policy)"
            ),
            security_summary=summary,
            security_findings=findings,
        )

    return StepRunResult(
        status="success",
        exit_code=0,
        summary_message="gitleaks passed with 0 findings",
        security_summary=summary,
        security_findings=findings,
    )
