from __future__ import annotations

from pathlib import Path

from app.constants import RUNTIME_TYPE, STEP_NAMES
from app.models import PipelineRun, PipelineStep, SecurityFinding, SecuritySummary, StepRunResult, now_iso
from app.steps.build import run_build
from app.steps.clone import run_clone
from app.steps.deep_security import run_deep_security_scan
from app.steps.install import run_install
from app.steps.lightweight_security import run_lightweight_security_scan
from app.steps.test import run_test
from app.utils.filesystem import make_run_id, prepare_run_paths, save_json


class LocalOrchestrator:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def run(self, repo_url: str, branch: str | None) -> tuple[PipelineRun, Path]:
        run_id = make_run_id(self.base_dir)
        paths = prepare_run_paths(base_dir=self.base_dir, run_id=run_id)
        run_dir = paths["run_dir"]
        logs_dir = paths["logs_dir"]
        repo_dir = paths["repo_dir"]

        pipeline_run = PipelineRun(
            run_id=run_id,
            repo_url=repo_url,
            branch=branch,
            runtime_type=RUNTIME_TYPE,
            steps=[PipelineStep(step_name=name) for name in STEP_NAMES],
        )

        security_summaries: list[SecuritySummary] = []
        security_findings: list[SecurityFinding] = []
        has_failure = False
        continue_on_failure_steps = {"lightweight_security_scan"}

        self._write_pipeline_result(run_dir, pipeline_run)

        pipeline_run.status = "running"
        pipeline_run.started_at = now_iso()
        self._write_pipeline_result(run_dir, pipeline_run)

        for step in pipeline_run.steps:
            pipeline_run.current_step = step.step_name
            step.status = "running"
            step.started_at = now_iso()
            step.log_file = str((logs_dir / f"{step.step_name}.log").relative_to(self.base_dir))
            self._write_pipeline_result(run_dir, pipeline_run)

            try:
                result = self._execute_step(
                    step_name=step.step_name,
                    repo_url=repo_url,
                    branch=branch,
                    repo_dir=repo_dir,
                    run_dir=run_dir,
                    logs_dir=logs_dir,
                )
            except Exception as exc:  # noqa: BLE001
                result = StepRunResult(
                    status="failed",
                    exit_code=1,
                    summary_message=f"Unhandled exception: {exc}",
                )

            step.finished_at = now_iso()
            step.status = result.status
            step.exit_code = result.exit_code
            step.summary_message = result.summary_message

            if result.security_summary:
                security_summaries.append(result.security_summary)
            if result.security_findings:
                security_findings.extend(result.security_findings)

            self._write_pipeline_result(run_dir, pipeline_run)

            if result.status == "failed":
                has_failure = True
                if step.step_name not in continue_on_failure_steps:
                    pipeline_run.status = "failed"
                    pipeline_run.finished_at = now_iso()
                    pipeline_run.current_step = step.step_name
                    self._write_security_results(run_dir, security_summaries, security_findings)
                    self._write_pipeline_result(run_dir, pipeline_run)
                    return pipeline_run, run_dir

        pipeline_run.status = "failed" if has_failure else "success"
        pipeline_run.finished_at = now_iso()
        pipeline_run.current_step = None
        self._write_security_results(run_dir, security_summaries, security_findings)
        self._write_pipeline_result(run_dir, pipeline_run)
        return pipeline_run, run_dir

    def _execute_step(
        self,
        step_name: str,
        repo_url: str,
        branch: str | None,
        repo_dir: Path,
        run_dir: Path,
        logs_dir: Path,
    ) -> StepRunResult:
        log_file = logs_dir / f"{step_name}.log"

        if step_name == "clone":
            return run_clone(repo_url=repo_url, branch=branch, repo_dir=repo_dir, log_file=log_file)
        if step_name == "install":
            return run_install(repo_dir=repo_dir, log_file=log_file)
        if step_name == "lightweight_security_scan":
            return run_lightweight_security_scan(
                repo_dir=repo_dir,
                log_file=log_file,
                report_file=run_dir / "gitleaks_report.json",
            )
        if step_name == "test":
            return run_test(repo_dir=repo_dir, log_file=log_file)
        if step_name == "deep_security_scan":
            return run_deep_security_scan(
                repo_dir=repo_dir,
                log_file=log_file,
                report_file=run_dir / "semgrep_report.json",
            )
        if step_name == "build":
            return run_build(repo_dir=repo_dir, log_file=log_file)

        return StepRunResult(status="failed", exit_code=1, summary_message=f"Unknown step: {step_name}")

    @staticmethod
    def _write_pipeline_result(run_dir: Path, pipeline_run: PipelineRun) -> None:
        save_json(run_dir / "pipeline_result.json", pipeline_run.to_dict())

    @staticmethod
    def _write_security_results(
        run_dir: Path,
        summaries: list[SecuritySummary],
        findings: list[SecurityFinding],
    ) -> None:
        save_json(run_dir / "security_summary.json", [item.to_dict() for item in summaries])
        save_json(run_dir / "security_findings.json", [item.to_dict() for item in findings])
