from pathlib import Path
from unittest.mock import Mock

from app import poller


def test_docker_only_mode_skips_host_poller(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ENGINE_DOCKER_ONLY", "true")
    monkeypatch.setattr(poller, "CONTAINER_MARKER", tmp_path / "missing-dockerenv")

    assert poller.main() == 0


def test_report_failure_posts_engine_failure_payload(monkeypatch) -> None:
    calls = []

    def fake_http_json(method, url, *, token, engine_id, payload=None, timeout_sec=10):
        calls.append((method, url, token, engine_id, payload, timeout_sec))
        return 200, {"status": "failed"}, ""

    monkeypatch.setattr(poller, "_http_json", fake_http_json)

    poller._report_failure(
        "http://backend:8000",
        "job-1",
        "shared",
        "engine-1",
        reason="engine_start_failed",
        detail="spawn failed",
    )

    assert calls == [
        (
            "POST",
            "http://backend:8000/api/jobs/job-1/fail",
            "shared",
            "engine-1",
            {"reason": "engine_start_failed", "detail": "spawn failed"},
            10,
        )
    ]


def test_normalize_selected_items_accepts_backend_payload_shapes() -> None:
    assert poller._normalize_selected_items("CWE-89, sql-injection") == [
        "CWE-89",
        "sql-injection",
    ]
    assert poller._normalize_selected_items(
        [{"cwe": "CWE-79"}, {"key": "broken-access-control"}, "CWE-89"]
    ) == ["CWE-79", "broken-access-control", "CWE-89"]


def test_spawn_pipeline_forwards_job_contract_without_logging_token(
    monkeypatch, tmp_path: Path
) -> None:
    engine_dir = tmp_path / "engine"
    data_dir = tmp_path / "data"
    engine_dir.mkdir()
    (engine_dir / "main.py").touch()
    monkeypatch.setattr(poller, "BASE_DIR", engine_dir)
    monkeypatch.setenv("CICD_ENGINE_DATA_DIR", str(data_dir))

    process = Mock(pid=1234)
    popen = Mock(return_value=process)
    monkeypatch.setattr(poller.subprocess, "Popen", popen)

    token = "github-secret-token"
    result = poller._spawn_pipeline(
        {
            "job_id": "job-1",
            "repo_url": "https://github.com/example/private.git",
            "branch": "main",
            "repo_token": token,
            "selected_items": ["CWE-89"],
            "approved_cwes": ["CWE-79"],
            "commit_sha": "abc123",
        },
        "http://backend:8000/get-results",
        "engine-shared-secret",
    )

    assert result is process
    command = popen.call_args.args[0]
    assert command[command.index("--repo-token") + 1] == token
    assert command[command.index("--callback-url") + 1] == "http://backend:8000/get-results"
    assert command[command.index("--callback-token") + 1] == "engine-shared-secret"
    assert command[command.index("--selected-items") + 1] == "CWE-89"
    assert command[command.index("--approved-cwes") + 1] == "CWE-79"
    assert command[command.index("--commit") + 1] == "abc123"

    spawn_log = data_dir / "runs" / "job-job-1.log"
    assert token not in spawn_log.read_text(encoding="utf-8")
    assert "engine-shared-secret" not in spawn_log.read_text(encoding="utf-8")
    assert "****" in spawn_log.read_text(encoding="utf-8")
