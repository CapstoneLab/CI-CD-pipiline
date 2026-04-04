# 로컬 CI 엔진 MVP 문서

## 구현 업데이트 메모 (2026-04-04)

- 현재 코드 구현은 Node 레포 기준으로 동작한다.
- install: npm ci (lockfile 없으면 npm install)
- test: npm test (테스트 없으면 skipped)
- build: npm run build

## 1. 문서 목적

이 문서의 목적은 자체 CI/CD 엔진 개발의 첫 단계로, 로컬 환경에서 작게 시작하는 CI MVP의 범위와 구현 순서를 정의하는 것이다.

이번 MVP는 실제 서비스 배포나 운영 환경 연결이 아니라, 아래 핵심 흐름이 한 대의 로컬 머신에서 안정적으로 실행되는지 검증하는 데 목적이 있다.

clone -> install -> lightweight security scan (gitleaks) -> test (없으면 skip) -> deep security scan (semgrep) -> build

이번 단계에서 의도적으로 제외하는 항목:

- DB 저장
- 웹 UI
- FastAPI 서버
- 분산 Runner
- GitHub OAuth
- GitHub webhook
- CD/배포
- 다중 언어 지원
- private repo 인증

핵심 목표:

Python 3.11.9 환경에서 public Python repo 하나를 받아 CI 파이프라인을 로컬에서 끝까지 실행한다.

## 2. MVP 범위

### 2.1 포함 범위

- 입력
  - GitHub Repository URL
  - branch 이름
- 지원 언어
  - Python만 지원
  - Python 버전 3.11.9 고정
- 실행 단계
  - clone
  - install
  - lightweight_security_scan (gitleaks)
  - test (pytest, 테스트 없으면 skip)
  - deep_security_scan (semgrep)
  - build
- 출력
  - 콘솔 로그
  - 파일 로그
  - 단계별 실행 결과
  - 보안 검사 요약
  - 전체 파이프라인 최종 상태

입력 예시:

- repo_url = https://github.com/user/repo.git
- branch = main

### 2.2 제외 범위

- 데이터베이스 저장
- 상태 조회 API
- 로그 조회 API
- 보안 리포트 API
- Docker 기반 Runner 분리
- 다중 사용자
- 멀티 파이프라인 동시 실행
- 자동 재시도
- 배포/URL 반환
- project DB 생성
- migration
- 환경변수 주입
- health check

이번 단계의 성공 정의는 단 하나다: 엔진이 실제로 로컬에서 돈다.

## 3. 성공 기준

### 3.1 기능 성공 기준

CLI 명령 실행 시 다음이 수행되어야 한다.

1. repo clone 성공
2. Python 프로젝트 판별
3. 의존성 설치
4. gitleaks 실행
5. 테스트가 있으면 pytest 실행
6. 테스트가 없으면 skip 처리
7. semgrep 실행
8. build 실행
9. 각 단계 성공/실패가 콘솔과 파일에 기록
10. 최종 성공/실패 결과 출력

### 3.2 품질 성공 기준

- 실패한 단계에서 즉시 중단
- 로그로 실패 단계 식별 가능
- 보안 검사 결과가 요약 형태로 저장
- 이후 DB/API 확장에 유리한 구조

## 4. 핵심 설계 원칙

### 4.1 최대한 작게 시작

최초 성공 시나리오를 하나로 고정한다.

public Python repo 하나를 입력받아 clone -> install -> gitleaks -> test -> semgrep -> build를 로컬에서 수행

### 4.2 DB 대신 메모리 + 파일 저장

- 실행 중 상태: 메모리 객체
- 실행 후 결과: JSON 파일 + 로그 파일

예시 구조:

runs/
  run-20260404-001/
    pipeline_result.json
    logs/
      clone.log
      install.log
      lightweight_security_scan.log
      test.log
      deep_security_scan.log
      build.log

### 4.3 단계 이름과 상태를 사전 고정

권장 step 이름:

- clone
- install
- lightweight_security_scan
- test
- deep_security_scan
- build

권장 step status:

- pending
- running
- success
- failed
- skipped

권장 pipeline status:

- queued
- running
- success
- failed
- cancelled

## 5. 로컬 MVP 아키텍처

