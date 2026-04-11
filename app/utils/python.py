from __future__ import annotations

import fnmatch
import os
import shutil
from pathlib import Path

import re

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python < 3.11 fallback
    try:
        import tomli as tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


# Packages that are provided and invoked by the engine itself, so they
# must NOT be re-installed as project dependencies. semgrep in particular
# drags in 200MB+ of deps and its wheel build has been known to OOM
# small EC2 hosts. gitleaks is a Go binary also shipped by the engine.
ENGINE_MANAGED_PYTHON_PACKAGES = {"semgrep", "gitleaks"}


SUPPORTED_PACKAGE_MANAGERS = {"pip", "poetry", "pipenv", "uv", "pdm", "hatch"}

VENV_DIR_NAME = ".venv"

_PROJECT_MARKERS = (
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
)


def read_pyproject(repo_dir: Path) -> dict:
    pyproject = repo_dir / "pyproject.toml"
    if not pyproject.exists() or tomllib is None:
        return {}
    try:
        with pyproject.open("rb") as fp:
            return tomllib.load(fp)
    except (OSError, ValueError):
        return {}


def has_pyproject(repo_dir: Path) -> bool:
    return (repo_dir / "pyproject.toml").exists()


def has_requirements_txt(repo_dir: Path) -> bool:
    return (repo_dir / "requirements.txt").exists()


def _has_python_marker(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in _PROJECT_MARKERS)


def find_python_project_root(repo_dir: Path, max_depth: int = 3) -> Path:
    """Locate the directory that owns the python project markers.

    Checks the repo root first; if nothing is found, performs a bounded
    breadth-first scan to support monorepos (e.g. `backend/pyproject.toml`).
    Falls back to ``repo_dir`` when no markers are present anywhere.
    """
    if _has_python_marker(repo_dir):
        return repo_dir

    queue: list[tuple[Path, int]] = [(repo_dir, 0)]
    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        try:
            children = sorted(current.iterdir())
        except (OSError, PermissionError):
            continue
        for child in children:
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in _IGNORED_DIRS:
                continue
            if _has_python_marker(child):
                return child
            queue.append((child, depth + 1))
    return repo_dir


def is_python_project(repo_dir: Path) -> bool:
    return _has_python_marker(find_python_project_root(repo_dir))


def python_executable() -> str:
    if os.name == "nt":
        return "python"
    if shutil.which("python3"):
        return "python3"
    return "python"


def venv_dir(repo_dir: Path) -> Path:
    return repo_dir / VENV_DIR_NAME


def venv_python(repo_dir: Path) -> Path:
    base = venv_dir(repo_dir)
    if os.name == "nt":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"


def venv_exists(repo_dir: Path) -> bool:
    return venv_python(repo_dir).exists()


def create_venv_command(repo_dir: Path) -> list[str]:
    return [python_executable(), "-m", "venv", str(venv_dir(repo_dir))]


def effective_python_executable(repo_dir: Path) -> str:
    if venv_exists(repo_dir):
        return str(venv_python(repo_dir))
    return python_executable()


def package_manager_executable(package_manager: str) -> str:
    if package_manager == "pip":
        return python_executable()
    if package_manager in SUPPORTED_PACKAGE_MANAGERS:
        return package_manager
    return package_manager


def is_command_available(command_name: str) -> bool:
    return shutil.which(command_name) is not None


def effective_package_manager(repo_dir: Path) -> str:
    """Like :func:`detect_package_manager` but downgrades to ``pip`` when the
    detected manager is not installed on the host and ``requirements.txt``
    provides a viable fallback."""
    manager = detect_package_manager(repo_dir)
    if manager == "pip":
        return manager
    executable = package_manager_executable(manager)
    if is_command_available(executable):
        return manager
    if (repo_dir / "requirements.txt").exists():
        return "pip"
    return manager


