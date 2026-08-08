from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.constants import RUNS_DIR_NAME, WORKSPACE_DIR_NAME

KST = timezone(timedelta(hours=9))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def data_root(base_dir: Path) -> Path:
    """Return the persistent run-data root.

    Containers set ``CICD_ENGINE_DATA_DIR`` to a host path mounted at the
    exact same absolute path. Keeping the path identical on both sides lets
    child ``docker run -v`` commands use workspace paths without translating
    container paths back to host paths.
    """
    configured = os.environ.get("CICD_ENGINE_DATA_DIR", "").strip()
    if not configured:
        return base_dir
    return Path(configured).expanduser().resolve()


def make_run_id(base_dir: Path) -> str:
    runs_dir = data_root(base_dir) / RUNS_DIR_NAME
    ensure_dir(runs_dir)

    date_prefix = datetime.now(KST).strftime("%Y%m%d")
    run_prefix = f"run-{date_prefix}-"

    max_index = 0
    for item in runs_dir.iterdir():
        if not item.is_dir() or not item.name.startswith(run_prefix):
            continue
        suffix = item.name.replace(run_prefix, "", 1)
        if suffix.isdigit():
            max_index = max(max_index, int(suffix))

    return f"{run_prefix}{max_index + 1:03d}"


def prepare_run_paths(base_dir: Path, run_id: str) -> dict[str, Path]:
    root_dir = data_root(base_dir)
    runs_dir = root_dir / RUNS_DIR_NAME
    workspace_dir = root_dir / WORKSPACE_DIR_NAME

    run_dir = runs_dir / run_id
    logs_dir = run_dir / "logs"
    workspace_run_dir = workspace_dir / run_id
    repo_dir = workspace_run_dir / "repo"

    ensure_dir(run_dir)
    ensure_dir(logs_dir)
    ensure_dir(workspace_run_dir)

    return {
        "run_dir": run_dir,
        "logs_dir": logs_dir,
        "workspace_run_dir": workspace_run_dir,
        "repo_dir": repo_dir,
    }


def save_json(file_path: Path, payload: Any) -> None:
    ensure_dir(file_path.parent)
    with file_path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
