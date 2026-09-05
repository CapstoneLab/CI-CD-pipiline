from app import callback
from app.models import PipelineRun, PipelineStep


def _pipeline() -> tuple[PipelineRun, PipelineStep]:
    clone = PipelineStep(
        step_name="clone",
        status="success",
        started_at="2026-08-11T10:00:00+09:00",
        finished_at="2026-08-11T10:00:02+09:00",
        exit_code=0,
        summary_message="clone complete",
    )
    build = PipelineStep(step_name="build")
    run = PipelineRun(
        run_id="run-001",
        repo_url="https://github.com/example/repo.git",
        branch="main",
        status="running",
        steps=[clone, build],
    )
    return run, clone


def test_step_callback_contains_progress_delivery_id_and_logs() -> None:
    pipeline_run, step = _pipeline()

    payload = callback.build_step_callback_payload(
        job_id="job-001",
        repo_url=pipeline_run.repo_url,
        branch="main",
        pipeline_run=pipeline_run,
        step=step,
        step_log=["cloning", "done"],
        deployment={"status": "success", "url": "https://deploy.example/repo"},
    )

    assert payload["type"] == "step_complete"
    assert payload["step"]["step_order"] == 1
    assert payload["step"]["total_steps"] == 2
    assert payload["step"]["logs"] == ["cloning", "done"]
    assert len(payload["step"]["delivery_id"]) == 64
    assert payload["deployment"]["url"] == "https://deploy.example/repo"

    repeated = callback.build_step_callback_payload(
        job_id="job-001",
        repo_url=pipeline_run.repo_url,
        branch="main",
        pipeline_run=pipeline_run,
        step=step,
        step_log=["cloning", "done"],
    )
    assert repeated["step"]["delivery_id"] == payload["step"]["delivery_id"]


def test_step_callback_retries_transient_failures(monkeypatch) -> None:
    attempts = []

    def fake_post_once(**_kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            return False, "temporary failure"
        return True, "200"

    monkeypatch.setattr(callback, "_post_once", fake_post_once)
    monkeypatch.setattr(callback.time, "sleep", lambda _delay: None)

    ok, detail = callback.post_step_callback(
        callback_url="http://backend:8000/get-results",
        callback_token="shared",
        payload={"job_id": "job-001"},
        retry_delays_sec=[0, 0],
    )

    assert ok is True
    assert len(attempts) == 3
    assert "3 attempt(s)" in detail