User CLI
  -> Local Orchestrator
  -> Step Executor
      - Git
      - pip
      - gitleaks
      - pytest
      - semgrep
      - build command
  -> Logs + JSON Result

구성 요소:

1. CLI
   - 진입점
   - 예: python main.py --repo https://github.com/user/repo.git --branch main

2. Local Orchestrator
   - run 생성
   - workspace 생성
   - step 순서 실행
   - 실패 시 중단
   - 최종 결과 정리

3. Step Executor
   - shell command 실행
   - stdout/stderr 수집
   - exit code 수집
   - 결과 파일 저장

4. Result Writer
   - JSON 및 로그 파일 저장

## 6. 권장 디렉토리 구조

ci_mvp/
├─ main.py
├─ requirements.txt
├─ README.md
├─ app/
│  ├─ orchestrator.py
│  ├─ models.py
│  ├─ constants.py
│  ├─ utils/
│  │  ├─ shell.py
│  │  ├─ filesystem.py
│  │  └─ logger.py
│  ├─ steps/
│  │  ├─ clone.py
│  │  ├─ install.py
│  │  ├─ lightweight_security.py
│  │  ├─ test.py
│  │  ├─ deep_security.py
│  │  └─ build.py
│  └─ scanners/
│     ├─ gitleaks_parser.py
│     └─ semgrep_parser.py
├─ runs/
└─ workspace/

## 7. 실행 환경 기준

### 7.1 Python 버전

- Python 3.11.9

### 7.2 로컬 필수 설치 도구

- git
- Python 3.11.9
- pip
- gitleaks
- semgrep
- docker 또는 대체 build 도구

### 7.3 Python 패키지 예시

- pydantic
- typer 또는 argparse
- rich
- pytest
- dataclasses-json 또는 기본 json

초기 버전은 argparse + dataclass + subprocess 조합으로도 충분하다.

## 8. 데이터 모델 설계 (DB 없는 버전)

### 8.1 PipelineRun

```json
{
  "run_id": "run-20260404-001",
  "repo_url": "https://github.com/user/repo.git",
  "branch": "main",
  "runtime_type": "python",
  "python_version": "3.11.9",
  "status": "running",
  "current_step": "test",
  "started_at": "2026-04-04T10:00:00",
  "finished_at": null,
  "steps": []
}
```

### 8.2 PipelineStep

```json
{
  "step_name": "install",
  "status": "success",
  "started_at": "2026-04-04T10:01:00",
  "finished_at": "2026-04-04T10:02:10",
  "exit_code": 0,
  "summary_message": "requirements installed",
  "log_file": "runs/run-20260404-001/logs/install.log"
}
```

### 8.3 SecuritySummary

```json
{
  "scanner_name": "semgrep",
  "scan_type": "deep",
  "critical_count": 0,
  "high_count": 1,
  "medium_count": 2,
  "low_count": 3,
  "max_detected_severity": "high"
}
```

### 8.4 SecurityFinding

```json
{
  "scanner_name": "semgrep",
  "rule_id": "python.lang.security.audit.eval-use.eval-use",
  "severity": "high",
  "title": "Use of eval detected",
  "file_path": "app/main.py",
  "line_number": 42,
  "message": "Detected use of eval()"
}
```

## 9. 단계별 상세 설계

### 9.1 clone

- 목적: 입력받은 GitHub repo를 workspace에 clone
- 입력: repo_url, branch
- 처리
  - run별 workspace 생성
  - git clone
  - git checkout branch
- 성공 조건
  - repo clone 성공
  - branch checkout 성공
- 실패 조건
  - 잘못된 URL
  - branch 없음
  - git clone 실패

로그 예시:

- [clone] cloning repository...
- [clone] checkout main success

### 9.2 install

- 목적: Python 의존성 설치
- Python 프로젝트 판별 기준
  - requirements.txt
  - pyproject.toml
  - setup.py
  - Pipfile
- MVP 우선 정책
  - requirements.txt 기반만 지원
  - requirements.txt 없으면 실패 처리
- 처리
  - pip install -r requirements.txt
- 성공 조건: pip exit code 0
- 실패 조건
  - requirements 없음
  - install 실패

