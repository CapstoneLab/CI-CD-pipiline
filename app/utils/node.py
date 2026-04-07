from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path


def read_package_json(repo_dir: Path) -> dict:
    package_json = repo_dir / "package.json"
    if not package_json.exists():
        return {}

    try:
        return json.loads(package_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def has_script(repo_dir: Path, script_name: str) -> bool:
    package_data = read_package_json(repo_dir)
    scripts = package_data.get("scripts", {})
    return isinstance(scripts, dict) and script_name in scripts


def get_script(repo_dir: Path, script_name: str) -> str | None:
    package_data = read_package_json(repo_dir)
    scripts = package_data.get("scripts", {})
    if not isinstance(scripts, dict):
        return None
    script = scripts.get(script_name)
    return script if isinstance(script, str) else None


def is_placeholder_test_script(script_value: str | None) -> bool:
    if not script_value:
        return True

    value = script_value.lower()
    return "no test specified" in value


def has_test_files(repo_dir: Path) -> bool:
    test_file_patterns = [
        "*.spec.js",
        "*.test.js",
        "*.spec.ts",
        "*.test.ts",
        "*.spec.jsx",
        "*.test.jsx",
        "*.spec.tsx",
        "*.test.tsx",
        "*.spec.mjs",
        "*.test.mjs",
        "*.spec.cjs",
        "*.test.cjs",
    ]
    ignored_dirs = {
        "node_modules",
        ".git",
        "dist",
        "build",
        "coverage",
        ".next",
        "out",
    }

    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]

        for file_name in files:
            if any(fnmatch.fnmatch(file_name, pattern) for pattern in test_file_patterns):
                return True

    return False


def npm_executable() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"
