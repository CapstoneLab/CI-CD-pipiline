# 실시간 로그·배포 URL 프론트 연동 명세

이 문서는 CICD 엔진 → 백엔드 → 프론트엔드의 실시간 로그 흐름과 배포 URL 조회 계약을 정의한다.

## 1. 전체 흐름

```text
CICD 엔진 append_log()
  → POST /get-results (type=log_batch, x-callback-token)
  → 백엔드 DB 저장 + job별 WebSocket fan-out
  → WS /api/pipelines/{job_id}/logs/ws
  → 프론트 로그 콘솔

deploy 성공
  → runs/{run_id}/deployment_result.json
  → step_complete / pipeline_complete callback의 deployment
  → GET /api/pipelines/{job_id}/deployment
  → 프론트 “배포 사이트 열기” 링크
```

엔진과 백엔드 사이는 재시도 및 서버 측 검증이 쉬운 HTTP 콜백을 사용한다. 브라우저에만 WebSocket을 열어 엔진을 외부에 직접 노출하지 않는다.

## 2. 엔진 → 백엔드 로그 콜백

`POST /get-results`

Headers:

```http
Content-Type: application/json
x-callback-token: ${ENGINE_SHARED_TOKEN}
```

Payload:

```json
{
  "schema_version": 1,
  "type": "log_batch",
  "event_id": "sha256-id",
  "job_id": "job-uuid",
  "run_id": "run-20260905-001",
  "repo_url": "https://github.com/owner/repo.git",
  "branch": "main",
  "pipeline_status": "running",
  "sequence_start": 41,
  "sequence_end": 42,
  "events": [
    {
      "sequence": 41,
      "timestamp": "2026-09-05T03:00:00+00:00",
      "step_name": "build",
      "stream": "stdout",
      "message": "[2026-09-05 12:00:00] npm run build"
    }
  ],
  "logs": [
    "[build.log] [2026-09-05 12:00:00] npm run build"
  ],
  "metadata": {
    "executor": "ubuntu-ci-engine",
    "run_id": "run-20260905-001"
  }
}
```

- `event_id`와 `sequence`는 중복 콜백 제거 및 정렬에 사용한다.
- `events`가 신규 표준이며 `logs`는 기존 백엔드 호환 필드다.
- 실시간 전송이 실패해도 엔진의 `logs/*.log`는 보존된다.
- 각 `step_complete`에는 해당 단계 전체 로그가 다시 포함되므로 백엔드는 최종 상태를 보정할 수 있다.
- 실패한 실시간 전송 범위는 `runs/{run_id}/log_stream_failures.jsonl`에 기록된다. 로그 원문은 중복 저장하지 않는다.

## 3. 프론트 → 백엔드 WebSocket

Endpoint:

```text
WS /api/pipelines/{job_id}/logs/ws
```

- HTTPS 환경에서는 반드시 `wss://`를 사용한다.
- JWT를 URL query에 넣지 않는다. URL/access log 유출을 피하기 위해 연결 직후 첫 메시지로 인증한다.
- 서버는 인증 메시지를 5초 안에 받지 못하면 연결을 종료한다.

클라이언트 첫 메시지:

```json
{
  "type": "authenticate",
  "token": "JWT access token"
}
```

서버 메시지 순서:

1. `authenticated`
2. `log_snapshot`
3. 0개 이상의 `log_batch`, `step_complete`
4. `pipeline_complete`

### `authenticated`

```json
{
  "schema_version": 1,
  "type": "authenticated",
  "job_id": "job-uuid"
}
```

### `log_snapshot`

연결 전에 발생한 로그다. 화면의 초기 로그 목록으로 사용한다.

```json
{
  "schema_version": 1,
  "type": "log_snapshot",
  "job_id": "job-uuid",
  "lines": ["[clone.log] ...", "[build.log] ..."],
  "events": []
}
```

### `log_batch`

```json
{
  "schema_version": 1,
  "type": "log_batch",
  "event_id": "sha256-id",
  "job_id": "job-uuid",
  "run_id": "run-20260905-001",
  "sequence_start": 41,
  "sequence_end": 42,
  "events": [
    {
      "sequence": 41,
      "timestamp": "2026-09-05T03:00:00+00:00",
      "step_name": "build",
      "stream": "stdout",
      "message": "[2026-09-05 12:00:00] npm run build"
    }
  ]
}
```

프론트는 `(run_id, sequence)`를 키로 중복을 제거하고 `sequence` 순서로 추가한다. `message`는 HTML이 아닌 text로 렌더링한다.

### 단계 및 종료 이벤트

