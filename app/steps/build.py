from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.models import StepRunResult
from app.utils.logger import append_log
from app.utils.node import (
    corepack_executable,
    detect_package_manager,
    has_script,
    is_command_available,
    package_manager_executable,
    package_manager_prepare_target,
    run_script_command,
    wrap_with_corepack,
)
from app.utils.shell import run_command


def run_build(repo_dir: Path, log_file: Path, artifacts_dir: Path) -> StepRunResult:
    package_manager = detect_package_manager(repo_dir)
    use_corepack = _should_use_corepack_runner(repo_dir=repo_dir, package_manager=package_manager, log_file=log_file)
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
        cmd = run_script_command(package_manager, script_name)
        if use_corepack:
            cmd = wrap_with_corepack(cmd, package_manager)
        # Some toolchains (e.g., react-scripts) treat warnings as errors when CI=true.
        # Build should fail only on actual errors, not warnings.
        result = run_command(command=cmd, cwd=repo_dir, log_file=log_file, env={"CI": "false"})
        if result.exit_code != 0:
            return StepRunResult(
                status="failed",
                exit_code=result.exit_code,
                summary_message=f"{package_manager} run {script_name} failed",
            )

    collected = _collect_build_artifacts(repo_dir=repo_dir, artifacts_dir=artifacts_dir)
    if not collected:
        generated_names = _create_fallback_artifacts(
            repo_dir=repo_dir,
            artifacts_dir=artifacts_dir,
            build_scripts=build_scripts,
        )
        append_log(
            log_file,
            "Build completed without known output directories; generated deployable fallback artifacts",
        )
        collected.extend(generated_names)

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


def _create_fallback_artifacts(repo_dir: Path, artifacts_dir: Path, build_scripts: list[str]) -> list[str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    fallback_dir_name = "dist-server"
    fallback_dir_path = artifacts_dir / fallback_dir_name
    _create_node_fallback_directory(repo_dir=repo_dir, output_dir=fallback_dir_path)

    fallback_path = artifacts_dir / "build_meta.json"
    payload = {
        "repo": str(repo_dir),
        "build_scripts": build_scripts,
        "note": "No known build output directory detected; generated deployable fallback directory.",
        "fallback_directory": fallback_dir_name,
    }
    fallback_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return [fallback_dir_name, fallback_path.name]


def _should_use_corepack_runner(repo_dir: Path, package_manager: str, log_file: Path) -> bool:
    executable = package_manager_executable(package_manager)
    if is_command_available(executable):
        return False

    if package_manager not in {"yarn", "pnpm"}:
        return False

    corepack = corepack_executable()
    if not is_command_available(corepack):
        return False

    prepare_cmd = [corepack, "prepare", package_manager_prepare_target(repo_dir, package_manager), "--activate"]
    run_command(command=prepare_cmd, cwd=repo_dir, log_file=log_file)
    return True


def _create_node_fallback_directory(repo_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    excluded_dirs = {
        ".git",
        "node_modules",
        ".next",
        "dist",
        "build",
        "coverage",
        ".cache",
    }
    excluded_files = {
        ".DS_Store",
    }

    for path in sorted(repo_dir.rglob("*")):
        rel = path.relative_to(repo_dir)

        if any(part in excluded_dirs for part in rel.parts):
            continue

        if path.name in excluded_files:
            continue

        destination = output_dir / rel
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
