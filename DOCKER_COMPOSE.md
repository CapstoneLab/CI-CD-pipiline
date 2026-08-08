# Docker Compose 운영

백엔드와 CICD 엔진은 `capstone-internal` 사용자 정의 네트워크에서 통신한다.
엔진은 컨테이너 IP가 아니라 `http://backend:8000`을 사용해 작업을 폴링하고,
동일한 주소의 `/get-results`로 결과를 전송한다.

## 1. 기존 백엔드 컨테이너를 유지하는 실행

프로젝트 `.env`에 백엔드와 동일한 `ENGINE_SHARED_TOKEN`이 있어야 한다.

```bash
./docker/compose-up.sh existing
```

스크립트는 실행 중인 `capstone-back`을 `capstone-internal` 네트워크에
`backend`라는 별칭으로 연결하고 CICD 엔진만 Compose로 기동한다.

## 2. 백엔드까지 Compose가 관리하도록 이관

기존 컨테이너의 환경변수는 값이 출력되지 않는 방식으로 `.env.backend`에
옮겨지고 파일 권한은 600으로 제한된다. 기존 컨테이너를 중지·제거한 뒤
같은 이미지로 재생성하려면 다음 명령을 사용한다.

```bash
./docker/compose-up.sh managed --replace-existing
```

`.env.backend`가 이미 있다면 자동으로 덮어쓰지 않는다. 새 서버에서는
`.env.backend.example`을 참고해 직접 생성하면 된다.

## 3. 상태와 로그 확인

```bash
ENGINE_DATA_ROOT="$PWD/.docker-data" docker compose ps
ENGINE_DATA_ROOT="$PWD/.docker-data" docker compose logs -f cicd-engine
curl http://127.0.0.1:8000/health
```

실행 결과와 체크아웃 workspace는 `.docker-data`에 유지된다. 이 절대경로를
호스트와 엔진 컨테이너에 동일하게 마운트하므로, 엔진이 Docker 기반
워크플로우를 실행할 때도 bind mount 경로가 어긋나지 않는다.

백엔드 8000 포트는 기존 동작을 유지하기 위해 기본적으로 `0.0.0.0`에
바인딩된다. 같은 호스트의 reverse proxy만 백엔드에 접근한다면 `.env`에
`BACKEND_BIND_ADDRESS=127.0.0.1`을 설정해 외부 직접 접근을 차단한다.

> 엔진은 `/var/run/docker.sock`을 마운트하므로 호스트 Docker에 대한 높은
> 권한을 가진다. 신뢰할 수 있는 저장소와 승인된 워크플로우만 실행해야 한다.

## 4. GitHub Actions

`.github/workflows/cicd-engine-container.yml`은 PR에서 Python 테스트와 이미지
빌드를 수행한다. `main` 또는 `v*` 태그가 푸시되면 동일한 이미지를
`ghcr.io/capstonelab/cicd-engine`에 게시한다.
