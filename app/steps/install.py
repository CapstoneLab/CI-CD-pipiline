from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.models import StepRunResult
from app.utils.node import (
    corepack_executable,
    detect_package_manager,
    has_lock_file,
    install_command,
    is_command_available,
    package_manager_executable,
    package_manager_prepare_target,
    read_package_json,
    wrap_with_corepack,
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


def run_install(repo_dir: Path, log_file: Path) -> StepRunResult:
    is_supported, reason = _detect_node_project(repo_dir)
    if not is_supported:
        return StepRunResult(status="failed", exit_code=1, summary_message=reason)

    package_manager = detect_package_manager(repo_dir)
    resolution = _resolve_package_manager_runner(repo_dir=repo_dir, package_manager=package_manager, log_file=log_file)
    if resolution.error:
        return StepRunResult(status="failed", exit_code=127, summary_message=resolution.error)

    if has_lock_file(repo_dir, package_manager):
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
