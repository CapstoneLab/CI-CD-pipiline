from __future__ import annotations

import os
import shlex
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.constants import BUILTIN_STEP_NAMES, RUNTIME_TYPE
from app.utils.java import is_java_project
from app.utils.python import is_python_project


REPO_WORKFLOW_CANDIDATES = (
    ".localci/workflow.yml",
    ".localci/workflow.yaml",
    ".ci/workflow.yml",
    ".ci/workflow.yaml",
)

DEFAULT_REPO_WORKFLOW_PATH = ".localci/workflow.yml"
ENGINE_WORKFLOW_TEMPLATE_CANDIDATES = (
    "workflow.template.yml",
    "workflow.example.yml",
)


@dataclass
class WorkflowStepDefinition:
    name: str
    kind: str
    uses: str | None = None
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = "."
    continue_on_failure: bool = False
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowDefinition:
    name: str
    runtime_type: str
    steps: list[WorkflowStepDefinition]
    source: str
    selected_items: list[str] | None = None  # CWE ids / item keys; None = all 16


def default_workflow_definition() -> WorkflowDefinition:
    steps = [
        WorkflowStepDefinition(name="install", kind="builtin", uses="install"),
        WorkflowStepDefinition(
            name="lightweight_security_scan",
            kind="builtin",
            uses="lightweight_security_scan",
            continue_on_failure=True,
        ),
        WorkflowStepDefinition(name="test", kind="builtin", uses="test"),
        WorkflowStepDefinition(name="deep_security_scan", kind="builtin", uses="deep_security_scan"),
        WorkflowStepDefinition(name="build", kind="builtin", uses="build"),
    ]
    return WorkflowDefinition(
        name="default-node-workflow",
        runtime_type=RUNTIME_TYPE,
        steps=steps,
        source="builtin-default",
    )


def resolve_workflow_definition(
    repo_dir: Path,
    base_dir: Path,
    workflow_path: str | None = None,
) -> WorkflowDefinition:
    if workflow_path:
        resolved = _resolve_explicit_workflow_path(workflow_path=workflow_path, repo_dir=repo_dir, base_dir=base_dir)
        return load_workflow_from_yaml(resolved)

    for candidate in REPO_WORKFLOW_CANDIDATES:
        candidate_path = repo_dir / candidate
        if candidate_path.exists() and candidate_path.is_file():
            loaded = load_workflow_from_yaml(candidate_path)
            return _reconcile_workflow_runtime(loaded, repo_dir)

    detected_runtime = detect_repo_runtime(repo_dir)
    generated_path = materialize_workflow_template(
        target_path=repo_dir / DEFAULT_REPO_WORKFLOW_PATH,
        base_dir=base_dir,
        runtime_type=detected_runtime,
        repo_dir=repo_dir,
    )
    return _reconcile_workflow_runtime(load_workflow_from_yaml(generated_path), repo_dir)


def detect_repo_runtime(repo_dir: Path) -> str:
    if (repo_dir / "package.json").exists():
        return "node"
    if is_python_project(repo_dir):
        return "python"
    if is_java_project(repo_dir):
        return "java"
    return RUNTIME_TYPE


def _runtime_markers_present(runtime_type: str, repo_dir: Path) -> bool:
    if runtime_type == "node":
        return (repo_dir / "package.json").exists()
    if runtime_type == "python":
        return is_python_project(repo_dir)
    if runtime_type == "java":
        return is_java_project(repo_dir)
    return False


def _reconcile_workflow_runtime(workflow: WorkflowDefinition, repo_dir: Path) -> WorkflowDefinition:
    declared = workflow.runtime_type
    if _runtime_markers_present(declared, repo_dir):
        return workflow

    detected = detect_repo_runtime(repo_dir)
    if detected == declared:
        return workflow
    if not _runtime_markers_present(detected, repo_dir):
        return workflow

    workflow.runtime_type = detected
    return workflow


