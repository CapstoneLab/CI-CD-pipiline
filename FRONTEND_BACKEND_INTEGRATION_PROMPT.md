# CICD-Engine ↔ 프론트엔드 연동 구현 프롬프트

> 기준일: 2026-08-09  
> 검증 기준: CICD-Engine의 2026-08-08 변경분, 현재 실행 중인 백엔드 이미지의 OpenAPI 및 라우트 구현  
> 운영 API base URL: `https://api.pwd.kr/capstonelab/capstone-back`

아래의 **복붙용 프롬프트**를 프론트엔드 저장소를 열은 코딩 에이전트에게 전달한다. 이 문서의 경로, 파일명, 라이브러리는 예시가 아니라 **현재 프론트 저장소를 먼저 조사한 후 해당 구조에 맞게 적용**해야 한다.

---

## 복붙용 프롬프트

```text
너는 기존 프론트엔드 제품에 CICD-Engine 기반 파이프라인 기능을 연동하는 시니어 프론트엔드 엔지니어다. 아래 계약은 실제 실행 중인 FastAPI 백엔드와 CICD-Engine 코드를 대조한 결과이다.

목표
- GitHub OAuth 로그인 → 저장소/브랜치 선택 → 보안 검사 항목 선택 → 파이프라인 생성 → 실행 상태/스텝/로그 추적 → 보안 결과 → 필요 시 승인/거부와 후속 job 추적까지 실제로 동작시켜라.
- 기존 UI, 라우팅, 상태관리, API client, 디자인 시스템을 유지하고 필요한 부분만 수정해라.
- mock, 임시 하드코딩, 가짜 지연, 가짜 성공 처리를 모두 제거하고 실제 API를 사용해라.

0. 먼저 할 일
1) package.json, 라우터, 전역 상태, API client, 인증 저장 방식, 현재 파이프라인/저장소/결과 화면을 먼저 조사해라.
2) 기존 변경을 덮어쓰지 말고, 기존 추상화와 컴포넌트를 재사용해라.
3) 현재 스택이 TypeScript를 사용하면 이 문서의 계약을 타입으로 정의해라. JavaScript 프로젝트면 JSDoc 또는 기존 검증 방식을 사용해라.
4) 기존 data-fetching 라이브러리가 있으면 그것을 사용하고, 이 연동만을 위해 새 대형 상태관리/네트워크 라이브러리를 추가하지 마라.
5) 조사 후 구현 범위와 변경 파일을 짧게 정리하고 바로 구현해라. 계약을 바꿔야 할 정도의 실제 충돌만 질문해라.

1. 아키텍처 경계
- 프론트는 백엔드만 호출한다.
- 프론트가 CICD-Engine 컨테이너, Docker socket, engine token, callback token, GitHub access token에 직접 접근하거나 전송하면 안 된다.
- `/api/jobs/pending`, `/api/jobs/{job_id}/claim`, `POST /get-results`는 엔진↔백엔드 내부용이므로 프론트에서 절대 호출하지 마라.
- private repository 토큰은 백엔드가 로그인 사용자에서 찾아 엔진에 `repo_token`으로 전달한다. 프론트 request body에 토큰을 넣지 마라.

전체 흐름
OAuth 로그인
  → GitHub 저장소/브랜치 조회
  → 보안 카탈로그 조회 및 검사 설정
  → POST /api/pipelines
  → job/steps/logs polling
  → 종료 시 GET /api/jobs/{job_id}/result
  → block_pending_approval이면 승인 request/approve/reject
  → 승인 시 반환된 followup_job_id로 동일한 추적 흐름 반복

2. API base URL과 client
- 운영: `https://api.pwd.kr/capstonelab/capstone-back`
- 로컬 백엔드: `http://127.0.0.1:8000`
- 코드에 URL을 하드코딩하지 말고, 현재 빌드 도구의 환경변수 규칙을 따라 `API_BASE_URL`을 생성해라. Vite면 `VITE_API_BASE_URL`, Next.js면 `NEXT_PUBLIC_API_BASE_URL` 등 기존 스택 규칙을 따라라.
- base URL의 마지막 `/`를 정규화해 `//api/...`가 되지 않게 해라. `/capstonelab/capstone-back` prefix를 누락하지 마라.
- JSON request에 `Accept: application/json`, body가 있을 때 `Content-Type: application/json`을 넣어라.
- JWT가 있으면 모든 `/api/*`, `/auth/me` request에 `Authorization: Bearer <jwt>`를 일관되게 넣어라. 현재 몇몇 GET이 서버에서 무인증으로 열려 있더라도 프론트는 무인증 사용을 전제하지 마라.
- HTTP non-2xx를 성공 payload로 처리하지 말고 공통 error로 변환해라.

