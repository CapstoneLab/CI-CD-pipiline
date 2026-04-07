# Local CI Engine MVP

Single-machine local CI engine with repository-specific YAML workflows.

## Scope

- Bootstrap clone + dynamic workflow execution
- Built-in steps: install, lightweight_security_scan, test, deep_security_scan, build
- Custom command steps via YAML run
- Per-step continue_on_failure policy
- Persist logs and JSON artifacts under runs/

## Prerequisites

- git
- Node.js + npm
- gitleaks
- semgrep

## Usage

Run with explicit branch:

python main.py --repo https://github.com/example/repo.git --branch main

Run with Windows callback delivery:

python main.py --job-id <uuid> --repo https://github.com/example/repo.git --branch main --callback-url https://windows-host/get-results --callback-token <shared-token>

Run with repository default branch:

python main.py --repo https://github.com/example/repo.git

Run with explicit workflow file:

python main.py --repo https://github.com/example/repo.git --workflow .localci/workflow.yml

If --workflow points to a missing relative YAML path, the engine creates a workflow file from the common template after clone and runs it.

Relative workflow paths are resolved in this order:

- cloned repository root
- engine root

Automatic workflow discovery inside cloned repository:

- .localci/workflow.yml
- .localci/workflow.yaml
- .ci/workflow.yml
- .ci/workflow.yaml

If no workflow file is found, the engine falls back to the built-in default workflow.

Current fallback behavior:

- clone is always executed first
- if no workflow file is found after clone, the engine generates .localci/workflow.yml from workflow.template.yml and executes it

## Workflow YAML format

Minimal example:

name: project-ci
runtime:
	type: node
steps:
	- name: install
		uses: install
	- name: gitleaks
		uses: lightweight_security_scan
		continue_on_failure: true
	- name: unit-test
		uses: test
	- name: semgrep
		uses: deep_security_scan
	- name: build
		uses: build

Command step example:

steps:
	- name: lint
		run: ["npm", "run", "lint"]
		cwd: .
		env:
			CI: "true"
		continue_on_failure: true

Built-in step ids:

- install
- lightweight_security_scan
- test
- deep_security_scan
- build

Workflow step fields:

- name: visible step name in pipeline result
- uses: built-in step id
- run: custom command (string or list)
- cwd: working directory relative to cloned repo (default: .)
- env: extra environment variables
- continue_on_failure: whether pipeline continues when this step fails
- args: built-in step options
	- lightweight_security_scan args.report_file
	- deep_security_scan args.report_file

Reference file: workflow.example.yml

## Output

Each run generates:

- runs/run-YYYYMMDD-XXX/pipeline_result.json
- runs/run-YYYYMMDD-XXX/security_summary.json
- runs/run-YYYYMMDD-XXX/security_findings.json
- runs/run-YYYYMMDD-XXX/callback_result.json (when callback_url is set)
- runs/run-YYYYMMDD-XXX/callback_delivery.json (callback transmission status)
- runs/run-YYYYMMDD-XXX/logs/*.log
- runs/run-YYYYMMDD-XXX/artifacts/** (build outputs)

Test step policy:

- If no test files exist in repository, test step is skipped.
- If test files exist but package.json test script is missing/placeholder, test step is skipped.

Build step policy:

- Build must generate deployable artifacts in known output paths.
- If build command succeeds but no artifacts are found, build step is marked failed.

Cloned repository workspace:

- workspace/run-YYYYMMDD-XXX/repo

## Note about juice-shop

https://github.com/juice-shop/juice-shop is a Node repository and is now supported by this MVP.