def load_workflow_from_yaml(file_path: Path) -> WorkflowDefinition:
    try:
        with file_path.open("r", encoding="utf-8") as fp:
            raw = yaml.safe_load(fp)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in workflow file: {file_path} ({exc})") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Workflow file must contain a YAML object: {file_path}")

    name = str(raw.get("name") or file_path.stem)
    runtime = raw.get("runtime")
    runtime_type = RUNTIME_TYPE
    if isinstance(runtime, dict):
        runtime_type = str(runtime.get("type") or RUNTIME_TYPE)

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError(f"Workflow file must define a non-empty 'steps' list: {file_path}")

    parsed_steps: list[WorkflowStepDefinition] = []
    for idx, item in enumerate(raw_steps, start=1):
        parsed_steps.append(_parse_step(item=item, step_index=idx, file_path=file_path))

    return WorkflowDefinition(
        name=name,
        runtime_type=runtime_type,
        steps=parsed_steps,
        source=str(file_path),
        selected_items=_parse_selected_items(raw),
    )


def _parse_selected_items(raw: dict) -> list[str] | None:
    """Read selected security items from `selected_items:` or `security.selected_items:`."""
    candidate = raw.get("selected_items")
    if candidate is None:
        security = raw.get("security")
        if isinstance(security, dict):
            candidate = security.get("selected_items")
    if candidate is None:
        return None
    if isinstance(candidate, (list, tuple)):
        items = [str(token).strip() for token in candidate if str(token).strip()]
        return items or None
    if isinstance(candidate, str):
        items = [token.strip() for token in candidate.split(",") if token.strip()]
        return items or None
    return None


def _parse_step(item: Any, step_index: int, file_path: Path) -> WorkflowStepDefinition:
    if not isinstance(item, dict):
        raise ValueError(f"Step #{step_index} must be an object in {file_path}")

    uses = item.get("uses")
    run = item.get("run")
    if uses and run:
        raise ValueError(f"Step #{step_index} cannot define both 'uses' and 'run' in {file_path}")
    if not uses and not run:
        raise ValueError(f"Step #{step_index} must define either 'uses' or 'run' in {file_path}")

    name = str(item.get("name") or uses or f"command-{step_index}")
    continue_on_failure = bool(item.get("continue_on_failure", False))
    cwd = str(item.get("cwd") or ".")

    raw_env = item.get("env") or {}
    if not isinstance(raw_env, dict):
        raise ValueError(f"Step '{name}' has invalid env. It must be an object in {file_path}")
    env = {str(key): str(value) for key, value in raw_env.items()}

    raw_args = item.get("args") or {}
    if not isinstance(raw_args, dict):
        raise ValueError(f"Step '{name}' has invalid args. It must be an object in {file_path}")

    if uses:
        uses_name = str(uses)
        if uses_name == "clone":
            raise ValueError(
                "Workflow steps must not include 'clone'. Repository clone is executed by the engine bootstrap phase"
            )
        if uses_name not in BUILTIN_STEP_NAMES:
            available_steps = ", ".join(sorted(name for name in BUILTIN_STEP_NAMES if name != "clone"))
            raise ValueError(
                f"Unknown built-in step '{uses_name}'. Available steps: {available_steps}"
            )
        return WorkflowStepDefinition(
            name=name,
            kind="builtin",
            uses=uses_name,
            env=env,
            cwd=cwd,
            continue_on_failure=continue_on_failure,
            args=raw_args,
        )

    return WorkflowStepDefinition(
        name=name,
        kind="command",
        command=_normalize_command(run),
        env=env,
        cwd=cwd,
        continue_on_failure=continue_on_failure,
        args=raw_args,
    )


def _normalize_command(raw_command: Any) -> list[str]:
    if isinstance(raw_command, list):
        parts = [str(part) for part in raw_command if str(part).strip()]
        if not parts:
            raise ValueError("Command step has an empty 'run' list")
        return parts

    if isinstance(raw_command, str):
        parts = shlex.split(raw_command, posix=(os.name != "nt"))
        if not parts:
            raise ValueError("Command step has an empty 'run' string")
        return parts

    raise ValueError("Command step 'run' must be a string or list")