공통 에러 형태
`{ error: string, message: string, detail?: unknown, existing_job_id?: string }`

단, FastAPI request validation 422는 `{ detail: Array<{loc, msg, type}> }` 형태일 수 있으므로 두 형태를 모두 처리해라. 사용자에게 raw JSON이나 stack trace를 노출하지 마라.

3. GitHub OAuth/JWT
- 로그인은 AJAX/fetch가 아니라 브라우저 top-level navigation으로 `${API_BASE_URL}/auth/github/login`에 이동해라.
- 백엔드 OAuth callback이 프론트의 인증 callback 라우트로 `?token=<JWT>`를 붙여 redirect한다.
- 인증 callback 화면은 `token`을 한 번만 읽고, 기존 auth store 규칙에 맞게 저장한 뒤 `history.replaceState` 또는 router replace로 URL에서 즉시 제거해라. 콘솔, analytics, error report에 토큰을 기록하지 마라.
- 기존 저장 규칙이 없으면 `sessionStorage`를 사용하고 key를 하나로 집중해라. localStorage와 sessionStorage에 중복 저장하지 마라.
- 저장 후 `GET /auth/me`로 토큰을 검증하고 사용자 정보를 로드해라.
- 401은 토큰 삭제 → auth state 초기화 → 로그인 화면 안내로 일관되게 처리해라. 무한 redirect loop를 만들지 마라.
- `/auth/me` 응답에서 사용할 필드: `id`, `github_id`, `github_login`, `display_name`, `avatar_url`, `email`, `source` 및 추가 GitHub profile 필드.

4. 실제 프론트용 API 계약

A. 저장소
`GET /api/repos` (Bearer 필수)

응답:
{
  "count": number,
  "orgs_detected": string[],
  "org_errors": Array<Record<string, string>>,
  "repos": GitHubRepository[]
}

`repos` 항목은 GitHub repository 원본 필드에 `owner_login` shortcut이 추가된 형태다. 선택 UI에서는 최소한 `id`, `name`, `full_name`, `html_url`, `clone_url`, `private`, `default_branch`, `owner_login`, `description`, `language`, `updated_at`만 안전하게 사용해라. 파이프라인 `repo_url`에는 `clone_url` 또는 정상 HTTPS GitHub URL을 보내라.

B. 브랜치
`GET /api/repos/{owner}/{repo}/branches` (Bearer 필수)

응답:
{
  "owner": string,
  "repo": string,
  "count": number,
  "branches": Array<{
    "name": string,
    "commit": { "sha": string, "url"?: string },
    "protected"?: boolean,
    "commit_sha": string
  }>
}

owner와 repo는 path segment별로 encodeURIComponent 처리해라. 저장소 변경 시 브랜치 상태를 초기화하고, `default_branch`가 있으면 기본 선택해라.

C. 16개 보안 정책 카탈로그
`GET /api/security/catalog` (현재 무인증)

응답:
{
  "total": 16,
  "items": Array<{
    "key": string,
    "name": string,
    "cwe": string,
    "grade": "critical" | "high" | "medium" | "low"
  }>,
  "by_grade": {
    "critical": SecurityCatalogItem[],
    "high": SecurityCatalogItem[],
    "medium": SecurityCatalogItem[],
    "low": SecurityCatalogItem[]
  }
}

