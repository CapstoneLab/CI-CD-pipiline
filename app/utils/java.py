from __future__ import annotations

import os
import shutil
from pathlib import Path


SUPPORTED_BUILD_TOOLS = {"maven", "gradle"}

_MAVEN_MARKERS = ("pom.xml",)
_GRADLE_MARKERS = (
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
)
_PROJECT_MARKERS = _MAVEN_MARKERS + _GRADLE_MARKERS

_IGNORED_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".vscode",
    "build",
    "target",
    "out",
    "bin",
    "node_modules",
    ".mvn",
    ".cache",
}

# Packages/plugins that must not be re-installed because the engine runs them
# itself. Kept as a constant for symmetry with python/node helpers; Java
# build files are less uniform so actual rewriting is not attempted here.
ENGINE_MANAGED_JAVA_ARTIFACTS = {"semgrep", "gitleaks"}


def _has_java_marker(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in _PROJECT_MARKERS)


def find_java_project_root(repo_dir: Path, max_depth: int = 3) -> Path:
    """Locate the directory that owns the Java project markers.

    Checks the repo root first; if nothing is found, performs a bounded
    breadth-first scan so monorepo layouts (``backend/pom.xml``,
    ``services/api/build.gradle``) are still discovered. Falls back to
    ``repo_dir`` when no markers are present anywhere.
    """
    if _has_java_marker(repo_dir):
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
            if _has_java_marker(child):
                return child
            queue.append((child, depth + 1))
    return repo_dir


def is_java_project(repo_dir: Path) -> bool:
    return _has_java_marker(find_java_project_root(repo_dir))


def detect_build_tool(repo_dir: Path) -> str:
    """Return ``'maven'`` or ``'gradle'``.

    Prefers Gradle when both toolchains leave files behind (very rare); a
    ``settings.gradle*`` or ``build.gradle*`` file is treated as the canonical
    signal. Falls back to ``'maven'``.
    """
    for marker in _GRADLE_MARKERS:
        if (repo_dir / marker).exists():
            return "gradle"
    if (repo_dir / "pom.xml").exists():
        return "maven"
    return "maven"


def _gradle_wrapper_is_usable(repo_dir: Path) -> bool:
    """A Gradle wrapper is only usable if both the launcher script and the
    bootstrap jar are present. Repos occasionally commit ``gradlew`` but
    forget ``gradle/wrapper/gradle-wrapper.jar`` (or ship a hand-edited
    script that mangles ``DEFAULT_JVM_OPTS`` quoting), which makes the
    wrapper crash before it can download Gradle. In those cases we want to
    silently fall back to the host-installed ``gradle``."""
    script = repo_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not script.exists():
        return False
    wrapper_jar = repo_dir / "gradle" / "wrapper" / "gradle-wrapper.jar"
    wrapper_props = repo_dir / "gradle" / "wrapper" / "gradle-wrapper.properties"
    return wrapper_jar.exists() and wrapper_props.exists()


def _maven_wrapper_is_usable(repo_dir: Path) -> bool:
    """Same idea as the Gradle check: ``mvnw`` needs either the legacy
    ``.mvn/wrapper/maven-wrapper.jar`` or the newer ``maven-wrapper.properties``
    bootstrap to actually function."""
    script = repo_dir / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    if not script.exists():
        return False
    wrapper_dir = repo_dir / ".mvn" / "wrapper"
    return (wrapper_dir / "maven-wrapper.properties").exists() or (
        wrapper_dir / "maven-wrapper.jar"
    ).exists()


def has_wrapper(repo_dir: Path, build_tool: str) -> bool:
    if build_tool == "gradle":
        return _gradle_wrapper_is_usable(repo_dir)
    if build_tool == "maven":
        return _maven_wrapper_is_usable(repo_dir)
    return False


def build_tool_executable(repo_dir: Path, build_tool: str) -> str:
    """Resolve the tool invocation. Prefers the project-local wrapper
    (``./gradlew``, ``./mvnw``) over a host-wide install so each repo uses
    its pinned build tool version. Falls back to the host binary when the
    wrapper is missing or broken (see ``_gradle_wrapper_is_usable``)."""
    if build_tool == "gradle":
        if _gradle_wrapper_is_usable(repo_dir):
            wrapper = "gradlew.bat" if os.name == "nt" else "gradlew"
            return str(repo_dir / wrapper)
        return "gradle.bat" if os.name == "nt" else "gradle"

    if build_tool == "maven":
        if _maven_wrapper_is_usable(repo_dir):
            wrapper = "mvnw.cmd" if os.name == "nt" else "mvnw"
            return str(repo_dir / wrapper)
        return "mvn.cmd" if os.name == "nt" else "mvn"

    return build_tool


def is_command_available(command_name: str) -> bool:
    return shutil.which(command_name) is not None