def _resolve_explicit_workflow_path(workflow_path: str, repo_dir: Path, base_dir: Path) -> Path:
    candidate = Path(workflow_path)
    if candidate.is_absolute():
        if candidate.exists() and candidate.is_file():
            return candidate
        raise ValueError(f"Workflow file not found: {candidate}")

    repo_candidate = repo_dir / candidate
    if repo_candidate.exists() and repo_candidate.is_file():
        return repo_candidate

    base_candidate = base_dir / candidate
    if base_candidate.exists() and base_candidate.is_file():
        return base_candidate

    if _looks_like_yaml_file(candidate):
        return materialize_workflow_template(target_path=repo_candidate, base_dir=base_dir)

    raise ValueError(
        "Workflow file not found. Checked relative paths in repository and engine root: "
        f"{workflow_path}"
    )


def materialize_workflow_template(
    target_path: Path,
    base_dir: Path,
    runtime_type: str = RUNTIME_TYPE,
    repo_dir: Path | None = None,
) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not target_path.exists():
        target_path.write_text(
            _load_template_text(base_dir, runtime_type=runtime_type, repo_dir=repo_dir),
            encoding="utf-8",
        )
    return target_path


def _load_template_text(
    base_dir: Path,
    runtime_type: str = RUNTIME_TYPE,
    repo_dir: Path | None = None,
) -> str:
    if runtime_type == "node" and repo_dir is not None and _looks_like_juice_shop_project(repo_dir):
        return _juice_shop_template_yaml_text()

    if runtime_type == "node" and repo_dir is not None and _looks_like_dvna_project(repo_dir):
        return _dvna_docker_template_yaml_text()

    if runtime_type == "java" and repo_dir is not None and _looks_like_webgoat_project(repo_dir):
        return _webgoat_docker_template_yaml_text()

    if runtime_type == "python" and repo_dir is not None and _looks_like_dvpwa_project(repo_dir):
        return _dvpwa_docker_template_yaml_text()

    if runtime_type == "python" and repo_dir is not None and _looks_like_python2_project(repo_dir):
        return _python2_docker_template_yaml_text(repo_dir)

    # For non-node runtimes we always emit the built-in template for that runtime
    # instead of falling back to the engine's node-oriented example files.
    if runtime_type == RUNTIME_TYPE:
        for relative_path in ENGINE_WORKFLOW_TEMPLATE_CANDIDATES:
            candidate = base_dir / relative_path
            if candidate.exists() and candidate.is_file():
                return candidate.read_text(encoding="utf-8")

    return _default_template_yaml_text(runtime_type=runtime_type)


def _looks_like_juice_shop_project(repo_dir: Path) -> bool:
    package_json = repo_dir / "package.json"
    if not package_json.exists():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    name = str(data.get("name") or "").lower()
    scripts = data.get("scripts")
    if name == "juice-shop" and isinstance(scripts, dict):
        return "test:frontend" in scripts and "test:server" in scripts and "test:api" in scripts
    return False


def _juice_shop_template_yaml_text() -> str:
    return (
        "name: juice-shop-generated-workflow\n"
        "runtime:\n"
        "  type: node\n"
        "steps:\n"
        "  - name: install\n"
        "    uses: install\n"
        "\n"
        "  - name: lightweight-security\n"
        "    uses: lightweight_security_scan\n"
        "    continue_on_failure: true\n"
        "    args:\n"
        "      report_file: gitleaks_report.json\n"
        "\n"
        "  - name: test\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        npm run test:frontend\n"
        "        npm run test:server\n"
        "\n"
        "  - name: deep-security\n"
        "    uses: deep_security_scan\n"
        "    args:\n"
        "      report_file: semgrep_report.json\n"
        "\n"
        "  - name: build\n"
        "    uses: build\n"
        "\n"
        "  - name: deploy\n"
        "    uses: deploy\n"
    )