- 서버 응답을 단일 소스로 사용하고 16개를 프론트에 중복 하드코딩하지 마라.
- 등급별 전체 선택/해제와 전체 선택을 제공해라.
- 기본은 16개 전체 선택이다. 0개로 실행하지 말고 제출 버튼을 비활성화해라.
- request에는 일관되게 item `key`를 보내라. 백엔드/엔진은 CWE도 수용하지만 한 화면에서 형태를 섞지 마라.

D. 파이프라인 생성
`POST /api/pipelines` (Bearer 필수, 성공 202)

request:
{
  "repo_url": "https://github.com/owner/repo.git",
  "branch": "main",
  "trigger_source": "manual",
  "selected_items": ["sql-injection", "idor", "xss"],
  "source": "capstone",
  "environment": "development",
  "workflow_path": null,
  "commit_sha": "<branch response commit_sha or null>"
}

규칙:
- `source`: 기본 `capstone`. `capstone`은 gitleaks+semgrep, `mirae`는 semgrep만 실행한다. 제품 정책으로 `mirae`가 확정된 경우만 변경하고, 환경변수/구성 상수로 관리해라.
- `environment`: `production | staging | development | feature`. 기본 `development`.
- `production`/`staging`은 Medium도 승인 필요로 올라갈 수 있다. 선택 UI에 안내해라.
- 브랜치 선택 시 받은 `commit_sha`를 보내 실제 선택 커밋과 스캔 커밋을 일치시켜라.
- `selected_checks`, `env_vars`, `is_first_run`은 request model에는 있지만 현재 polling 경로에서 실행 엔진으로 전달되지 않는다. UI 핵심 기능이나 성공 조건으로 사용하지 마라.

성공 응답:
{
  "job_id": "uuid",
  "status": "pending",
  "message": "파이프라인이 큐에 등록되었습니다. 엔진이 곧 가져갑니다."
}

202를 받으면 `job_id`를 기준으로 실행 상세 화면에 이동하고 polling을 시작해라. 생성 버튼은 request 중 중복 제출을 막아라.

409 중복 실행 응답은 다음 형태다.
{
  "error": "CONFLICT",
  "message": "... 이미 실행 중인 파이프라인이 있습니다",
  "existing_job_id": "uuid"
}
toast/inline alert를 보여주고 `existing_job_id`로 기존 실행 화면을 열 수 있게 해라.

E. job 상태
`GET /api/jobs/{job_id}`

응답:
{
  "job": {
    "job_id": string,
    "repo_url": string,
    "branch": string,
    "trigger_source": string,
    "status": "queued" | "running" | "success" | "failed" | "cancelled",
    "overall_result": string | null,
    "created_at": string | null,
    "started_at": string | null,
    "completed_at": string | null,
    "duration_secs": number | null,
    "metadata": Record<string, unknown> | null
  },
  "steps": PipelineStep[],
  "current_step": string | null,
  "security": object | null
}

PipelineStep:
{
  "step_id": string,
  "step_name": string,
  "step_type": string,
  "status": "pending" | "running" | "success" | "failed" | "skipped",
  "error_message": string | null,
  "started_at": string | null,
  "ended_at": string | null,
  "duration_secs": number | null
}

- create response의 `pending`은 생성 직후 client-side alias이다. 이후 job API는 DB status `queued`를 줄 수 있다. 둘 다 대기 상태로 표시해라.
- terminal status는 `success | failed | cancelled`다.
- `failed`를 단순 시스템 에러로 단정하지 마라. 보안 verdict `block` 또는 `block_pending_approval`도 job status는 `failed`다. terminal에서 result API를 반드시 확인해라.
- job detail의 `security.verdict`는 진행/요약용 legacy shape이다. 최종 보안 화면의 단일 소스는 반드시 `/api/jobs/{job_id}/result`로 해라.