```json
{
  "schema_version": 1,
  "type": "step_complete",
  "job_id": "job-uuid",
  "step": {
    "step_name": "deploy",
    "status": "success",
    "step_order": 7,
    "total_steps": 7
  },
  "deployment": {
    "status": "success",
    "url": "https://deploy.example/services/owner/repo"
  }
}
```

```json
{
  "schema_version": 1,
  "type": "pipeline_complete",
  "job_id": "job-uuid",
  "status": "success",
  "deployment": {
    "status": "success",
    "url": "https://deploy.example/services/owner/repo"
  },
  "ended_at": "2026-09-05T03:05:00+00:00"
}
```

`step_complete` 수신 시 steps/logs REST API를 한 번 재조회하고, `pipeline_complete` 수신 시 result/deployment REST API를 최종 재조회한다.

WebSocket close code:

| code | 의미 |
|---:|---|
| `4401` | JWT 누락, 만료 또는 형식 오류 |
| `4403` | 허용되지 않은 Origin 또는 다른 사용자의 job |
| `4404` | job 없음 |

## 4. 배포 URL 조회

Endpoint:

```http
GET /api/pipelines/{job_id}/deployment
Authorization: Bearer ${JWT}
```

Response 200, 배포 전 또는 배포 없는 파이프라인:

```json
{
  "job_id": "job-uuid",
  "status": "running",
  "deployment": null
}
```

Response 200, 배포 성공:

```json
{
  "job_id": "job-uuid",
  "status": "success",
  "deployment": {
    "schema_version": 1,
    "status": "success",
    "url": "https://deploy.example/services/owner/repo",
    "public_url": "https://deploy.example/services/owner/repo",
    "direct_url": "http://127.0.0.1:12345",
    "urls": [
      "https://deploy.example/services/owner/repo",
      "http://127.0.0.1:12345"
    ],
    "domain": "deploy.example",
    "owner": "owner",
    "repo": "repo",
    "branch": "main",
    "runtime": "node",
    "deployed_at": "2026-09-05T03:05:00+00:00"
  }
}
```

`GET /api/jobs/{job_id}/result` 응답에도 동일한 `deployment` 필드가 포함된다. 프론트의 버튼 href에는 `deployment.url`만 사용하고, 값이 `http:` 또는 `https:`인지 `new URL()`로 검증한 뒤 `target="_blank" rel="noopener noreferrer"`를 적용한다. `direct_url`은 진단용이며 사용자 링크로 사용하지 않는다.

엔진 로컬 파일 `runs/{run_id}/deployment_result.json`도 같은 구조다. 기존 소비자를 위해 `service_url`, `direct_service_url`, `service_urls` 필드는 함께 유지한다.

## 5. 프론트 TypeScript 예시

```ts
type LogEvent = {
  sequence: number;
  timestamp: string;
  step_name: string;
  stream: "stdout" | "stderr";
  message: string;
};

export function connectPipelineLogs(
  apiBaseUrl: string,
  jobId: string,
  jwt: string,
  onMessage: (event: Record<string, unknown>) => void,
) {
  const url = new URL(apiBaseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/api/pipelines/${encodeURIComponent(jobId)}/logs/ws`;
  url.search = "";

  const socket = new WebSocket(url);
  socket.addEventListener("open", () => {
    socket.send(JSON.stringify({ type: "authenticate", token: jwt }));
  });
  socket.addEventListener("message", ({ data }) => {
    onMessage(JSON.parse(String(data)));
  });
  return () => socket.close(1000, "component unmounted");
}
```

권장 처리:

- `log_snapshot`: 기존 화면 로그를 `lines`로 초기화
- `log_batch`: `events`를 sequence 기준 중복 제거 후 추가
- `step_complete`: `GET /api/pipelines/{job_id}/steps`와 logs를 1회 갱신
- `pipeline_complete`: 소켓 종료 후 result 및 deployment 조회
- 비정상 종료: 지수 backoff로 재접속하고 그 사이 `GET /api/pipelines/{job_id}/logs`를 fallback으로 사용
- component unmount 또는 `job_id` 변경 시 반드시 기존 소켓 종료

## 6. 운영 설정

- 엔진과 백엔드의 `ENGINE_SHARED_TOKEN` 값이 같아야 한다.
- `ALLOWED_ORIGINS`에 실제 프론트 origin을 정확히 추가한다.
- reverse proxy가 WebSocket `Upgrade`/`Connection` 헤더와 긴 read timeout을 지원해야 한다.
- 현재 fan-out backlog는 단일 Uvicorn worker의 메모리에 있다. 백엔드를 여러 worker/replica로 확장할 때는 동일 메시지 계약을 유지한 채 Redis pub/sub로 교체한다.
