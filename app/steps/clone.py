from __future__ import annotations

import shutil
from pathlib import Path

from app.models import StepRunResult
from app.utils.shell import run_command


def run_clone(repo_url: str, branch: str | None, repo_dir: Path, log_file: Path) -> StepRunResult:
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    branch_candidates = _build_branch_candidates(branch)
    result = None
    used_branch = "default"

    for idx, candidate_branch in enumerate(branch_candidates):
        current_result = _run_clone_command(
            repo_url=repo_url,
            branch=candidate_branch,
            repo_dir=repo_dir,
            log_file=log_file,
        )
        result = current_result

        if current_result.exit_code == 0:
            used_branch = candidate_branch if candidate_branch else "default"
            break

        is_last_try = idx == len(branch_candidates) - 1
        if not _is_missing_branch_error(current_result.output) or is_last_try:
            break

    assert result is not None

    if result.exit_code == 0:
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=f"Repository cloned and branch {used_branch} checked out",
        )

    return StepRunResult(
        status="failed",
        exit_code=result.exit_code,
        summary_message="git clone failed. Check clone.log for details",
    )


def _run_clone_command(repo_url: str, branch: str | None, repo_dir: Path, log_file: Path):
    cmd = ["git", "clone", "--depth", "1"]
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([repo_url, str(repo_dir)])
    return run_command(command=cmd, cwd=repo_dir.parent, log_file=log_file)


def _is_missing_branch_error(output: str) -> bool:
    lowered = output.lower()
    return "could not find remote branch" in lowered or "remote branch" in lowered and "not found" in lowered


def _build_branch_candidates(branch: str | None) -> list[str | None]:
    if not branch:
        return [None]

    normalized = branch.strip()
    candidates: list[str | None] = [normalized]

    if normalized == "main":
        candidates.append("master")
    elif normalized == "master":
        candidates.append("main")

    candidates.append(None)
    return candidates
