from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models import StepRunResult
from app.utils.logger import append_log
from app.utils.node import (
    corepack_executable,
    detect_package_manager,
    has_lock_file,
    install_command,
    is_command_available,
    package_manager_executable,
    package_manager_prepare_target,
    read_package_json,
    strip_engine_managed_dependencies,
    wrap_with_corepack,
)
from app.utils.java import (
    build_tool_executable as java_build_tool_executable,
    detect_build_tool as java_detect_build_tool,
    ensure_wrapper_executable as java_ensure_wrapper_executable,
    find_java_project_root,
    has_wrapper as java_has_wrapper,
    install_command as java_install_command,
    install_command_fallbacks as java_install_command_fallbacks,
    is_command_available as java_is_command_available,
    is_java_project,
    setup_java_env,
)
from app.utils.python import (
    create_venv_command,
    detect_package_manager as py_detect_package_manager,
    effective_package_manager as py_effective_package_manager,
    find_python_project_root,
    has_lock_file as py_has_lock_file,
    install_command as py_install_command,
    is_command_available as py_is_command_available,
    is_python_project,
    package_manager_executable as py_package_manager_executable,
    strip_engine_managed_requirements,
    venv_exists,
    venv_python,
)
from app.utils.shell import run_command


@dataclass
class _PackageManagerResolution:
    command_transform: str
    error: str | None = None


def _detect_node_project(repo_dir: Path) -> tuple[bool, str]:
    package_data = read_package_json(repo_dir)
    if not package_data:
        return False, "No valid package.json found (Node project required)"
    return True, "package.json"


def run_install(repo_dir: Path, log_file: Path, runtime_type: str = "node") -> StepRunResult:
    if runtime_type == "python":
        return _run_python_install(repo_dir=repo_dir, log_file=log_file)
    if runtime_type == "java":
        return _run_java_install(repo_dir=repo_dir, log_file=log_file)
    return _run_node_install(repo_dir=repo_dir, log_file=log_file)


def _run_node_install(repo_dir: Path, log_file: Path) -> StepRunResult:
    is_supported, reason = _detect_node_project(repo_dir)
    if not is_supported:
        return StepRunResult(status="failed", exit_code=1, summary_message=reason)

    stripped_node_pkgs = strip_engine_managed_dependencies(repo_dir)
    if stripped_node_pkgs:
        append_log(
            log_file,
            "Removed engine-managed packages from package.json: "
            + ", ".join(stripped_node_pkgs),
        )

    package_manager = detect_package_manager(repo_dir)
    resolution = _resolve_package_manager_runner(repo_dir=repo_dir, package_manager=package_manager, log_file=log_file)
    if resolution.error:
        return StepRunResult(status="failed", exit_code=127, summary_message=resolution.error)

    # If we rewrote package.json the lockfile will no longer agree, so
    # frozen installs (`npm ci`, `yarn --frozen-lockfile`) would fail.
    # Fall back to a non-frozen install in that case.
    if has_lock_file(repo_dir, package_manager) and not stripped_node_pkgs:
        frozen_install_cmd = install_command(package_manager, frozen_lock=True)
        if resolution.command_transform == "corepack":
            frozen_install_cmd = wrap_with_corepack(frozen_install_cmd, package_manager)

        ci_result = run_command(
            command=frozen_install_cmd,
            cwd=repo_dir,
            log_file=log_file,
        )

        if ci_result.exit_code == 0:
            return StepRunResult(
                status="success",
                exit_code=0,
                summary_message=f"dependencies installed ({package_manager} lock install)",
            )

        install_cmd = install_command(package_manager, frozen_lock=False)
        if resolution.command_transform == "corepack":
            install_cmd = wrap_with_corepack(install_cmd, package_manager)

        install_result = run_command(
            command=install_cmd,
            cwd=repo_dir,
            log_file=log_file,
        )
        if install_result.exit_code == 0:
            return StepRunResult(
                status="success",
                exit_code=0,
                summary_message=f"dependencies installed ({package_manager} install fallback)",
            )

        return StepRunResult(
            status="failed",
            exit_code=install_result.exit_code,
            summary_message=(
                f"dependency install failed ({package_manager} lock install and fallback install both failed)"
            ),
        )

    install_cmd = install_command(package_manager, frozen_lock=False)
    if resolution.command_transform == "corepack":
        install_cmd = wrap_with_corepack(install_cmd, package_manager)

    result = run_command(
        command=install_cmd,
        cwd=repo_dir,
        log_file=log_file,
    )

    if result.exit_code == 0:
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=f"dependencies installed ({package_manager} install)",
        )

    return StepRunResult(
        status="failed",
        exit_code=result.exit_code,
        summary_message="dependency install failed",
    )


def _resolve_package_manager_runner(repo_dir: Path, package_manager: str, log_file: Path) -> _PackageManagerResolution:
    executable = package_manager_executable(package_manager)
    if is_command_available(executable):
        return _PackageManagerResolution(command_transform="direct")

    if package_manager not in {"yarn", "pnpm"}:
        return _PackageManagerResolution(
            command_transform="direct",
            error=f"Command not found: {package_manager}",
        )

    corepack = corepack_executable()
    if not is_command_available(corepack):
        return _PackageManagerResolution(
            command_transform="direct",
            error=(
                f"Command not found: {package_manager} (corepack unavailable for bootstrap)"
            ),
        )

    prepare_cmd = [corepack, "prepare", package_manager_prepare_target(repo_dir, package_manager), "--activate"]
    prepare_result = run_command(command=prepare_cmd, cwd=repo_dir, log_file=log_file)
    if prepare_result.exit_code != 0:
        return _PackageManagerResolution(
            command_transform="direct",
            error=f"Failed to bootstrap {package_manager} via corepack",
        )

    if is_command_available(executable):
        return _PackageManagerResolution(command_transform="direct")

    # Use corepack runner even when shim is not present on PATH in this environment.
    return _PackageManagerResolution(command_transform="corepack")


