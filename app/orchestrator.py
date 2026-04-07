from __future__ import annotations

from pathlib import Path

from app.constants import RUNTIME_TYPE
from app.models import PipelineRun, PipelineStep, SecurityFinding, SecuritySummary, StepRunResult, now_iso
from app.steps.build import run_build
from app.steps.clone import run_clone
from app.steps.deep_security import run_deep_security_scan
from app.steps.install import run_install
from app.steps.lightweight_security import run_lightweight_security_scan
from app.steps.test import run_test
from app.utils.filesystem import make_run_id, prepare_run_paths, save_json
from app.utils.logger import append_log
from app.utils.shell import run_command
from app.workflow import WorkflowStepDefinition, resolve_workflow_definition


class LocalOrchestrator:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def run(self, repo_url: str, branch: str | None, workflow_path: str | None = None) -> tuple[PipelineRun, Path]:
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
            steps=[PipelineStep(step_name="clone")],
        )

        security_summaries: list[SecuritySummary] = []
        security_findings: list[SecurityFinding] = []
        has_failure = False

        self._write_pipeline_result(run_dir, pipeline_run)

        pipeline_run.status = "running"
        pipeline_run.started_at = now_iso()
        self._write_pipeline_result(run_dir, pipeline_run)

        clone_step = pipeline_run.steps[0]
        clone_result = self._run_and_record_step(
            pipeline_run=pipeline_run,
            step=clone_step,
            repo_url=repo_url,
            branch=branch,
            repo_dir=repo_dir,
            run_dir=run_dir,
            logs_dir=logs_dir,
            step_definition=None,
        )

        if clone_result.status == "failed":
            pipeline_run.status = "failed"
            pipeline_run.finished_at = now_iso()
            pipeline_run.current_step = clone_step.step_name
            self._write_security_results(run_dir, security_summaries, security_findings)
            self._write_pipeline_result(run_dir, pipeline_run)
            return pipeline_run, run_dir

        try:
            workflow = resolve_workflow_definition(
                repo_dir=repo_dir,
                base_dir=self.base_dir,
                workflow_path=workflow_path,
            )
        except Exception as exc:  # noqa: BLE001
            resolve_step = PipelineStep(step_name="resolve_workflow")
            pipeline_run.steps.append(resolve_step)
            self._record_step_result(
                pipeline_run=pipeline_run,
                run_dir=run_dir,
                step=resolve_step,
                result=StepRunResult(
                    status="failed",
                    exit_code=1,
                    summary_message=f"Workflow resolution failed: {exc}",
                ),
            )
            pipeline_run.status = "failed"
            pipeline_run.finished_at = now_iso()
            pipeline_run.current_step = resolve_step.step_name
            self._write_security_results(run_dir, security_summaries, security_findings)
            self._write_pipeline_result(run_dir, pipeline_run)
            return pipeline_run, run_dir

        pipeline_run.runtime_type = workflow.runtime_type
        pipeline_run.workflow_name = workflow.name
        pipeline_run.workflow_source = workflow.source

        workflow_steps = [
            PipelineStep(
                step_name=step_definition.name,
                continue_on_failure=step_definition.continue_on_failure,
            )
            for step_definition in workflow.steps
        ]
        pipeline_run.steps.extend(workflow_steps)
        self._write_pipeline_result(run_dir, pipeline_run)

        for step, step_definition in zip(workflow_steps, workflow.steps):
            result = self._run_and_record_step(
                pipeline_run=pipeline_run,
                step=step,
                repo_url=repo_url,
                branch=branch,
                repo_dir=repo_dir,
                run_dir=run_dir,
                logs_dir=logs_dir,
                step_definition=step_definition,
            )

            if result.security_summary:
                security_summaries.append(result.security_summary)
            if result.security_findings:
                security_findings.extend(result.security_findings)

            if result.status == "failed":
                has_failure = True
                if not step.continue_on_failure:
                    self._mark_remaining_steps_skipped(
                        pipeline_run=pipeline_run,
                        remaining_steps=workflow_steps[workflow_steps.index(step) + 1 :],
                        reason=f"Skipped because previous step failed: {step.step_name}",
                    )
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

    def _run_and_record_step(
        self,
        pipeline_run: PipelineRun,
        step: PipelineStep,
        repo_url: str,
        branch: str | None,
        repo_dir: Path,
        run_dir: Path,
        logs_dir: Path,
        step_definition: WorkflowStepDefinition | None,
    ) -> StepRunResult:
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
                step_definition=step_definition,
            )
        except Exception as exc:  # noqa: BLE001
            result = StepRunResult(
                status="failed",
                exit_code=1,
                summary_message=f"Unhandled exception: {exc}",
            )

        self._record_step_result(pipeline_run=pipeline_run, run_dir=run_dir, step=step, result=result)
        return result

    def _record_step_result(
        self,
        pipeline_run: PipelineRun,
        run_dir: Path,
        step: PipelineStep,
        result: StepRunResult,
    ) -> None:
        step.finished_at = now_iso()
        step.status = result.status
        step.exit_code = result.exit_code
        step.summary_message = result.summary_message

        if step.log_file:
            step_log_path = self.base_dir / step.log_file
            append_log(step_log_path, f"[step_status] {step.status}")
            append_log(step_log_path, f"[step_summary] {step.summary_message or 'no message'}")
            append_log(step_log_path, f"[step_exit_code] {step.exit_code if step.exit_code is not None else 'null'}")

        self._write_pipeline_result(run_dir, pipeline_run)

    @staticmethod
    def _mark_remaining_steps_skipped(
        pipeline_run: PipelineRun,
        remaining_steps: list[PipelineStep],
        reason: str,
    ) -> None:
        for step in remaining_steps:
            if step.status != "pending":
                continue
            step.status = "skipped"
            step.started_at = step.started_at or now_iso()
            step.finished_at = now_iso()
            step.exit_code = 0
            step.summary_message = reason

    def _execute_step(
        self,
        step_name: str,
        repo_url: str,
        branch: str | None,
        repo_dir: Path,
        run_dir: Path,
        logs_dir: Path,
        step_definition: WorkflowStepDefinition | None,
    ) -> StepRunResult:
        log_file = logs_dir / f"{step_name}.log"

        if step_name == "clone":
            return run_clone(repo_url=repo_url, branch=branch, repo_dir=repo_dir, log_file=log_file)

        if not step_definition:
            return StepRunResult(
                status="failed",
                exit_code=1,
                summary_message=f"Unknown step: {step_name}",
            )

        if step_definition.kind == "builtin":
            return self._execute_builtin_step(
                step_definition=step_definition,
                repo_dir=repo_dir,
                run_dir=run_dir,
                log_file=log_file,
            )

        if step_definition.kind == "command":
            return self._execute_command_step(
                step_definition=step_definition,
                repo_dir=repo_dir,
                log_file=log_file,
            )

        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message=f"Unsupported step kind: {step_definition.kind}",
        )

    def _execute_builtin_step(
        self,
        step_definition: WorkflowStepDefinition,
        repo_dir: Path,
        run_dir: Path,
        log_file: Path,
    ) -> StepRunResult:
        uses_name = step_definition.uses

        if uses_name == "install":
            return run_install(repo_dir=repo_dir, log_file=log_file)

        if uses_name == "lightweight_security_scan":
            report_name = _safe_report_file_name(step_definition.args.get("report_file"), "gitleaks_report.json")
            return run_lightweight_security_scan(
                repo_dir=repo_dir,
                log_file=log_file,
                report_file=run_dir / report_name,
            )

        if uses_name == "test":
            return run_test(repo_dir=repo_dir, log_file=log_file)

        if uses_name == "deep_security_scan":
            report_name = _safe_report_file_name(step_definition.args.get("report_file"), "semgrep_report.json")
            return run_deep_security_scan(
                repo_dir=repo_dir,
                log_file=log_file,
                report_file=run_dir / report_name,
            )

        if uses_name == "build":
            return run_build(
                repo_dir=repo_dir,
                log_file=log_file,
                artifacts_dir=run_dir / "artifacts",
            )

        return StepRunResult(
            status="failed",
            exit_code=1,
            summary_message=f"Unknown built-in step: {uses_name}",
        )

    def _execute_command_step(
        self,
        step_definition: WorkflowStepDefinition,
        repo_dir: Path,
        log_file: Path,
    ) -> StepRunResult:
        try:
            working_dir = (repo_dir / step_definition.cwd).resolve()
            repo_root = repo_dir.resolve()
            if not working_dir.is_relative_to(repo_root):
                return StepRunResult(
                    status="failed",
                    exit_code=1,
                    summary_message=(
                        f"Step '{step_definition.name}' cwd escapes repository root: {step_definition.cwd}"
                    ),
                )

            if not working_dir.exists() or not working_dir.is_dir():
                return StepRunResult(
                    status="failed",
                    exit_code=1,
                    summary_message=f"Step '{step_definition.name}' cwd not found: {step_definition.cwd}",
                )

            command_result = run_command(
                command=step_definition.command,
                cwd=working_dir,
                log_file=log_file,
                env=step_definition.env or None,
            )
        except Exception as exc:  # noqa: BLE001
            return StepRunResult(
                status="failed",
                exit_code=1,
                summary_message=f"Command step '{step_definition.name}' failed before execution: {exc}",
            )

        if command_result.exit_code == 0:
            return StepRunResult(
                status="success",
                exit_code=0,
                summary_message=f"Command step succeeded: {' '.join(step_definition.command)}",
            )

        return StepRunResult(
            status="failed",
            exit_code=command_result.exit_code,
            summary_message=f"Command step failed: {' '.join(step_definition.command)}",
        )

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


def _safe_report_file_name(raw_value: object, default_name: str) -> str:
    if raw_value is None:
        return default_name

    candidate_name = Path(str(raw_value)).name.strip()
    if not candidate_name:
        return default_name

    return candidate_name
