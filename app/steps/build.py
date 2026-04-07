from __future__ import annotations

import shutil
from pathlib import Path

from app.models import StepRunResult
from app.utils.logger import append_log
from app.utils.node import has_script, npm_executable
from app.utils.shell import run_command


def run_build(repo_dir: Path, log_file: Path, artifacts_dir: Path) -> StepRunResult:
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

    collected = _collect_build_artifacts(repo_dir=repo_dir, artifacts_dir=artifacts_dir)
    if not collected:
        append_log(
            log_file,
            "Build completed but no deployable artifacts were found in known output locations",
        )
        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message="Build succeeded but no artifacts were found",
        )

    return StepRunResult(
        status="success",
        exit_code=0,
        summary_message=(
            "build scripts succeeded: "
            + ", ".join(build_scripts)
            + f" | artifacts saved: {', '.join(collected)}"
        ),
    )


def _collect_build_artifacts(repo_dir: Path, artifacts_dir: Path) -> list[str]:
    candidates = ["dist", "build", "out", ".next", ".output", "release", "public/build"]
    collected: list[str] = []

    for relative in candidates:
        source = repo_dir / relative
        if not source.exists():
            continue

        destination = artifacts_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)

        if source.is_dir():
            if not any(source.iterdir()):
                continue
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(source, destination)
            collected.append(relative)
            continue

        shutil.copy2(source, destination)
        collected.append(relative)

    return collected
