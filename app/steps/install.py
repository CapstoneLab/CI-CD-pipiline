from __future__ import annotations

from pathlib import Path

from app.models import StepRunResult
from app.utils.node import npm_executable, read_package_json
from app.utils.shell import run_command


def _detect_node_project(repo_dir: Path) -> tuple[bool, str]:
    package_data = read_package_json(repo_dir)
    if not package_data:
        return False, "No valid package.json found (Node project required)"
    return True, "package.json"


def run_install(repo_dir: Path, log_file: Path) -> StepRunResult:
    is_supported, reason = _detect_node_project(repo_dir)
    if not is_supported:
        return StepRunResult(status="failed", exit_code=1, summary_message=reason)

    lock_file = repo_dir / "package-lock.json"
    npm_cmd = npm_executable()
    if lock_file.exists():
        ci_result = run_command(
            command=[npm_cmd, "ci", "--no-audit", "--no-fund"],
            cwd=repo_dir,
            log_file=log_file,
        )

        if ci_result.exit_code == 0:
            return StepRunResult(
                status="success",
                exit_code=0,
                summary_message="dependencies installed (npm ci)",
            )

        # Some repositories commit stale lock files; fallback keeps CI compatible.
        if _should_fallback_to_npm_install(ci_result.output):
            install_result = run_command(
                command=[npm_cmd, "install", "--no-audit", "--no-fund"],
                cwd=repo_dir,
                log_file=log_file,
            )
            if install_result.exit_code == 0:
                return StepRunResult(
                    status="success",
                    exit_code=0,
                    summary_message="dependencies installed (npm install fallback)",
                )
            return StepRunResult(
                status="failed",
                exit_code=install_result.exit_code,
                summary_message="dependency install failed (npm ci fallback to npm install also failed)",
            )

        return StepRunResult(
            status="failed",
            exit_code=ci_result.exit_code,
            summary_message="dependency install failed",
        )

    result = run_command(
        command=[npm_cmd, "install", "--no-audit", "--no-fund"],
        cwd=repo_dir,
        log_file=log_file,
    )

    if result.exit_code == 0:
        return StepRunResult(status="success", exit_code=0, summary_message="dependencies installed (npm install)")

    return StepRunResult(
        status="failed",
        exit_code=result.exit_code,
        summary_message="dependency install failed",
    )


def _should_fallback_to_npm_install(output: str) -> bool:
    lowered = output.lower()
    return "can only install packages when your package.json and package-lock.json" in lowered or "missing:" in lowered
