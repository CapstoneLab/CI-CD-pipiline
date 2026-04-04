from __future__ import annotations

from pathlib import Path

from app.models import StepRunResult
from app.utils.logger import append_log
from app.utils.node import has_script, npm_executable
from app.utils.shell import run_command


def run_build(repo_dir: Path, log_file: Path) -> StepRunResult:
    npm_cmd = npm_executable()
    build_scripts: list[str] = []

    if has_script(repo_dir, "build"):
        build_scripts = ["build"]
    else:
        if has_script(repo_dir, "build:frontend"):
            build_scripts.append("build:frontend")
        if has_script(repo_dir, "build:server"):
            build_scripts.append("build:server")

    if not build_scripts:
        append_log(log_file, "No supported build scripts found: build, build:frontend, build:server")
        return StepRunResult(status="failed", exit_code=1, summary_message="build script missing in package.json")

    for script_name in build_scripts:
        cmd = [npm_cmd, "run", script_name]
        result = run_command(command=cmd, cwd=repo_dir, log_file=log_file, env={"CI": "true"})
        if result.exit_code != 0:
            return StepRunResult(
                status="failed",
                exit_code=result.exit_code,
                summary_message=f"npm run {script_name} failed",
            )

    return StepRunResult(
        status="success",
        exit_code=0,
        summary_message="build scripts succeeded: " + ", ".join(build_scripts),
    )
