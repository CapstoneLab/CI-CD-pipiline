from __future__ import annotations

from pathlib import Path

from app.models import StepRunResult
from app.utils.logger import append_log
from app.utils.node import get_script, has_script, has_test_files, is_placeholder_test_script, npm_executable
from app.utils.shell import run_command


def run_test(repo_dir: Path, log_file: Path) -> StepRunResult:
    test_script = get_script(repo_dir, "test")
    has_tests = has_test_files(repo_dir)
    has_test_script = has_script(repo_dir, "test") and not is_placeholder_test_script(test_script)

    if not has_tests:
        append_log(log_file, "No test files found; test step skipped")
        append_log(log_file, "[exit_code] 0")
        return StepRunResult(status="skipped", exit_code=0, summary_message="No tests found")

    if not has_test_script:
        append_log(log_file, "Test files detected but package.json test script is missing or placeholder; skipped")
        append_log(log_file, "[exit_code] 0")
        return StepRunResult(
            status="skipped",
            exit_code=0,
            summary_message="Test files detected but package.json test script is missing, skipped",
        )

    cmd = [npm_executable(), "test"]
    result = run_command(command=cmd, cwd=repo_dir, log_file=log_file, env={"CI": "true"})

    if result.exit_code == 0:
        return StepRunResult(status="success", exit_code=0, summary_message="npm test passed")

    return StepRunResult(status="failed", exit_code=result.exit_code, summary_message="npm test failed")