F. 스텝과 로그
`GET /api/pipelines/{job_id}/steps`
응답: `{ "job_id": string, "job": JobSummary | null, "steps": PipelineStep[] }`

`GET /api/pipelines/{job_id}/logs`
응답: `{ "job_id": string, "lines": string[] }`

- 실행 중에는 job을 2~3초, 스텝을 2~3초 간격으로 polling하되 중복 timer를 만들지 마라.
- 로그는 로그 탭이 열려 있을 때만 3~5초 간격으로 조회해라.
- 화면이 background면 polling 간격을 늘리거나 일시 정지하고, foreground 복귀 시 즉시 refetch해라.
- terminal status에서 timer를 종료하고 result를 가져와라. component unmount, job_id 변경, 재시작 시 기존 timer/request를 취소해라.
- 특정 스텝의 순서나 개수를 하드코딩하지 말고 서버 배열 순서를 사용해라.
- 로그와 코드 snippet은 text로 표시하고 `dangerouslySetInnerHTML`를 사용하지 마라.

G. 최종 보안 결과
`GET /api/jobs/{job_id}/result?severity=critical,high&limit=100&offset=0` (Bearer 필수)

`severity`는 선택, `limit` 1~1000(기본 100), `offset` 0 이상이다.

응답 타입:
{
  "job_id": string,
  "repo_url": string,
  "branch": string,
  "commit_sha": string | null,
  "completed_at": string | null,
  "scores": {
    "security_score": number,
    "score_label": string | null,
    "gauge_color": "red" | "orange" | "yellow" | "green" | null,
    "code_quality_score": number
  },
  "verdict": {
    "verdict": "block" | "block_pending_approval" | "warn" | "pass" | null,
    "overall_status": string,
    "status_reason": string | null,
    "total_findings": number,
    "requires_approval": boolean,
    "block_reasons": string[],
    "warn_reasons": string[],
    "selected_items": string[],
    "selected_count": number,
    "out_of_scope_count": number,
    "score_breakdown": {
      "critical"?: number,
      "high"?: number,
      "medium"?: number,
      "low"?: number
    }
  },
  "severity_summary": {
    "critical": number,
    "high": number,
    "medium": number,
    "low": number
  },
  "scanner_summaries": Array<{
    "scanner": string,
    "count": number,
    "critical": number,
    "high": number,
    "medium": number,
    "low": number
  }>,
  "findings": SecurityFinding[],
  "approval": ApprovalRecord | null,
  "pagination": {
    "total": number,
    "limit": number,
    "offset": number,
    "has_more": boolean
  }
}

SecurityFinding:
{
  "id": string,
  "scanner": "gitleaks" | "semgrep" | string,
  "rule_id": string,
  "cwe": string | null,
  "policy_item": string | null,
  "in_scope": boolean,
  "cve": null,
  "cvss": string | null,
  "cvss_version": null,
  "title": string,
  "severity": "critical" | "high" | "medium" | "low",
  "file_path": string,
  "line_start": number,
  "line_end": number,
  "code_snippet": string | null,
  "code_snippet_start_line": number | null,
  "description": string,
  "ai_suggestion": string | null,
  "references": unknown[]
}

중요: 이 endpoint의 finding 필드는 엔진 callback 원본과 이름이 다르다. 최종 UI는 `line_number` 대신 `line_start`, `message` 대신 `description`, `ai_recommendation` 대신 `ai_suggestion`, `scanner_name` 대신 `scanner`를 사용해라.

진행 중에 result API를 호출하면 `verdict.overall_status = "pending"`, findings 빈 배열을 받을 수 있다. 이를 “안전” 또는 “취약점 0건”으로 표시하지 마라.