def _run_python_install(repo_dir: Path, log_file: Path) -> StepRunResult:
    if not is_python_project(repo_dir):
        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message="No Python project markers found (pyproject.toml / requirements.txt / setup.py / Pipfile)",
        )

    project_root = find_python_project_root(repo_dir)

    stripped_py_pkgs = strip_engine_managed_requirements(project_root)
    if stripped_py_pkgs:
        append_log(
            log_file,
            "Removed engine-managed packages from requirements.txt: "
            + ", ".join(stripped_py_pkgs),
        )

    declared_manager = py_detect_package_manager(project_root)
    package_manager = py_effective_package_manager(project_root)

    if package_manager != declared_manager:
        append_log(
            log_file,
            f"{declared_manager} not available on engine; falling back to {package_manager}",
        )

    if package_manager != "pip":
        availability_error = _ensure_python_manager_available(package_manager=package_manager)
        if availability_error:
            return StepRunResult(status="failed", exit_code=127, summary_message=availability_error)

    if package_manager == "pip":
        venv_error = _ensure_python_venv(repo_dir=project_root, log_file=log_file)
        if venv_error:
            return StepRunResult(status="failed", exit_code=1, summary_message=venv_error)

    has_lock = py_has_lock_file(project_root, package_manager)
    primary_cmd = py_install_command(package_manager, frozen_lock=has_lock, repo_dir=project_root)

    primary_result = run_command(command=primary_cmd, cwd=project_root, log_file=log_file)
    if primary_result.exit_code == 0:
        mode = "frozen" if has_lock else "install"
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=f"python dependencies installed ({package_manager} {mode})",
        )

    if has_lock:
        fallback_cmd = py_install_command(package_manager, frozen_lock=False, repo_dir=project_root)
        if fallback_cmd != primary_cmd:
            fallback_result = run_command(command=fallback_cmd, cwd=project_root, log_file=log_file)
            if fallback_result.exit_code == 0:
                return StepRunResult(
                    status="success",
                    exit_code=0,
                    summary_message=f"python dependencies installed ({package_manager} fallback install)",
                )
            return StepRunResult(
                status="failed",
                exit_code=fallback_result.exit_code,
                summary_message=(
                    f"python dependency install failed ({package_manager} frozen and fallback both failed)"
                ),
            )

    return StepRunResult(
        status="failed",
        exit_code=primary_result.exit_code,
        summary_message=f"python dependency install failed ({package_manager})",
    )


def _ensure_python_manager_available(package_manager: str) -> str | None:
    if package_manager == "pip":
        return None

    executable = py_package_manager_executable(package_manager)
    if py_is_command_available(executable):
        return None

    return (
        f"Command not found: {package_manager} "
        f"(install it on the engine host, e.g. `pipx install {package_manager}`)"
    )


def _ensure_python_venv(repo_dir: Path, log_file: Path) -> str | None:
    if venv_exists(repo_dir):
        return None

    create_result = run_command(
        command=create_venv_command(repo_dir),
        cwd=repo_dir,
        log_file=log_file,
    )
    if create_result.exit_code != 0:
        return (
            "failed to create python virtualenv (.venv); "
            "ensure python3-venv is installed on the engine host"
        )

    if not venv_exists(repo_dir):
        return f"python virtualenv missing after creation: {venv_python(repo_dir)}"

    # Upgrade pip inside the venv best-effort; failure is non-fatal.
    run_command(
        command=[str(venv_python(repo_dir)), "-m", "pip", "install", "--upgrade", "pip"],
        cwd=repo_dir,
        log_file=log_file,
    )
    return None


def _run_java_install(repo_dir: Path, log_file: Path) -> StepRunResult:
    if not is_java_project(repo_dir):
        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message="No Java project markers found (pom.xml / build.gradle)",
        )

    project_root = find_java_project_root(repo_dir)
    build_tool = java_detect_build_tool(project_root)
    java_ensure_wrapper_executable(project_root, build_tool)

    using_wrapper = java_has_wrapper(project_root, build_tool)
    executable = java_build_tool_executable(project_root, build_tool)

    if not using_wrapper and not java_is_command_available(executable):
        return StepRunResult(
            status="failed",
            exit_code=127,
            summary_message=(
                f"Command not found: {build_tool} "
                f"(install it on the engine host or include the {build_tool} wrapper in the repo)"
            ),
        )

    java_env = setup_java_env()

    cmd = java_install_command(project_root, build_tool)
    result = run_command(command=cmd, cwd=project_root, log_file=log_file, env=java_env or None)
    if result.exit_code == 0:
        wrapper_note = " via wrapper" if using_wrapper else ""
        return StepRunResult(
            status="success",
            exit_code=0,
            summary_message=f"java dependencies resolved ({build_tool}{wrapper_note})",
        )

    for fallback_cmd in java_install_command_fallbacks(project_root, build_tool):
        append_log(log_file, f"Primary install failed; trying fallback: {' '.join(fallback_cmd)}")
        fallback_result = run_command(command=fallback_cmd, cwd=project_root, log_file=log_file, env=java_env or None)
        if fallback_result.exit_code == 0:
            wrapper_note = " via wrapper" if using_wrapper else ""
            return StepRunResult(
                status="success",
                exit_code=0,
                summary_message=f"java dependencies resolved ({build_tool}{wrapper_note}, fallback)",
            )

    return StepRunResult(
        status="failed",
        exit_code=result.exit_code,
        summary_message=f"java dependency resolution failed ({build_tool})",
    )
