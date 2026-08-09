from pathlib import Path

from app.models import PipelineRun, PipelineStep, StepRunResult
from app.orchestrator import LocalOrchestrator
from app.utils.filesystem import prepare_run_paths


def test_step_logs_use_configured_data_root(monkeypatch, tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    mounted_root = tmp_path / "mounted-data"
    engine_dir.mkdir()
    monkeypatch.setenv("CICD_ENGINE_DATA_DIR", str(mounted_root))

    run_id = "run-20260809-001"
    paths = prepare_run_paths(engine_dir, run_id)
    step = PipelineStep(step_name="clone")
    pipeline_run = PipelineRun(
        run_id=run_id,
        repo_url="https://github.com/example/repo.git",
        branch="main",
        steps=[step],
    )
    orchestrator = LocalOrchestrator(base_dir=engine_dir)
    monkeypatch.setattr(
        orchestrator,
        "_execute_step",
        lambda **_kwargs: StepRunResult(
            status="success",
            exit_code=0,
            summary_message="clone complete",
        ),
    )

    result = orchestrator._run_and_record_step(
        pipeline_run=pipeline_run,
        step=step,
        repo_url=pipeline_run.repo_url,
        branch=pipeline_run.branch,
        repo_dir=paths["repo_dir"],
        run_dir=paths["run_dir"],
        logs_dir=paths["logs_dir"],
        step_definition=None,
    )

    assert result.status == "success"
    assert step.log_file == f"runs/{run_id}/logs/clone.log"

    log_file = mounted_root / step.log_file
    assert log_file.is_file()
    log_text = log_file.read_text(encoding="utf-8")
    assert "[step_status] success" in log_text
    assert "[step_summary] clone complete" in log_text