5. 보안 결과 UI 규칙
- pipeline 실행 상태와 security verdict를 서로 다른 신호로 표시해라. 두 값 중 하나로 나머지를 덮어쓰지 마라.
  - job `failed` + verdict `block`: 보안 즉시 차단
  - job `failed` + verdict `block_pending_approval`: 보안 승인 대기
  - job `failed` + verdict `pass|warn`: 보안 결과와 별개로 build/test/deploy 등 pipeline 실패를 표시
  - job `success` + verdict `pass|warn`: 파이프라인 성공, 단 warn은 경고 표시
- 게이트와 점수는 별개다. 배너/게이트 상태를 메인으로, 점수를 보조 지표로 보여줘라.
- 색상을 점수 구간으로 재계산하지 말고 `scores.gauge_color`를 그대로 사용해라.
- legacy 데이터에서 `gauge_color`가 null이면 score 구간이 아니라 verdict 매핑(`block=red`, `block_pending_approval=orange`, `warn=yellow`, `pass=green`)을 fallback으로 사용해라.
- 점수 문구는 가능하면 `scores.score_label`을 그대로 사용해 “검사 항목 N개 기준”을 함께 보여줘라.
- `out_of_scope_count > 0`이면 “정책 범위 밖 N건 탐지(미검사/미선택 범위)”를 반드시 표시해라. 미선택을 “안전”이라고 표현하지 마라.
- `block_reasons` / `warn_reasons`는 서버 문구를 보존해 목록으로 보여줘라.
- severity 표시 순서는 `critical → high → medium → low`다.
- finding은 severity, CWE, policy item, scanner, title, `file_path:line_start`, description을 보여주고 snippet과 AI 제안은 기본 접힌 상태로 펼칠 수 있게 해라.
- snippet 줄 번호는 `code_snippet_start_line + index`로 계산하고 `line_start`를 강조해라. null이면 영역을 숨겨라.
- gitleaks의 secret은 엔진에서 마스킹되지만, 프론트는 추가로 민감 값을 복원/추론/로그하지 마라.

verdict 매핑:
- `block`: 빨강, 즉시 차단, 승인 버튼 금지
- `block_pending_approval`: 주황, 현재 차단 + 승인 가능
- `warn`: 노랑, 경고하지만 파이프라인 성공 가능
- `pass`: 초록, 통과

6. 승인 워크플로

ApprovalRecord:
{
  "id": string,
  "job_id": string,
  "commit_sha": string | null,
  "scanned_commit_sha": string | null,
  "repo": string,
  "branch": string,
  "target_cwes": string[],
  "block_reasons": string[],
  "acknowledged_cwes": string[],
  "followup_job_id": string | null,
  "reason": string | null,
  "approver_id": string | null,
  "status": "pending" | "approved" | "rejected",
  "approved_at": string | null,
  "expires_at": string | null,
  "created_at": string | null
}

- `GET /api/jobs/{job_id}/approval` (Bearer): 최신 승인 레코드. 없으면 404이며 이를 정상적인 “아직 승인 요청 없음”으로 처리해라.
- `POST /api/jobs/{job_id}/approval/request` (Bearer, body 없음): `block_pending_approval`에 대한 pending 레코드 생성. 이미 pending이면 409.
- `POST /api/jobs/{job_id}/approval/approve` (Bearer):
  `{ "reason": "수용 사유", "approved_cwes": ["CWE-639"], "expires_at": "ISO-8601 or omitted" }`
  `reason`은 필수다. `approved_cwes`를 생략하면 백엔드가 차단 사유의 모든 승인 가능 CWE를 수용한다. Critical은 서버가 제외하며 `block`은 403이다.
- approve 성공 응답의 `followup_job_id`는 승인된 CWE를 넣어 새로 enqueue된 job이다. 성공 안내 후 해당 job 상세로 이동하거나 명확한 링크를 제공하고 polling을 시작해라.
- `POST /api/jobs/{job_id}/approval/reject` (Bearer): `{ "reason": "거부 사유" }`. 서버는 빈 사유도 받지만 UI에서는 감사 가능성을 위해 거부 사유를 필수로 받아라.
- `GET /api/approvals?status=pending|approved|rejected` (Bearer): `{ total, records }`. 기존 승인/감사 화면이 있으면 연동해라.
- 승인 버튼은 `verdict.verdict === "block_pending_approval" && verdict.requires_approval === true`일 때만 보여줘라.
- request→approve/reject 순서를 지켜라. 승인 레코드가 없으면 먼저 request를 생성해야 한다.
- 승인은 취약점을 제거한 것이 아니라 특정 위험을 수용한 것이므로, “수용된 취약점 포함”으로 표시해라.

