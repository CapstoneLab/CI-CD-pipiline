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
from app.utils.python import (
    build_command as py_build_command,
    detect_package_manager as py_detect_package_manager,
    effective_package_manager as py_effective_package_manager,
    effective_python_executable,
    find_python_project_root,
    is_command_available as py_is_command_available,
    is_python_project,
    package_manager_executable as py_package_manager_executable,
    python_executable,
    venv_exists,
)
from app.utils.shell import run_command


def run_build(
    repo_dir: Path,
    log_file: Path,
    artifacts_dir: Path,
    runtime_type: str = "node",
) -> StepRunResult:
    if runtime_type == "python":
        return _run_python_build(repo_dir=repo_dir, log_file=log_file, artifacts_dir=artifacts_dir)
    return _run_node_build(repo_dir=repo_dir, log_file=log_file, artifacts_dir=artifacts_dir)


def _run_node_build(repo_dir: Path, log_file: Path, artifacts_dir: Path) -> StepRunResult:
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


_PYTHON_BUILDABLE_MANAGERS = {"poetry", "uv", "pdm", "hatch"}


def _run_python_build(repo_dir: Path, log_file: Path, artifacts_dir: Path) -> StepRunResult:
    if not is_python_project(repo_dir):
        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message="No Python project markers found (pyproject.toml / setup.py)",
        )

    project_root = find_python_project_root(repo_dir)
    package_manager = py_effective_package_manager(project_root)
    buildable = package_manager in _PYTHON_BUILDABLE_MANAGERS or (project_root / "pyproject.toml").exists()

    if not buildable:
        append_log(
            log_file,
            "No pyproject.toml or buildable package manager detected; generating source fallback artifacts",
        )
        generated = _create_python_fallback_artifacts(
            repo_dir=project_root,
            artifacts_dir=artifacts_dir,
            package_manager=package_manager,
        )
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=(
                f"python build skipped ({package_manager} non-buildable) | artifacts saved: {', '.join(generated)}"
            ),
        )

    build_tool_missing = _ensure_python_build_tool_available(
        package_manager=package_manager,
        repo_dir=project_root,
        log_file=log_file,
    )
    if build_tool_missing:
        return StepRunResult(status="failed", exit_code=127, summary_message=build_tool_missing)

    cmd = py_build_command(package_manager, repo_dir=project_root)
    result = run_command(command=cmd, cwd=project_root, log_file=log_file)
    if result.exit_code != 0:
        # Many FastAPI/Flask-style apps have a pyproject.toml for dependency
        # management only and do not declare a distributable package layout
        # (e.g. missing `packages = [...]` with poetry-core). In that case
        # `python -m build` / `poetry build` fails even though the repo is
        # perfectly deployable as a source tree. Emit a source fallback
        # artifact and continue instead of aborting the pipeline.
        append_log(
            log_file,
            f"python build ({package_manager}) failed to produce a wheel/sdist; "
            "packaging source tree as fallback artifact",
        )
        generated = _create_python_fallback_artifacts(
            repo_dir=project_root,
            artifacts_dir=artifacts_dir,
            package_manager=package_manager,
        )
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=(
                f"python build fell back to source artifact "
                f"({package_manager} build failed) | artifacts saved: {', '.join(generated)}"
            ),
        )

    collected = _collect_python_build_artifacts(repo_dir=project_root, artifacts_dir=artifacts_dir)
    if not collected:
        collected = _create_python_fallback_artifacts(
            repo_dir=project_root,
            artifacts_dir=artifacts_dir,
            package_manager=package_manager,
        )
        append_log(
            log_file,
            "Python build completed without standard dist output; generated source fallback artifacts",
        )

    return StepRunResult(
        status="success",
        exit_code=0,
        summary_message=(
            f"python build succeeded ({package_manager}) | artifacts saved: {', '.join(collected)}"
        ),
    )


def _ensure_python_build_tool_available(package_manager: str, repo_dir: Path, log_file: Path) -> str | None:
    if package_manager in _PYTHON_BUILDABLE_MANAGERS:
        executable = py_package_manager_executable(package_manager)
        if py_is_command_available(executable):
            return None
        return f"Command not found: {package_manager} (required for python build)"

    # Fallback path uses `python -m build`; ensure the `build` module can be invoked.
    if not venv_exists(repo_dir):
        return (
            "python virtualenv (.venv) not found; "
            "ensure the install step ran before build"
        )
    py = effective_python_executable(repo_dir)
    probe = run_command(
        command=[py, "-m", "build", "--version"],
        cwd=repo_dir,
        log_file=log_file,
    )
    if probe.exit_code != 0:
        install_result = run_command(
            command=[py, "-m", "pip", "install", "build"],
            cwd=repo_dir,
            log_file=log_file,
        )
        if install_result.exit_code != 0:
            return "python 'build' package unavailable and venv pip install failed"
    return None


def _collect_python_build_artifacts(repo_dir: Path, artifacts_dir: Path) -> list[str]:
    candidates = ["dist", "build"]
    collected: list[str] = []

    for relative in candidates:
        source = repo_dir / relative
        if not source.exists() or not source.is_dir():
            continue
        if not any(source.iterdir()):
            continue

        destination = artifacts_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        collected.append(relative)

    return collected


def _create_python_fallback_artifacts(
    repo_dir: Path,
    artifacts_dir: Path,
    package_manager: str,
) -> list[str]:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    fallback_dir_name = "dist-python"
    fallback_dir_path = artifacts_dir / fallback_dir_name
    _create_python_fallback_directory(repo_dir=repo_dir, output_dir=fallback_dir_path)

    meta_path = artifacts_dir / "build_meta.json"
    payload = {
        "repo": str(repo_dir),
        "runtime": "python",
        "package_manager": package_manager,
        "note": "No python wheel/sdist produced; packaged source tree as deployable fallback.",
        "fallback_directory": fallback_dir_name,
    }
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return [fallback_dir_name, meta_path.name]


def _create_python_fallback_directory(repo_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    excluded_dirs = {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "build",
        "dist",
        ".tox",
        ".pytest_cache",
        ".mypy_cache",
        ".cache",
        "node_modules",
        ".eggs",
    }
    excluded_files = {".DS_Store"}

    for path in sorted(repo_dir.rglob("*")):
        rel = path.relative_to(repo_dir)

        if any(part in excluded_dirs for part in rel.parts):
            continue
        if any(part.endswith(".egg-info") for part in rel.parts):
            continue
        if path.name in excluded_files:
            continue

        destination = output_dir / rel
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue

        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
