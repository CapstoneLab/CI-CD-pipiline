# Local CI Engine MVP

Single-machine local CI MVP for Node repositories.

## Scope

- Node.js runtime
- Clone -> Install -> gitleaks -> Test -> semgrep -> Build
- Stop immediately on failure
- Persist logs and JSON artifacts under runs/

## Prerequisites

- git
- Node.js + npm
- gitleaks
- semgrep

## Usage

Run with explicit branch:

python main.py --repo https://github.com/example/repo.git --branch main

Run with repository default branch:

python main.py --repo https://github.com/example/repo.git

## Output

Each run generates:

- runs/run-YYYYMMDD-XXX/pipeline_result.json
- runs/run-YYYYMMDD-XXX/security_summary.json
- runs/run-YYYYMMDD-XXX/security_findings.json
- runs/run-YYYYMMDD-XXX/logs/*.log

Cloned repository workspace:

- workspace/run-YYYYMMDD-XXX/repo

## Note about juice-shop

https://github.com/juice-shop/juice-shop is a Node repository and is now supported by this MVP.