def _looks_like_dvna_project(repo_dir: Path) -> bool:
    package_json = repo_dir / "package.json"
    if not package_json.exists():
        return False
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    name = str(data.get("name") or "").lower()
    dependencies = data.get("dependencies")
    if name != "dvna" or not isinstance(dependencies, dict):
        return False
    if "libxmljs" not in dependencies or "node-serialize" not in dependencies:
        return False

    dockerfile = repo_dir / "Dockerfile"
    if not dockerfile.exists():
        return False
    try:
        dockerfile_text = dockerfile.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "from node:carbon" in dockerfile_text


def _looks_like_webgoat_project(repo_dir: Path) -> bool:
    pom_file = repo_dir / "pom.xml"
    if not pom_file.exists():
        return False
    try:
        pom_text = pom_file.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return (
        "<groupid>org.owasp.webgoat</groupid>" in pom_text
        and "<artifactid>webgoat</artifactid>" in pom_text
    )


def _webgoat_docker_template_yaml_text() -> str:
    return (
        "name: webgoat-docker-generated-workflow\n"
        "runtime:\n"
        "  type: java\n"
        "steps:\n"
        "  - name: install\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        mkdir -p .localci/m2\n"
        "        docker run --rm \\\n"
        "          -v \"$PWD:/repo\" \\\n"
        "          -v \"$PWD/.localci/m2:/root/.m2\" \\\n"
        "          -w /repo \\\n"
        "          eclipse-temurin:25-jdk \\\n"
        "          bash -lc './mvnw -B -ntp -DskipTests compile'\n"
        "\n"
        "  - name: lightweight-security\n"
        "    uses: lightweight_security_scan\n"
        "    continue_on_failure: true\n"
        "    args:\n"
        "      report_file: gitleaks_report.json\n"
        "\n"
        "  - name: test\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        mkdir -p .localci/m2\n"
        "        docker run --rm \\\n"
        "          -v \"$PWD:/repo\" \\\n"
        "          -v \"$PWD/.localci/m2:/root/.m2\" \\\n"
        "          -w /repo \\\n"
        "          eclipse-temurin:25-jdk \\\n"
        "          bash -lc './mvnw -B -ntp test'\n"
        "\n"
        "  - name: deep-security\n"
        "    uses: deep_security_scan\n"
        "    args:\n"
        "      report_file: semgrep_report.json\n"
        "\n"
        "  - name: build\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        mkdir -p .localci/m2\n"
        "        docker run --rm \\\n"
        "          -v \"$PWD:/repo\" \\\n"
        "          -v \"$PWD/.localci/m2:/root/.m2\" \\\n"
        "          -w /repo \\\n"
        "          eclipse-temurin:25-jdk \\\n"
        "          bash -lc './mvnw -B -ntp -DskipTests package'\n"
        "\n"
        "  - name: deploy\n"
        "    uses: deploy\n"
    )


def _dvna_docker_template_yaml_text() -> str:
    return (
        "name: dvna-docker-generated-workflow\n"
        "runtime:\n"
        "  type: node\n"
        "steps:\n"
        "  - name: install\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        docker build -t localci-dvna:ci .\n"
        "\n"
        "  - name: lightweight-security\n"
        "    uses: lightweight_security_scan\n"
        "    continue_on_failure: true\n"
        "    args:\n"
        "      report_file: gitleaks_report.json\n"
        "\n"
        "  - name: test\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        docker run --rm localci-dvna:ci node --check /app/server.js\n"
        "\n"
        "  - name: deep-security\n"
        "    uses: deep_security_scan\n"
        "    args:\n"
        "      report_file: semgrep_report.json\n"
        "\n"
        "  - name: build\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        docker image inspect localci-dvna:ci >/dev/null\n"
        "\n"
        "  - name: deploy\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        echo \"Docker image localci-dvna:ci is ready for deployment\"\n"
    )


