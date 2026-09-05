from __future__ import annotations

from pathlib import Path

from app import log_stream
from app.log_stream import CallbackLogStreamer, LogEvent, build_log_batch_payload
from app.utils.logger import append_log


def _event(sequence: int, message: str = "line") -> LogEvent:
    return LogEvent(
        sequence=sequence,
        timestamp="2026-09-05T00:00:00+00:00",
        run_id="run-001",
        step_name="build",
        stream="stdout",
        message=message,
        log_file=Path("/tmp/run-001/logs/build.log"),
    )


def test_log_batch_contract_has_ordering_and_compatibility_lines() -> None:
    payload = build_log_batch_payload(
        job_id="job-001",
        repo_url="https://github.com/example/repo.git",
        branch="main",
        events=[_event(4, "first"), _event(5, "second")],
    )

    assert payload["type"] == "log_batch"
    assert payload["schema_version"] == 1
    assert payload["sequence_start"] == 4
    assert payload["sequence_end"] == 5
    assert payload["events"][0]["step_name"] == "build"
    assert payload["logs"] == [
        "[build.log] first",
        "[build.log] second",
    ]
    assert len(payload["event_id"]) == 64


def test_streamer_observes_append_log_and_flushes_on_close(monkeypatch, tmp_path: Path) -> None:
    delivered: list[dict] = []

    def fake_post(**kwargs):
        delivered.append(kwargs["payload"])
        return True, {"attempts": 1, "error": None, "http_status": "200"}

    monkeypatch.setattr(log_stream, "post_callback_with_retry", fake_post)
    log_file = tmp_path / "run-001" / "logs" / "test.log"

    with CallbackLogStreamer(
        callback_url="http://backend/get-results",
        callback_token="secret",
        job_id="job-001",
        repo_url="https://github.com/example/repo.git",
        branch="main",
        flush_interval_sec=60,
    ):
        append_log(log_file, "hello", echo=False)
        append_log(log_file, "world", echo=False)

    assert len(delivered) == 1
    assert delivered[0]["run_id"] == "run-001"
    assert [event["message"].split("] ", 1)[-1] for event in delivered[0]["events"]] == [
        "hello",
        "world",
    ]