7. 취소와 삭제
- `POST /api/pipelines/{job_id}/cancel` (Bearer): queued/running에서만 가능. 응답 `{ job_id, status: "cancelled", killed: boolean, message }`.
- `DELETE /api/pipelines/{job_id}` (Bearer): 연관 DB 데이터와 로컬 result를 삭제한다. 복구 불가능한 실질적 삭제이므로 명시적 확인 modal 후에만 호출해라. 실행 중이면 서버가 취소 후 삭제한다.
- 기존 화면에 취소/삭제 UX가 없다면 핵심 연동을 망가뜨리면서까지 새로 만들지 말고, API client 함수와 확장 가능한 구조만 먼저 준비해라.

8. 사용하지 말아야 할 legacy/미제공 계약
- 신규 프론트에서 `POST /start-pipeline` 대신 `POST /api/pipelines`를 사용해라.
- query 형식 `/pipeline-logs?job_id=...`, `/pipeline-steps?job_id=...` 대신 path 형식 `/api/pipelines/{job_id}/logs|steps`를 사용해라.
- raw callback 저장소인 `GET /get-results?job_id=...`를 최종 화면의 주 API로 사용하지 마라.
- `GET /api/jobs/{job_id}/findings`는 구형 field naming이므로 최종 결과 화면에서 혼용하지 말고 `/api/jobs/{job_id}/result`를 사용해라.
- 현재 `GET /api/jobs` 목록 API, repository latest-scan API는 없다. 가짜 endpoint를 만들거나 호출하지 마라.
- 배포 URL은 `GET /api/pipelines/{job_id}/deployment`의 `deployment.url` 또는 인증된 result API의 동일한 `deployment.url`을 사용한다. 값이 null이면 링크를 만들지 마라. 실시간 로그와 배포 이벤트는 `REALTIME_LOG_DEPLOYMENT_API.md` 계약을 따른다.

9. UI 상태와 접근성
- 모든 요청에 loading, empty, error, retry, success 상태를 구분해라.
- 저장소 0개, 브랜치 0개, 보안 finding 0개, log 0줄을 서로 다른 empty state로 표시해라.
- status/severity를 색상만으로 구분하지 말고 텍스트와 아이콘을 함께 사용해라.
- API에서 받은 URL/문자열을 임의 HTML로 렌더링하지 마라. 외부 링크는 `http:`/`https:` 허용 여부를 검증하고 `rel="noopener noreferrer"`를 사용해라.
- 일시적 네트워크 실패 한 번으로 실행을 실패로 단정하지 말고, 상태 조회를 재시도할 수 있게 해라.

10. 테스트
기존 테스트 스택을 사용해 최소한 다음을 검증해라.
- API client가 base path와 Bearer header를 올바르게 붙인다.
- OAuth callback이 token을 저장한 후 URL에서 제거한다.
- 401에서 세션을 정리한다.
- pipeline 202 성공 시 반환된 job_id로 이동한다.
- pipeline 409에서 existing_job_id를 노출하고 기존 실행으로 이동할 수 있다.
- pending/queued → running → terminal 폴링과 timer cleanup이 동작한다.
- job status `failed` + verdict `block_pending_approval`을 일반 실행 오류가 아닌 승인 필요 상태로 보여준다.
- gauge color를 score로 재계산하지 않는다.
- out_of_scope_count > 0이면 미검사 경고가 보인다.
- snippet 실제 줄 번호와 취약 줄 강조가 맞다.
- approval request → approve → followup_job_id 이동이 동작한다.
- block에는 승인 UI가 없고 block_pending_approval에만 있다.
- unmount/job 변경 시 polling이 중복되거나 남지 않는다.