### 9.3 lightweight_security_scan

- 목적: secret 노출 여부를 빠르게 검사
- 도구: gitleaks
- 처리
  - 레포 전체 스캔
  - JSON 결과 저장
  - 결과 요약 생성
- 권장 정책
  - secret 1건 이상 발견 시 즉시 fail
- 성공 조건: 검출 0건
- 실패 조건
  - secret 검출
  - gitleaks 실행 실패

### 9.4 test

- 목적: 최소 기능 동작 검증
- 도구: pytest
- 테스트 존재 기준
  - tests/ 디렉토리
  - test_*.py
  - *_test.py
- 정책
  - 테스트 있으면 pytest 실행
  - 없으면 skipped
- 성공 조건: pytest exit code 0
- 실패 조건: 테스트 실행 실패
- skip 조건: 테스트 파일/디렉토리 없음

### 9.5 deep_security_scan

- 목적: 정밀 보안 검사
- 도구: semgrep
- 처리
  - semgrep JSON 생성
  - severity 집계
  - finding 파싱
  - 보안 요약 생성
- 권장 정책
  - critical/high 발견 시 fail
  - medium/low는 기록 후 통과 가능
- 성공 조건: 임계치 초과 취약점 없음
- 실패 조건
  - critical/high 발견
  - semgrep 실행 실패

### 9.6 build

- 목적: 빌드 가능성 검증
- 1차 권장 정의
  - python -m compileall .
- 2차 확장
  - Dockerfile 존재 시 docker build
- MVP 추천
  - 초기에는 compile 기반 build를 기본 채택

## 10. 상태 전이 규칙

파이프라인 상태 전이:

queued -> running -> success 또는 failed

단계 상태:

- pending
- running
- success
- failed
- skipped

실패 규칙:

- 한 단계라도 failed면 파이프라인 failed
- test만 skipped 가능
- lightweight_security_scan 실패 시 즉시 중단
- deep_security_scan 실패 시 즉시 중단
- build 실패 시 최종 failed

## 11. 결과 저장 방식

파일 구조:

runs/
  run-20260404-001/
    pipeline_result.json
    security_summary.json
    security_findings.json
    logs/
      clone.log
      install.log
      lightweight_security_scan.log
      test.log
      deep_security_scan.log
      build.log

pipeline_result.json 예시:

```json
{
  "run_id": "run-20260404-001",
  "status": "failed",
  "current_step": "deep_security_scan",
  "steps": [
    {"step_name": "clone", "status": "success"},
    {"step_name": "install", "status": "success"},
    {"step_name": "lightweight_security_scan", "status": "success"},
    {"step_name": "test", "status": "skipped"},
    {"step_name": "deep_security_scan", "status": "failed"},
    {"step_name": "build", "status": "pending"}
  ]
}
```

## 12. 구현 순서

1. 기준 고정
   - step 이름
   - status 값
   - 실패 정책
   - build 정의
   - test skip 정책
   - Python 지원 범위

2. CLI 진입점 구현
   - python main.py --repo https://github.com/user/repo.git --branch main
   - 인자 파싱
   - run_id 생성

3. orchestrator 기본 구현
   - run/workspace 생성
   - step 순차 실행
   - 결과 수집
   - 실패 시 중단
   - 최종 결과 출력

4. clone step 구현
5. install step 구현
6. gitleaks step 구현
7. test step 구현
8. semgrep step 구현
9. build step 구현
10. 결과 파일 저장 정리

## 13. 추천 1차 개발 범위

더 작게 시작하는 경우:

- 1차 목표: clone -> install -> test
- 2차 목표: + gitleaks, + semgrep
- 3차 목표: + build, + JSON 결과 파일 정리

## 14. 권장 MVP v1 정의

Python 3.11.9 환경에서, 사용자가 입력한 public GitHub Python repository를 로컬에 clone하고, requirements.txt 기반 의존성 설치 후, gitleaks와 semgrep으로 보안 검사를 수행하고, pytest 테스트를 실행하되 테스트가 없으면 skip하며, 마지막으로 compile 기반 build 검증까지 수행한 뒤, 단계별 로그와 JSON 결과 파일을 남기는 단일 머신 CI 엔진.
