from __future__ import annotations

import shutil
from pathlib import Path

from app.models import StepRunResult
from app.utils.shell import run_command


def run_clone(repo_url: str, branch: str | None, repo_dir: Path, log_file: Path) -> StepRunResult:
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([repo_url, str(repo_dir)])

    result = run_command(command=cmd, cwd=repo_dir.parent, log_file=log_file)

    if result.exit_code == 0:
        branch_message = branch if branch else "default"
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=f"Repository cloned and branch {branch_message} checked out",
        )

    return StepRunResult(
        status="failed",
        exit_code=result.exit_code,
        summary_message="git clone failed. Check clone.log for details",
    )
