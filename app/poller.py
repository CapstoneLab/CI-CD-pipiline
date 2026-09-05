from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib import error, request

from app.utils.filesystem import data_root

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass

LOG_PATH = data_root(BASE_DIR) / "runs" / "poller.log"
CONTAINER_MARKER = Path("/.dockerenv")

logger = logging.getLogger("poller")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(stream)


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        logger.error("missing required env: %s", name)
        sys.exit(2)
    return value


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    engine_id: str,
    payload: dict[str, Any] | None = None,
    timeout_sec: int = 10,
) -> tuple[int, dict | list | None, str]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = request.Request(
        url,
        data=body,
        method=method,
        headers={
            "x-engine-token": token,
            "x-engine-id": engine_id,
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw) if raw else None, ""
            except json.JSONDecodeError:
                return resp.status, None, raw
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, None, body
    except error.URLError as exc:
        return 0, None, f"url error: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return 0, None, f"unexpected: {exc}"


def _fetch_pending(base_url: str, token: str, engine_id: str, limit: int) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/jobs/pending?limit={limit}"
    status, payload, raw = _http_json("GET", url, token=token, engine_id=engine_id)
    if status != 200:
        logger.warning("pending fetch failed: HTTP %s  %s", status, raw[:200])
        return []
    if not isinstance(payload, dict):
        logger.warning("pending response not a dict: %r", payload)
        return []
    jobs = payload.get("jobs", [])
    if not isinstance(jobs, list):
        return []
    return [j for j in jobs if isinstance(j, dict) and j.get("job_id")]


def _claim(base_url: str, job_id: str, token: str, engine_id: str) -> bool:
    url = f"{base_url.rstrip('/')}/api/jobs/{job_id}/claim"
    status, _payload, raw = _http_json("POST", url, token=token, engine_id=engine_id)
    if status == 200:
        return True
    if status == 409:
        logger.info("job %s already claimed/finished: %s", job_id, raw[:200])
        return False
    logger.warning("claim failed for %s: HTTP %s  %s", job_id, status, raw[:200])
    return False


def _report_failure(
    base_url: str,
    job_id: str,
    token: str,
    engine_id: str,
    *,
    reason: str,
    detail: str = "",
) -> None:
    url = f"{base_url.rstrip('/')}/api/jobs/{job_id}/fail"
    status, _payload, raw = _http_json(
        "POST",
        url,
        token=token,
        engine_id=engine_id,
        payload={"reason": reason, "detail": detail},
    )
    if status != 200:
        logger.warning("failure report failed for %s: HTTP %s  %s", job_id, status, raw[:200])


def _normalize_selected_items(value: Any) -> list[str]:
    """Accept selected_items as a list of strings (CWE id / key) or objects with
    a 'cwe'/'key' field, or a comma-separated string. Returns a flat token list."""
    if not value:
        return []
    if isinstance(value, str):
        return [tok.strip() for tok in value.split(",") if tok.strip()]
    if isinstance(value, (list, tuple)):
        tokens: list[str] = []
        for entry in value:
            if isinstance(entry, str) and entry.strip():
                tokens.append(entry.strip())
            elif isinstance(entry, dict):
                token = entry.get("cwe") or entry.get("key") or entry.get("id")
                if token and str(token).strip():
                    tokens.append(str(token).strip())
        return tokens
    return []


def _spawn_pipeline(
    job: dict[str, Any],
    callback_url: str,
    callback_token: str = "",
) -> subprocess.Popen | None:
    job_id = str(job.get("job_id") or "").strip()
    repo_url = str(job.get("repo_url") or "").strip()
    branch = str(job.get("branch") or "main").strip() or "main"
    source = str(job.get("source") or "capstone").strip() or "capstone"
    environment = str(job.get("environment") or "development").strip() or "development"
    workflow_path = str(job.get("workflow_path") or "").strip()
    selected_items = _normalize_selected_items(job.get("selected_items"))
    approved_cwes = _normalize_selected_items(job.get("approved_cwes"))
    commit_sha = str(job.get("commit_sha") or "").strip()
    repo_token = str(job.get("repo_token") or job.get("access_token") or "").strip()

    if not job_id or not repo_url:
        logger.error("invalid job payload (missing job_id/repo_url): %r", job)
        return None

    if source not in {"capstone", "mirae"}:
        logger.warning("unknown source %r, defaulting to capstone", source)
        source = "capstone"
    if environment not in {"production", "staging", "development", "feature"}:
        logger.warning("unknown environment %r, defaulting to development", environment)
        environment = "development"

    python_bin = BASE_DIR / ".venv" / "bin" / "python"
    if not python_bin.exists():
        python_bin = Path(sys.executable)

    cmd = [
        str(python_bin),
        str(BASE_DIR / "main.py"),
        "--job-id", job_id,
        "--repo", repo_url,
        "--branch", branch,
        "--source", source,
        "--environment", environment,
        "--callback-url", callback_url,
    ]
    if callback_token:
        cmd.extend(["--callback-token", callback_token])
    if workflow_path:
        cmd.extend(["--workflow", workflow_path])
    if selected_items:
        # backend payload selection -> engine. empty/missing = scan all 16 items.
        cmd.extend(["--selected-items", ",".join(selected_items)])
    if commit_sha:
        cmd.extend(["--commit", commit_sha])
    if repo_token:
        # private-repo OAuth/PAT token; masked downstream in run_command logs
        cmd.extend(["--repo-token", repo_token])
    if approved_cwes:
        # approved follow-up job: these High CWEs pass the gate (acknowledged)
        cmd.extend(["--approved-cwes", ",".join(approved_cwes)])

    spawn_log = data_root(BASE_DIR) / "runs" / f"job-{job_id}.log"
    spawn_log.parent.mkdir(parents=True, exist_ok=True)
    log_fh = spawn_log.open("ab", buffering=0)
    log_fh.write(f"\n=== poller spawn at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
    sensitive_args = {value for value in (repo_token, callback_token) if value}
    safe_cmd = " ".join("****" if tok in sensitive_args else tok for tok in cmd)
    log_fh.write(f"cmd: {safe_cmd}\n\n".encode())

    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    logger.info("spawned pipeline pid=%s job=%s repo=%s branch=%s", proc.pid, job_id, repo_url, branch)
    return proc


def _reaper(running: dict[str, subprocess.Popen], base_url: str, token: str, engine_id: str) -> None:
    finished: list[str] = []
    for job_id, proc in running.items():
        rc = proc.poll()
        if rc is None:
            continue
        finished.append(job_id)
        logger.info("pipeline finished job=%s pid=%s exit=%s", job_id, proc.pid, rc)
        if rc != 0:
            _report_failure(
                base_url,
                job_id,
                token,
                engine_id,
                reason="engine_process_failed",
                detail=f"engine process exited with code {rc}",
            )
    for job_id in finished:
        running.pop(job_id, None)


def main() -> int:
    _setup_logging()

    docker_only = os.environ.get("ENGINE_DOCKER_ONLY", "").strip().lower()
    if docker_only in {"1", "true", "yes", "on"} and not CONTAINER_MARKER.exists():
        logger.info("ENGINE_DOCKER_ONLY is enabled; host poller exits without claiming jobs")
        return 0

    base_url = _require_env("BACKEND_BASE_URL")
    token = _require_env("ENGINE_SHARED_TOKEN")
    engine_id = os.environ.get("ENGINE_ID", "ubuntu-engine-01").strip() or "ubuntu-engine-01"
    callback_url = f"{base_url.rstrip('/')}/get-results"
    try:
        poll_interval = max(5, int(os.environ.get("POLL_INTERVAL_SEC", "15")))
    except ValueError:
        poll_interval = 15
    try:
        fetch_limit = max(1, int(os.environ.get("POLL_FETCH_LIMIT", "5")))
    except ValueError:
        fetch_limit = 5

    logger.info(
        "poller starting: base=%s engine_id=%s interval=%ss limit=%s",
        base_url, engine_id, poll_interval, fetch_limit,
    )

    stop = threading.Event()

    def _signal_handler(signum, _frame):
        logger.info("received signal %s, stopping", signum)
        stop.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    running: dict[str, subprocess.Popen] = {}

    while not stop.is_set():
        try:
            _reaper(running, base_url, token, engine_id)
            jobs = _fetch_pending(base_url, token, engine_id, fetch_limit)
            for job in jobs:
                job_id = str(job.get("job_id"))
                if job_id in running:
                    continue
                if not _claim(base_url, job_id, token, engine_id):
                    continue
                proc = _spawn_pipeline(job, callback_url, token)
                if proc is not None:
                    running[job_id] = proc
                else:
                    _report_failure(
                        base_url,
                        job_id,
                        token,
                        engine_id,
                        reason="engine_start_failed",
                        detail="poller could not spawn local pipeline process",
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception("poller loop error: %s", exc)

        stop.wait(poll_interval)

    logger.info("waiting for %s running pipelines to finish...", len(running))
    deadline = time.time() + 60
    while running and time.time() < deadline:
        _reaper(running, base_url, token, engine_id)
        if running:
            time.sleep(2)

    if running:
        logger.warning("leaving %s pipelines still running on exit: %s",
                       len(running), list(running.keys()))

    logger.info("poller stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