def _looks_like_python2_project(repo_dir: Path) -> bool:
    version_files = [repo_dir / ".python-version", *repo_dir.glob("*/.python-version")]
    for version_file in version_files:
        try:
            version = version_file.read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        if version.startswith("2.7") or version.startswith("python-2.7"):
            return True

    dockerfiles = [repo_dir / "Dockerfile", *repo_dir.glob("*/Dockerfile")]
    for dockerfile in dockerfiles:
        try:
            text = dockerfile.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "from python:2.7" in text or "from python:2-" in text:
            return True

    return False


def _looks_like_dvpwa_project(repo_dir: Path) -> bool:
    if not (repo_dir / "Dockerfile.app").exists():
        return False
    if not (repo_dir / "requirements.txt").exists():
        return False
    if not (repo_dir / "run.py").exists() or not (repo_dir / "sqli").is_dir():
        return False

    try:
        readme = (repo_dir / "README.rst").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        readme = ""
    if "damn vulnerable python web application" in readme or "dvpwa" in readme:
        return True

    try:
        dockerfile = (repo_dir / "Dockerfile.app").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "python:alpine3.8" in dockerfile and "wait-for" in dockerfile


def _dvpwa_docker_template_yaml_text() -> str:
    return (
        "name: dvpwa-docker-generated-workflow\n"
        "runtime:\n"
        "  type: python\n"
        "steps:\n"
        "  - name: install\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        docker build -f Dockerfile.app -t localci-dvpwa-app:ci .\n"
        "\n"
        "  - name: lightweight-security\n"
        "    uses: lightweight_security_scan\n"
        "    continue_on_failure: true\n"
        "    args:\n"
        "      report_file: gitleaks_report.json\n"
        "\n"
        "  - name: test\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        docker run --rm localci-dvpwa-app:ci python -m compileall -q /app\n"
        "\n"
        "  - name: deep-security\n"
        "    uses: deep_security_scan\n"
        "    args:\n"
        "      report_file: semgrep_report.json\n"
        "\n"
        "  - name: build\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        docker image inspect localci-dvpwa-app:ci >/dev/null\n"
        "\n"
        "  - name: deploy\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        echo \"Docker image localci-dvpwa-app:ci is ready for deployment\"\n"
    )


def _python_project_cwd(repo_dir: Path) -> str:
    if (repo_dir / "requirements.txt").exists() or (repo_dir / "pyproject.toml").exists():
        return "."
    candidates = [
        child
        for child in sorted(repo_dir.iterdir())
        if child.is_dir()
        and not child.name.startswith(".")
        and ((child / "requirements.txt").exists() or (child / "pyproject.toml").exists())
    ]
    if len(candidates) == 1:
        return candidates[0].name
    return "."


def _python2_docker_template_yaml_text(repo_dir: Path) -> str:
    cwd = _python_project_cwd(repo_dir)
    return (
        "name: python2-docker-generated-workflow\n"
        "runtime:\n"
        "  type: python\n"
        "steps:\n"
        "  - name: install\n"
        "    cwd: " + cwd + "\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        grep -vi '^mysql-python==' requirements.txt > requirements.ci-py27.txt\n"
        "        docker run --rm -v \"$PWD:/repo\" -w /repo python:2.7-buster bash -lc '\n"
        "          python -m pip install --upgrade \"pip<21\" \"setuptools<45\" wheel virtualenv\n"
        "          python -m virtualenv .venv-py27\n"
        "          .venv-py27/bin/python -m pip install --upgrade \"pip<21\" \"setuptools<45\" wheel\n"
        "          .venv-py27/bin/python -m pip install -r requirements.ci-py27.txt\n"
        "        '\n"
        "\n"
        "  - name: lightweight-security\n"
        "    uses: lightweight_security_scan\n"
        "    continue_on_failure: true\n"
        "    args:\n"
        "      report_file: gitleaks_report.json\n"
        "\n"
        "  - name: test\n"
        "    cwd: " + cwd + "\n"
        "    run:\n"
        "      - bash\n"
        "      - -lc\n"
        "      - |\n"
        "        docker run --rm -v \"$PWD:/repo\" -w /repo python:2.7-buster bash -lc '\n"
        "          APP_PORT=5050 .venv-py27/bin/python app.py > /tmp/vulnerable-flask.log 2>&1 &\n"
        "          pid=$!\n"
        "          ok=0\n"
        "          for i in 1 2 3 4 5 6 7 8; do\n"
        "            sleep 1\n"
        "            .venv-py27/bin/python -c \"import urllib2; r=urllib2.urlopen(\\\"http://127.0.0.1:5050/\\\", timeout=2); print(r.getcode())\" && ok=1 && break\n"
        "          done\n"
        "          cat /tmp/vulnerable-flask.log\n"
        "          kill $pid 2>/dev/null || true\n"
        "          test \"$ok\" = 1\n"
        "        '\n"
        "\n"
        "  - name: deep-security\n"
        "    uses: deep_security_scan\n"
        "    args:\n"
        "      report_file: semgrep_report.json\n"
        "\n"
        "  - name: build\n"
        "    uses: build\n"
        "    cwd: " + cwd + "\n"
        "\n"
        "  - name: deploy\n"
        "    uses: deploy\n"
        "    cwd: " + cwd + "\n"
    )


