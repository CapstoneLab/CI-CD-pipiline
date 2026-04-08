from __future__ import annotations

from pathlib import Path

from app.models import StepRunResult
from app.scanners.semgrep_parser import parse_semgrep_report
from app.utils.executable import resolve_executable
from app.utils.shell import run_command


CRITICAL_CVSS_THRESHOLD = 9.0


def run_deep_security_scan(repo_dir: Path, log_file: Path, report_file: Path) -> StepRunResult:
    semgrep_executable = resolve_executable("semgrep")
    if not semgrep_executable:
        return StepRunResult(
            status="failed",
            exit_code=127,
            summary_message=(
                "semgrep not found. Install semgrep and ensure it is available in PATH"
            ),
        )

    cmd = [semgrep_executable, "--config", "auto", "--json", "--output", str(report_file), "."]
    result = run_command(
        command=cmd,
        cwd=repo_dir,
        log_file=log_file,
        env={
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        },
    )

    if result.exit_code not in (0, 1):
        if result.exit_code == 127:
            return StepRunResult(
                status="failed",
                exit_code=result.exit_code,
                summary_message=(
                    "semgrep not found. Install semgrep and ensure it is available in PATH"
                ),
            )

        if "UnicodeEncodeError" in result.output:
            return StepRunResult(
                status="failed",
                exit_code=result.exit_code,
                summary_message="semgrep failed due to Windows encoding issue (see deep_security_scan.log)",
            )

        return StepRunResult(
            status="failed",
            exit_code=result.exit_code,
            summary_message="semgrep execution failed",
        )

    summary, findings = parse_semgrep_report(report_file)

    _log_semgrep_findings(log_file, findings, summary)

    max_cvss = summary.max_cvss_score
    if max_cvss is not None and max_cvss >= CRITICAL_CVSS_THRESHOLD:
        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message=(
                "semgrep policy failed: "
                f"max_cvss={max_cvss:.1f} (threshold={CRITICAL_CVSS_THRESHOLD:.1f})"
            ),
            security_summary=summary,
            security_findings=findings,
        )

    cvss_text = f"max_cvss={max_cvss:.1f}" if max_cvss is not None else "max_cvss=unavailable"
    return StepRunResult(
        status="success",
        exit_code=0,
        summary_message=(
            "semgrep passed policy (critical CVSS only): "
            f"critical={summary.critical_count}, high={summary.high_count}, "
            f"medium={summary.medium_count}, low={summary.low_count}, {cvss_text}"
        ),
        security_summary=summary,
        security_findings=findings,
    )


def _log_semgrep_findings(log_file: Path, findings: list, summary) -> None:
    from app.utils.logger import append_log

    total = len(findings)
    append_log(
        log_file,
        f"[semgrep] {total} finding(s): "
        f"critical={summary.critical_count}, high={summary.high_count}, "
        f"medium={summary.medium_count}, low={summary.low_count}",
    )

    if not findings:
        return

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_findings = sorted(findings, key=lambda f: severity_order.get(f.severity, 4))

    for i, f in enumerate(sorted_findings, 1):
        cvss_str = f" | cvss={f.cvss_score:.1f}" if f.cvss_score is not None else ""
        append_log(
            log_file,
            f"  [{i}] [{f.severity.upper()}]{cvss_str} {f.rule_id} | "
            f"{f.file_path}:{f.line_number} | {f.message}",
        )