def detect_package_manager(repo_dir: Path) -> str:
    data = read_pyproject(repo_dir)
    tool_section = data.get("tool") if isinstance(data, dict) else None
    tool = tool_section if isinstance(tool_section, dict) else {}

    if "poetry" in tool:
        return "poetry"
    if "pdm" in tool:
        return "pdm"
    if "hatch" in tool:
        return "hatch"
    if "uv" in tool:
        return "uv"

    if (repo_dir / "poetry.lock").exists():
        return "poetry"
    if (repo_dir / "pdm.lock").exists():
        return "pdm"
    if (repo_dir / "uv.lock").exists():
        return "uv"
    if (repo_dir / "Pipfile.lock").exists() or (repo_dir / "Pipfile").exists():
        return "pipenv"

    build_system = data.get("build-system") if isinstance(data, dict) else None
    if isinstance(build_system, dict):
        requires = build_system.get("requires", [])
        if isinstance(requires, list):
            requires_str = " ".join(str(item).lower() for item in requires)
            if "hatchling" in requires_str:
                return "hatch"
            if "poetry-core" in requires_str or "poetry_core" in requires_str:
                return "poetry"
            if "pdm-backend" in requires_str or "pdm_backend" in requires_str:
                return "pdm"

    return "pip"


def has_lock_file(repo_dir: Path, package_manager: str) -> bool:
    if package_manager == "poetry":
        return (repo_dir / "poetry.lock").exists()
    if package_manager == "pdm":
        return (repo_dir / "pdm.lock").exists()
    if package_manager == "uv":
        return (repo_dir / "uv.lock").exists()
    if package_manager == "pipenv":
        return (repo_dir / "Pipfile.lock").exists()
    if package_manager == "pip":
        return (repo_dir / "requirements.txt").exists()
    return False


def install_command(package_manager: str, frozen_lock: bool, repo_dir: Path | None = None) -> list[str]:
    py = effective_python_executable(repo_dir) if repo_dir is not None else python_executable()

    if package_manager == "poetry":
        base = ["poetry", "install", "--no-interaction", "--no-root"]
        if frozen_lock:
            base.append("--sync")
        return base

    if package_manager == "pipenv":
        if frozen_lock:
            return ["pipenv", "install", "--deploy", "--dev"]
        return ["pipenv", "install", "--dev"]

    if package_manager == "uv":
        if frozen_lock:
            return ["uv", "sync", "--frozen"]
        return ["uv", "sync"]

    if package_manager == "pdm":
        if frozen_lock:
            return ["pdm", "install", "--frozen-lockfile"]
        return ["pdm", "install"]

    if package_manager == "hatch":
        return ["hatch", "env", "create"]

    # pip: prefer requirements.txt, then editable install from pyproject/setup.py
    if repo_dir is not None:
        if (repo_dir / "requirements.txt").exists():
            return [py, "-m", "pip", "install", "-r", "requirements.txt"]
        if has_pyproject(repo_dir) or (repo_dir / "setup.py").exists():
            return [py, "-m", "pip", "install", "."]
    return [py, "-m", "pip", "install", "-r", "requirements.txt"]


def build_command(package_manager: str, repo_dir: Path | None = None) -> list[str]:
    py = effective_python_executable(repo_dir) if repo_dir is not None else python_executable()

    if package_manager == "poetry":
        return ["poetry", "build"]
    if package_manager == "uv":
        return ["uv", "build"]
    if package_manager == "pdm":
        return ["pdm", "build"]
    if package_manager == "hatch":
        return ["hatch", "build"]
    # pip / pipenv / fallback: use PEP 517 build front-end
    return [py, "-m", "build"]


def run_in_env_command(package_manager: str, args: list[str]) -> list[str]:
    if package_manager == "poetry":
        return ["poetry", "run", *args]
    if package_manager == "pipenv":
        return ["pipenv", "run", *args]
    if package_manager == "uv":
        return ["uv", "run", *args]
    if package_manager == "pdm":
        return ["pdm", "run", *args]
    if package_manager == "hatch":
        return ["hatch", "run", *args]
    return args


def test_command(package_manager: str, repo_dir: Path | None = None) -> list[str]:
    py = effective_python_executable(repo_dir) if repo_dir is not None else python_executable()
    return run_in_env_command(package_manager, [py, "-m", "pytest"])


