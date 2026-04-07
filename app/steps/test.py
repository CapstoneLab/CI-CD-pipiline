from __future__ import annotations

from pathlib import Path

from app.models import StepRunResult
from app.utils.logger import append_log
from app.utils.node import (
    corepack_executable,
    detect_package_manager,
    get_script,
    has_script,
    has_test_files,
    is_command_available,
    is_placeholder_test_script,
    package_manager_executable,
    package_manager_prepare_target,
    test_command,
    wrap_with_corepack,
)
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

    package_manager = detect_package_manager(repo_dir)
    cmd = test_command(package_manager)
    cmd = _resolve_runner_command(cmd=cmd, package_manager=package_manager, repo_dir=repo_dir, log_file=log_file)
    if not cmd:
        return StepRunResult(
            status="failed",
            exit_code=127,
            summary_message=f"{package_manager} executable not available",
        )

    result = run_command(command=cmd, cwd=repo_dir, log_file=log_file, env={"CI": "true"})

    if result.exit_code == 0:
        return StepRunResult(status="success", exit_code=0, summary_message=f"{package_manager} test passed")

    return StepRunResult(status="failed", exit_code=result.exit_code, summary_message=f"{package_manager} test failed")


def _resolve_runner_command(cmd: list[str], package_manager: str, repo_dir: Path, log_file: Path) -> list[str] | None:
    executable = package_manager_executable(package_manager)
    if is_command_available(executable):
        return cmd

    if package_manager not in {"yarn", "pnpm"}:
        return None

    corepack = corepack_executable()
    if not is_command_available(corepack):
        return None

    prepare_cmd = [corepack, "prepare", package_manager_prepare_target(repo_dir, package_manager), "--activate"]
    run_command(command=prepare_cmd, cwd=repo_dir, log_file=log_file)
    return wrap_with_corepack(cmd, package_manager)