def _default_template_yaml_text(runtime_type: str = RUNTIME_TYPE) -> str:
    return (
        "name: default-generated-workflow\n"
        "runtime:\n"
        f"  type: {runtime_type}\n"
        "steps:\n"
        "  - name: install\n"
        "    uses: install\n"
        "\n"
        "  - name: lightweight-security\n"
        "    uses: lightweight_security_scan\n"
        "    continue_on_failure: true\n"
        "    args:\n"
        "      report_file: gitleaks_report.json\n"
        "\n"
        "  - name: test\n"
        "    uses: test\n"
        "\n"
        "  - name: deep-security\n"
        "    uses: deep_security_scan\n"
        "    args:\n"
        "      report_file: semgrep_report.json\n"
        "\n"
        "  - name: build\n"
        "    uses: build\n"
        "\n"
        "  - name: deploy\n"
        "    uses: deploy\n"
    )


def _looks_like_yaml_file(candidate: Path) -> bool:
    return candidate.suffix.lower() in {".yml", ".yaml"}


def generate_dynamic_workflow(repo_path: str) -> WorkflowDefinition:
    package_json_path = Path(repo_path) / "package.json"

    if package_json_path.exists():
        with open(package_json_path, "r", encoding="utf-8") as f:
            package_data = json.load(f)
            scripts = package_data.get("scripts", {})

            test_command = scripts.get("test")
            build_command = scripts.get("build", "npm run build")
    else:
        test_command = None
        build_command = "npm run build"

    steps = [
        WorkflowStepDefinition(name="install", kind="builtin", uses="install"),
        WorkflowStepDefinition(name="lightweight-security", kind="builtin", uses="lightweight_security_scan", continue_on_failure=True, args={"report_file": "gitleaks_report.json"}),
    ]

    if test_command:
        steps.append(WorkflowStepDefinition(name="test", kind="command", command=shlex.split(test_command)))

    steps.append(WorkflowStepDefinition(name="deep-security", kind="builtin", uses="deep_security_scan", args={"report_file": "semgrep_report.json"}))
    steps.append(WorkflowStepDefinition(name="build", kind="command", command=shlex.split(build_command)))

    return WorkflowDefinition(
        name="dynamic-workflow",
        runtime_type="node",
        steps=steps,
        source="generated",
    )


def save_workflow_to_file(workflow: WorkflowDefinition, repo_path: str):
    workflow_path = Path(repo_path) / DEFAULT_REPO_WORKFLOW_PATH
    with open(workflow_path, "w", encoding="utf-8") as f:
        yaml.dump(workflow, f)


def ensure_workflow_exists(repo_path: str):
    for candidate in REPO_WORKFLOW_CANDIDATES:
        if (Path(repo_path) / candidate).exists():
            return

    workflow = generate_dynamic_workflow(repo_path)
    save_workflow_to_file(workflow, repo_path)