def bootstrap_via_pip_command(package_manager: str) -> list[str]:
    """Best-effort command to install a Python package manager via pip."""
    py = python_executable()
    return [py, "-m", "pip", "install", "--user", package_manager]


TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")
_TEST_DIR_NAMES = {"tests", "test"}
_IGNORED_DIRS = {
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
    "node_modules",
    ".eggs",
    "site-packages",
    ".cache",
}


def has_test_files(repo_dir: Path) -> bool:
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]

        for dir_name in dirs:
            if dir_name in _TEST_DIR_NAMES:
                tests_path = Path(root) / dir_name
                for _, _, test_files in os.walk(tests_path):
                    if any(fname.endswith(".py") for fname in test_files):
                        return True

        for file_name in files:
            if any(fnmatch.fnmatch(file_name, pattern) for pattern in TEST_FILE_PATTERNS):
                return True

    return False


def find_test_directories(repo_dir: Path, max_depth: int = 4) -> list[Path]:
    """Return all ``test/`` and ``tests/`` directories under ``repo_dir``.

    Performs a bounded BFS so that monorepo layouts (``backend/tests``,
    ``src/tests``) and plural/singular variants (``test/`` vs ``tests/``)
    are both discovered. Ignored dirs (.venv, node_modules, etc.) are
    skipped and nested test dirs are not re-entered.
    """
    found: list[Path] = []
    queue: list[tuple[Path, int]] = [(repo_dir, 0)]
    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue
        try:
            children = sorted(current.iterdir())
        except (OSError, PermissionError):
            continue
        for child in children:
            if not child.is_dir():
                continue
            if child.name.startswith(".") or child.name in _IGNORED_DIRS:
                continue
            if child.name in _TEST_DIR_NAMES:
                found.append(child)
                continue
            queue.append((child, depth + 1))
    return found


def has_collectible_tests(repo_dir: Path) -> bool:
    """Like :func:`has_test_files` but only returns True when there is at
    least one file matching pytest's naming conventions. Empty scaffold
    packages (``tests/__init__.py`` only) are treated as empty."""
    candidates: list[Path] = [repo_dir, *find_test_directories(repo_dir)]
    seen: set[Path] = set()
    for base in candidates:
        resolved = base.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
            for file_name in files:
                if any(fnmatch.fnmatch(file_name, pattern) for pattern in TEST_FILE_PATTERNS):
                    return True
    return False


def strip_engine_managed_requirements(repo_dir: Path) -> list[str]:
    """Comment out engine-managed tools from ``requirements.txt``.

    Returns the list of package names that were neutralised. Lines matching
    packages in :data:`ENGINE_MANAGED_PYTHON_PACKAGES` are prefixed with
    ``# [engine-managed]`` instead of being deleted so diagnostics and
    future re-syncs can still see what was removed. Comments, blank lines
    and ``-r``/``-c`` include directives are preserved untouched.
    """
    req_file = repo_dir / "requirements.txt"
    if not req_file.exists():
        return []
    try:
        content = req_file.read_text(encoding="utf-8")
    except OSError:
        return []

    removed: list[str] = []
    new_lines: list[str] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            new_lines.append(raw_line)
            continue
        pkg_name = re.split(r"[\s<>=!~;\[]", stripped, maxsplit=1)[0].strip().lower()
        if pkg_name in ENGINE_MANAGED_PYTHON_PACKAGES:
            removed.append(pkg_name)
            new_lines.append(f"# [engine-managed] {raw_line}")
            continue
        new_lines.append(raw_line)

    if removed:
        req_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return removed


def has_pytest_configured(repo_dir: Path) -> bool:
    if (repo_dir / "pytest.ini").exists() or (repo_dir / "tox.ini").exists():
        return True
    data = read_pyproject(repo_dir)
    tool = data.get("tool") if isinstance(data, dict) else None
    if isinstance(tool, dict) and "pytest" in tool:
        return True
    return False