def ensure_wrapper_executable(repo_dir: Path, build_tool: str) -> None:
    """Ensure that the bundled wrapper script has the execute bit set.

    Git on Windows or zipped sources occasionally lose the +x bit, which
    makes ``./gradlew`` fail with ``Permission denied``.
    """
    if os.name == "nt":
        return
    wrapper_name = "gradlew" if build_tool == "gradle" else "mvnw"
    wrapper = repo_dir / wrapper_name
    if not wrapper.exists():
        return
    try:
        current_mode = wrapper.stat().st_mode
        wrapper.chmod(current_mode | 0o111)
    except OSError:
        pass


def install_command(repo_dir: Path, build_tool: str) -> list[str]:
    """Dependency resolution command.

    Java doesn't really separate "install dependencies" from the full build,
    so we resolve dependencies without compiling the whole project. This
    warms the local repository cache so subsequent test/build steps are
    offline-friendly and fail-fast on missing artifacts.
    """
    exe = build_tool_executable(repo_dir, build_tool)

    if build_tool == "maven":
        return [
            exe,
            "-B",  # batch mode (non-interactive)
            "-ntp",  # no transfer progress (cleaner logs)
            "-DskipTests",
            "dependency:go-offline",
        ]

    if build_tool == "gradle":
        return [
            exe,
            "--no-daemon",
            "-q",
            "dependencies",
        ]

    return [exe]


def test_command(repo_dir: Path, build_tool: str) -> list[str]:
    exe = build_tool_executable(repo_dir, build_tool)

    if build_tool == "maven":
        return [exe, "-B", "-ntp", "test"]

    if build_tool == "gradle":
        return [exe, "--no-daemon", "test"]

    return [exe, "test"]


def build_command(repo_dir: Path, build_tool: str) -> list[str]:
    exe = build_tool_executable(repo_dir, build_tool)

    if build_tool == "maven":
        return [exe, "-B", "-ntp", "-DskipTests", "package"]

    if build_tool == "gradle":
        # Prefer `bootJar` when Spring Boot is detected; fall back to `build`.
        if is_spring_boot_project(repo_dir):
            return [exe, "--no-daemon", "-x", "test", "bootJar"]
        return [exe, "--no-daemon", "-x", "test", "build"]

    return [exe, "build"]


def is_spring_boot_project(repo_dir: Path) -> bool:
    """Heuristic detection of Spring Boot through build file contents."""
    candidates = (
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
    )
    for marker in candidates:
        path = repo_dir / marker
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = content.lower()
        if "spring-boot" in lowered or "org.springframework.boot" in lowered:
            return True
    return False


def artifact_directories(build_tool: str) -> list[str]:
    """Directories (relative to the project root) where built archives
    (JAR/WAR/EAR) are emitted by each build tool."""
    if build_tool == "maven":
        return ["target"]
    if build_tool == "gradle":
        return ["build/libs", "build/distributions"]
    return []


_ARTIFACT_PATTERNS = ("*.jar", "*.war", "*.ear")
_ARTIFACT_EXCLUDES = (
    "-sources.jar",
    "-javadoc.jar",
    "-tests.jar",
    "original-",
)


def is_deployable_artifact(path: Path) -> bool:
    """Exclude intermediate/auxiliary archives that shouldn't be deployed."""
    name = path.name
    if any(name.endswith(suffix) for suffix in _ARTIFACT_EXCLUDES if suffix.startswith("-")):
        return False
    if any(name.startswith(prefix) for prefix in _ARTIFACT_EXCLUDES if not prefix.startswith("-")):
        return False
    return True


def has_test_files(repo_dir: Path) -> bool:
    """Check the standard Maven/Gradle layouts for test sources."""
    candidates = [
        repo_dir / "src" / "test" / "java",
        repo_dir / "src" / "test" / "kotlin",
        repo_dir / "src" / "test" / "groovy",
        repo_dir / "test",  # less common but legal for older projects
    ]
    for test_root in candidates:
        if not test_root.exists() or not test_root.is_dir():
            continue
        for _, dirs, files in os.walk(test_root):
            dirs[:] = [d for d in dirs if d not in _IGNORED_DIRS]
            for fname in files:
                if fname.endswith((".java", ".kt", ".kts", ".groovy")):
                    return True
    return False


def java_home_hint() -> str | None:
    """Return a likely JAVA_HOME candidate for the current platform so the
    engine can surface helpful errors when no JDK is installed."""
    if "JAVA_HOME" in os.environ:
        return os.environ["JAVA_HOME"]
    candidates = [
        "/usr/lib/jvm/java-21-amazon-corretto",
        "/usr/lib/jvm/java-17-amazon-corretto",
        "/usr/lib/jvm/default-java",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None
