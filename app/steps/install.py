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
    cmd = [npm_cmd, "ci", "--no-audit", "--no-fund"] if lock_file.exists() else [npm_cmd, "install", "--no-audit", "--no-fund"]
    result = run_command(command=cmd, cwd=repo_dir, log_file=log_file)

    if result.exit_code == 0:
        install_mode = "npm ci" if lock_file.exists() else "npm install"
        return StepRunResult(status="success", exit_code=0, summary_message=f"dependencies installed ({install_mode})")

    return StepRunResult(
        status="failed",
        exit_code=result.exit_code,
        summary_message="dependency install failed",
    )