11. 검증과 완료 보고
- lint, typecheck, unit/component test, production build 중 저장소에 존재하는 검증 명령을 모두 실행해라.
- 실제 API를 호출하는 수동 검증은 사용자 계정 데이터를 변경하지 않는 범위에서 하고, 실제 pipeline 생성/취소/삭제/승인은 명시적으로 허용받지 않았다면 테스트 double로 검증해라.
- 완료 보고에는 변경 파일, 구현한 흐름, 실행한 검증과 결과, 남은 backend/config blocker만 짧게 적어라.
```

---

## 프론트 작업 전 백엔드/배포 선행 조치

아래는 2026-08-09 실행 중인 `capstone-back` 컨테이너에서 확인한 값이다. 프론트 코드만으로는 해결할 수 없다.

1. OAuth 완료 redirect를 프론트 callback으로 변경

- 현재: `FRONTEND_REDIRECT_URL=http://112.186.136.153:8000/auth/success`
- 필요: `FRONTEND_REDIRECT_URL=https://<frontend-origin>/<frontend-auth-callback-route>`
- 예: `https://pwd.kr/auth/callback`

2. GitHub OAuth callback을 외부 HTTPS 백엔드 경로와 일치

- 현재: `GITHUB_REDIRECT_URI=http://112.186.136.153:8000/auth/github/callback`
- 권장: `GITHUB_REDIRECT_URI=https://api.pwd.kr/capstonelab/capstone-back/auth/github/callback`
- GitHub OAuth App의 Authorization callback URL도 동일해야 한다.

3. CORS

- 백엔드 기본 허용 origin은 `https://api.pwd.kr`, `https://pwd.kr`다.
- 현재 `ALLOWED_ORIGINS` 추가값은 비어 있다.
- 로컬 프론트엔드에서 연동하려면 정확한 dev origin(예: `http://localhost:5173`)을 `ALLOWED_ORIGINS`에 추가하고 백엔드를 재시작해야 한다.
- wildcard `*`로 풀지 말고 실제 origin을 콤마로 나열한다.

4. 백엔드 보안 강화 필요

현재 백엔드에서 `GET /api/jobs/{job_id}`, `GET /api/jobs/{job_id}/findings`, logs/steps API와 `POST /get-results`가 라우트 수준에서 인증/소유권 검사 없이 열려 있다. 프론트는 항상 Bearer를 보내되, 운영 전에 백엔드에서 다음을 보완해야 한다.

- 사용자용 job/log/step/findings 조회에 JWT 및 job 소유권 검사
- callback에 `x-callback-token` 또는 동등한 engine 인증 검증
- 불필요한 legacy/raw result 외부 노출 차단

5. 현재 API의 기능 공백

- job 전체 목록 API가 없어 서버 기준 실행 히스토리 화면을 완성할 수 없다.
- 인증된 result API에 deployment service URL이 없어 배포 바로가기를 정상 구현할 수 없다.
- 두 기능이 필요하면 프론트에서 추정/우회하지 말고 백엔드 API를 먼저 추가한다.

---

## 계약 검증 메모

- 운영 health check은 `GET https://api.pwd.kr/capstonelab/capstone-back/health` 기준 200 `{"status":"ok"}`를 확인했다.
- 어제 추가된 엔진 poller는 백엔드의 pending job을 최소 5초, 기본 15초 간격으로 조회한다. 파이프라인 생성 직후 잠시 `pending/queued`인 것은 정상이다.
- 엔진 최종 callback은 로그 최대 250줄, finding 최대 120개, snippet 항목당 최대 1,200자로 축약될 수 있다. 현재 result API는 DB에 저장된 데이터를 노출하므로 UI는 pagination과 null/축약 가능성을 항상 처리해야 한다.
