# Python 런타임 지원 매뉴얼

이 문서는 CI/CD 엔진에 **Python 런타임 대응으로 추가된 기능만** 정리한 것이다. 기존 Node 런타임 동작은 변경되지 않았으며 이 문서의 대상이 아니다.

---

## 1. 개요

파이프라인은 이제 Python 레포를 Node 레포와 동일한 단계(`install → lightweight_security → test → deep_security → build → deploy`)로 처리한다. 워크플로 YAML의 `runtime.type: python` 선언으로 활성화되며, 명시가 없어도 레포 마커로 자동 감지된다.

### 활성화 방법

```yaml
# .localci/workflow.yml
name: my-python-workflow
runtime:
  type: python
steps:
  - name: install
    uses: install
  - name: test
    uses: test
  - name: build
    uses: build
  - name: deploy
    uses: deploy
```

파일이 없으면 엔진이 레포를 스캔해서 Python 프로젝트로 판단될 경우 위 템플릿을 자동 생성한다.

---

## 2. 지원 패키지 매니저

총 6종을 지원하며, 감지 우선순위와 명령은 다음과 같다.

| 매니저 | 감지 기준 | frozen install | 일반 install |
|---|---|---|---|
| **poetry** | `pyproject.toml`의 `[tool.poetry]` 또는 `poetry.lock` 또는 `build-system.requires`에 `poetry-core` | `poetry install --no-interaction --no-root --sync` | `poetry install --no-interaction --no-root` |
| **pdm** | `[tool.pdm]` 또는 `pdm.lock` 또는 `pdm-backend` | `pdm install --frozen-lockfile` | `pdm install` |
| **uv** | `[tool.uv]` 또는 `uv.lock` | `uv sync --frozen` | `uv sync` |
| **hatch** | `[tool.hatch]` 또는 `build-system.requires`에 `hatchling` | `hatch env create` | 동일 |
| **pipenv** | `Pipfile.lock` 또는 `Pipfile` | `pipenv install --deploy --dev` | `pipenv install --dev` |
| **pip** | 기본값 / `requirements.txt` | `python -m pip install -r requirements.txt` | 동일 |

감지 우선순위:
1. `pyproject.toml`의 `[tool.<name>]` 섹션
2. 각 매니저의 전용 lockfile
3. `build-system.requires`의 백엔드 힌트
4. `Pipfile` 존재 여부
5. 기본값 `pip`

### 2.1 Effective package manager 폴백

선언된 매니저가 엔진 호스트에 설치되어 있지 않으면 다음 규칙으로 폴백한다:

- 호스트에 매니저 실행파일이 **없고** 프로젝트 루트에 `requirements.txt`가 **있으면** → 자동으로 `pip` 사용
- 둘 다 없으면 "Command not found: poetry (install it on the engine host, e.g. `pipx install poetry`)" 형태의 명확한 에러로 중단

폴백이 발생하면 install/test/build/deploy 전 단계에서 일관되게 pip 경로를 사용한다 (중간에 poetry로 다시 오선택되지 않음).

---

## 3. 프로젝트 구조 감지

### 3.1 단일 레포

레포 루트에 다음 마커 중 하나가 있으면 Python 프로젝트로 간주한다:

- `pyproject.toml`
- `requirements.txt`
- `setup.py`
- `setup.cfg`
- `Pipfile`

### 3.2 모노레포

루트에 마커가 없으면 BFS 얕은 스캔(기본 최대 깊이 3)으로 하위 디렉터리를 탐색한다. 첫 번째로 마커가 발견된 디렉터리가 **project_root**가 되며, install/test/build의 모든 명령은 그 디렉터리를 `cwd`로 사용한다.

예시 — 다음 구조에서 `backend/`가 project_root가 된다:

```
repo/
├── docs/
├── frontend/         (무시됨: 마커 없음)
├── backend/
│   ├── pyproject.toml   ← 마커
│   ├── requirements.txt
│   └── app/main.py
└── README.md
```

스캔에서 제외되는 디렉터리:
`.git`, `.venv`, `venv`, `env`, `__pycache__`, `build`, `dist`, `.tox`, `.pytest_cache`, `.mypy_cache`, `node_modules`, `.eggs`, `site-packages`, `.cache`, `.`으로 시작하는 디렉터리 전부

---

## 4. 런타임 자동 감지 및 reconcile

### 4.1 감지 규칙

```
1. package.json 존재       → node
2. 3.1/3.2의 Python 마커   → python
3. 그 외                   → node (기본값)
```

### 4.2 Reconcile (자동 교체)

레포에 `.localci/workflow.yml`이 이미 존재하더라도, 선언된 런타임의 마커가 레포에 **없고** 다른 런타임 마커만 **있으면** 자동으로 교체한다.

예: 커밋된 워크플로가 `runtime.type: node`인데 레포에는 `package.json`이 없고 `backend/pyproject.toml`만 있음 → `runtime.type`이 `python`으로 교체되어 진행.

사용자가 의도적으로 선언한 런타임 마커가 같이 존재하는 경우는 건드리지 않는다.

---

## 5. 단계별 상세 동작

### 5.1 install

```
1. find_python_project_root(repo_dir) 호출 → 모노레포 대응
2. strip_engine_managed_requirements(project_root)
   └─ requirements.txt의 semgrep/gitleaks 라인을 `# [engine-managed]` 주석으로 치환
3. effective_package_manager(project_root)로 실제 사용할 매니저 결정
4. pip 경로:
   a. 프로젝트 루트에 .venv가 없으면 `python3 -m venv .venv`로 생성
   b. `.venv/bin/python -m pip install --upgrade pip` (best-effort)
   c. requirements.txt 또는 pyproject.toml로 의존성 설치
   d. lockfile 있으면 frozen → 실패 시 non-frozen 폴백
5. pip 외 매니저 경로:
   a. 실행파일 존재 여부 확인
   b. 없으면 에러 반환 (엔진은 임의로 pip install <manager> 하지 않음)
   c. 해당 매니저의 install 명령 실행
```

**PEP 668 회피:** Ubuntu 24 / Amazon Linux 2023 등의 시스템 파이썬은 externally-managed 상태라 전역 pip install이 차단된다. pip 경로에서는 반드시 venv를 먼저 생성해서 격리된 환경을 사용한다.

### 5.2 test

```
1. find_python_project_root 재호출
2. find_test_directories(project_root) → test/ 및 tests/ 디렉터리 수집
3. has_collectible_tests(project_root) 검사
   └─ test_*.py 또는 *_test.py가 실제 존재하는지 확인
   └─ __init__.py만 있는 빈 스캐폴드는 False
4. has_pytest_configured(project_root) 검사
   └─ pyproject.toml [tool.pytest.ini_options], pytest.ini, tox.ini
5. 수집 가능한 테스트도 없고 pytest 설정도 없으면 skipped
6. 실행:
   - pip 경로: .venv/bin/python -m pytest [테스트_디렉터리...]
   - poetry/pipenv/uv/pdm/hatch 경로: <pm> run python -m pytest [테스트_디렉터리...]
   - 단, pyproject.toml에 [tool.pytest.ini_options]가 있으면 dir 인자를 생략해 사용자 설정 존중
7. 결과 처리:
   - exit 0 → success
   - exit 5 (no tests collected) → skipped
   - 그 외 → failed
8. pip 경로에서 pytest가 없으면 venv에 자동 설치 후 재시도
```

**테스트 디렉터리 탐색:** `test/` (단수)와 `tests/` (복수) 모두 지원한다. 모노레포에서 `backend/tests`, `services/api/test` 같은 여러 위치가 있으면 전부 pytest에 명시적 인자로 전달한다.

### 5.3 build

```
1. find_python_project_root
2. 매니저에 따라 빌드 명령 결정:
   - poetry → poetry build
   - uv     → uv build
   - pdm    → pdm build
   - hatch  → hatch build
   - pip    → .venv/bin/python -m build  (없으면 venv에 build 패키지 자동 설치)
3. 실행 후 분기:
   - 성공 + dist/ 존재 → artifacts_dir로 복사 → success
   - 성공 + dist/ 없음 → 소스 트리를 dist-python/으로 복사 → success
   - 실패 → 소스 트리 fallback + build_meta.json 기록 → success
```

**소스 트리 fallback 이유:** 많은 FastAPI/Flask 레포는 `pyproject.toml`에 패키지 레이아웃(`packages = [...]`)을 선언하지 않아 `poetry build` / `python -m build`가 "No file/folder found for package" 에러로 실패한다. 이런 레포는 wheel/sdist를 만들지 못해도 **소스 트리 자체로 충분히 배포 가능**하므로 실패 대신 소스 fallback을 생성하고 파이프라인을 계속 진행시킨다.

**fallback 디렉터리 구조:**

```
run-XXX/artifacts/
├── dist-python/          ← 소스 트리 복사본
│   ├── app/
│   ├── requirements.txt
│   ├── pyproject.toml
│   └── ...
└── build_meta.json       ← 폴백 메타데이터
```

제외 디렉터리: `.git`, `.venv`, `venv`, `env`, `__pycache__`, `build`, `dist`, `.tox`, `.pytest_cache`, `.mypy_cache`, `.cache`, `node_modules`, `.eggs`, `*.egg-info`

### 5.4 deploy

EC2에서 SSM으로 실행되는 bash 스크립트가 Python 전용 분기로 동작한다.

```
1. APP_ROOT 결정:
   dist-python → dist → dist-server → build → out 순으로 첫 번째 존재 디렉터리

2. 인터프리터 선택:
   /usr/bin/python3.12 → 3.11 → 3.10 → python3 순으로 첫 번째 실행 가능한 파일

3. TMPDIR 우회:
   export TMPDIR=$APP_DIR/.pip-tmp
   export TMP=$APP_DIR/.pip-tmp
   export TEMP=$APP_DIR/.pip-tmp
   export PIP_NO_CACHE_DIR=1
   → Amazon Linux 2023 t3.micro의 /tmp는 ~458MB tmpfs. 대형 wheel 빌드가 tmpfs를
     초과하는 것을 방지하기 위해 EBS 디스크 경로로 강제 우회.

4. venv 생성: $APP_ROOT/.venv
   (재배포 때마다 S3에서 소스가 새로 내려오므로 venv도 새로 만들어짐)

5. 의존성 설치:
   - requirements.txt 우선
   - 없으면 pip install .  (pyproject.toml로부터)

6. ASGI/WSGI 감지:
   requirements.txt / pyproject.toml / Pipfile 내에서 다음 패턴 검색:
   fastapi | starlette | uvicorn | sanic | quart | litestar | hypercorn
   → 하나라도 매치되면 ASGI, 아니면 WSGI

7. 엔트리 탐색 (첫 매치 사용):
   app/main.py        → app.main:app
   src/main.py        → src.main:app
   main.py            → main:app
   app.py             → app:app
   wsgi.py            → wsgi:application
   application.py     → application:application
   asgi.py            → asgi:application

8. 기존 프로세스 정리:
   - $APP_DIR/.app.pid 파일의 PID 확인 후 kill
   - pkill -f "uvicorn.*<owner>--<repo>"
   - pkill -f "gunicorn.*<owner>--<repo>"

9. 백그라운드 실행:
   ASGI: nohup .venv/bin/python -m uvicorn <entry> \
           --host 0.0.0.0 --port $PORT --app-dir $APP_ROOT \
           > app.stdout.log 2> app.stderr.log < /dev/null &

   WSGI: nohup .venv/bin/python -m gunicorn <entry> \
           -b 0.0.0.0:$PORT --chdir $APP_ROOT \
           > app.stdout.log 2> app.stderr.log < /dev/null &

   APP_PID → $APP_DIR/.app.pid

10. 헬스 체크:
    1초 간격으로 최대 8회 ss -tlnp에서 :$PORT 바인딩 확인
    실패 시:
      - stderr 마지막 60줄 출력
      - stdout 마지막 30줄 출력
      - exit 1 (파이프라인 failed 표시)

11. nginx 프록시 등록:
    /opt/deployments/nginx/<owner>__<repo>.conf 에 proxy_pass 작성
    nginx -t && systemctl reload nginx
```

---

## 6. 엔진 관리 패키지 자동 필터링

gitleaks와 semgrep은 엔진 내부에서 `lightweight_security_scan` / `deep_security_scan` 단계로 이미 실행되므로, 프로젝트 의존성으로 중복 설치하지 않도록 자동 제거한다.

### 6.1 대상

```python
ENGINE_MANAGED_PYTHON_PACKAGES = {"semgrep", "gitleaks"}
```

### 6.2 동작

install 단계에서 `strip_engine_managed_requirements(project_root)`를 호출한다. `requirements.txt`를 한 줄씩 파싱해서:

- 빈 줄, 주석, `-r` / `-c` 포함 지시어는 보존
- `pkg==1.0` / `pkg>=1.0` / `pkg[extra]` / `pkg;python_version<"3.10"` 등 PEP 508 스펙 해석해서 패키지명 추출
- 매칭되는 라인은 **삭제 대신** `# [engine-managed]` 접두사로 주석 처리
- 변경이 있을 때만 파일 재작성 (idempotent)

### 6.3 예시

**Before:**
```txt
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
semgrep>=1.50.0
gitleaks==8.0.0
sqlalchemy[asyncio]>=2.0.30
requests
```

**After:**
```txt
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
# [engine-managed] semgrep>=1.50.0
# [engine-managed] gitleaks==8.0.0
sqlalchemy[asyncio]>=2.0.30
requests
```

### 6.4 효과

- semgrep은 OCaml 바이너리 포함 수백 MB 의존성 트리를 끌어오며 wheel 빌드 시 메모리/디스크를 과도하게 사용함. 이걸 스킵하는 것만으로:
  - 설치 시간: 수 분 → 수 초
  - 설치 용량: ~200MB → ~50MB
  - EC2 tmpfs OOM 방지
- `pyproject.toml`은 현재 필터링하지 않는다 (TOML 안전한 재작성이 복잡함). requirements.txt만 대상.

---

## 7. 자동 생성되는 Python 워크플로 템플릿

레포에 워크플로 파일이 없을 때, Python으로 감지되면 다음 템플릿이 `.localci/workflow.yml`로 생성된다:

```yaml
name: default-generated-workflow
runtime:
  type: python
steps:
  - name: install
    uses: install

  - name: lightweight-security
    uses: lightweight_security_scan
    continue_on_failure: true
    args:
      report_file: gitleaks_report.json

  - name: test
    uses: test

  - name: deep-security
    uses: deep_security_scan
    args:
      report_file: semgrep_report.json

  - name: build
    uses: build

  - name: deploy
    uses: deploy
```

---

## 8. 지원 프레임워크

### 8.1 완전 지원 (자동 감지 + 자동 실행)

- **FastAPI** (ASGI, `uvicorn`으로 실행)
- **Starlette** (ASGI)
- **Sanic** (ASGI)
- **Quart** (ASGI)
- **Litestar** (ASGI)
- **Flask** (WSGI, `gunicorn`으로 실행)
- **Bottle** (WSGI)
- 일반 `wsgi.py` / `asgi.py` 엔트리 기반 앱

### 8.2 미지원 (수동 작업 필요)

- **Django** — `manage.py` 감지, `migrate`, `collectstatic`, `<project>.wsgi:application` 자동 추출이 구현되어 있지 않음. 필요 시 별도 작업으로 추가 가능.
- **Celery 워커** — 웹 앱만 기동하며 worker/beat 프로세스는 관리하지 않음.

---

## 9. 주요 코드 레퍼런스

| 기능 | 파일 | 핵심 함수/상수 |
|---|---|---|
| Python 유틸리티 | `app/utils/python.py` | `find_python_project_root`, `detect_package_manager`, `effective_package_manager`, `install_command`, `test_command`, `build_command`, `strip_engine_managed_requirements`, `has_collectible_tests`, `find_test_directories`, `venv_python`, `create_venv_command`, `effective_python_executable` |
| install 단계 | `app/steps/install.py` | `_run_python_install`, `_ensure_python_venv`, `_ensure_python_manager_available` |
| test 단계 | `app/steps/test.py` | `_run_python_test`, `_ensure_python_runner_available` |
| build 단계 | `app/steps/build.py` | `_run_python_build`, `_create_python_fallback_artifacts`, `_ensure_python_build_tool_available`, `_PYTHON_BUILDABLE_MANAGERS` |
| deploy 단계 | `app/steps/deploy.py` | `_build_ec2_deploy_script` 내 `elif [ "$RUNTIME" = "python" ]` 분기, `_detect_runtime`, `run_deploy`의 runtime_type 파라미터 |
| 워크플로 reconcile | `app/workflow.py` | `detect_repo_runtime`, `_reconcile_workflow_runtime`, `_runtime_markers_present`, `_default_template_yaml_text` |
| 런타임 상수 | `app/constants.py` | `SUPPORTED_RUNTIMES = {"node", "python"}` |

---

## 10. 알려진 제약사항

1. **외부 서비스 의존**
   lifespan/startup에서 DB/Redis/외부 API에 필수로 연결하는 앱은 기동되지 않는다. 엔진은 `.env`를 자동 주입하지 않으며, 이런 앱은 레포 코드 또는 EC2 인프라에서 별도로 처리해야 한다.

2. **Django 미자동화**
   `manage.py` 감지, `migrate`, `collectstatic`, `<project>.wsgi:application` 엔트리 자동 추출이 없다.

3. **Celery / 워커 프로세스**
   웹 앱만 기동되며 백그라운드 워커는 관리하지 않는다.

4. **startup 타임아웃 8초**
   ML 모델 로딩 등 기동 시간이 긴 앱은 타임아웃으로 failed 처리될 수 있다. 필요 시 deploy 스크립트의 헬스 체크 루프를 조정.

5. **`src/` layout editable install**
   `pip install -e .`이 필요한 src-layout 패키지는 자동화되지 않음. 현재는 `requirements.txt` 있으면 `pip install -r requirements.txt`만 실행.

6. **`pyproject.toml` 엔진 관리 패키지 필터링 미지원**
   requirements.txt만 필터링 대상. pyproject.toml은 TOML 안전한 재작성이 복잡해 현재 건드리지 않는다.

7. **t3.micro 메모리**
   1GB RAM 환경에서 FastAPI + 대형 라이브러리(ML, 대규모 semgrep 등) 동시 구동 시 OOM 가능. 인프라 사이즈 문제.

---

## 11. 트러블슈팅

### 11.1 `install: failed (No valid package.json found (Node project required))`

런타임이 node로 잘못 잡혔다. 확인할 점:
- 레포에 Python 마커(`pyproject.toml`/`requirements.txt`/`setup.py`/`Pipfile`) 중 하나가 존재하는가
- 모노레포면 깊이 3 이내에 있는가
- `.localci/workflow.yml`이 레포에 커밋되어 있고 `runtime.type: node`로 고정되어 있으면서 `package.json`도 같이 있는가 — 이 경우 reconcile이 동작하지 않으므로 워크플로를 직접 수정하거나 package.json을 제거해야 한다.

### 11.2 `install: failed (Command not found: poetry ...)`

선언된 매니저가 엔진 호스트에 없고 `requirements.txt`도 없다. 해결 방법:
- 엔진 호스트에 `pipx install poetry` (또는 uv/pdm/hatch/pipenv) 실행, 또는
- 레포 프로젝트 루트에 `requirements.txt` 추가 → 자동으로 pip 폴백

### 11.3 `build: success (python build fell back to source artifact ...)`

정상 동작이다. `python -m build` 또는 `poetry build`가 패키지 레이아웃 누락으로 실패했지만 소스 트리 fallback이 생성되어 파이프라인이 계속 진행된다. `run_dir/artifacts/dist-python/`에 소스 트리와 `build_meta.json`이 남는다.

### 11.4 `deploy: failed` + `python app failed to bind port ... within 8s`

uvicorn/gunicorn 프로세스가 기동되지 않았다. `/opt/deployments/apps/<owner>/<repo>/app.stderr.log`에서 정확한 원인 확인. 자주 보는 원인:

- **DB 연결 실패** — `ConnectionRefusedError: [Errno 111]` 등. 레포의 lifespan이 startup 시 DB를 강제 요구하는 경우. 레포 수정 또는 EC2에 DB 설치 필요.
- **환경변수 누락** — `ValidationError: DATABASE_URL Field required`. pydantic-settings가 필수 환경변수를 찾지 못함. `.env` 파일을 EC2에 수동 배치하거나 레포의 Settings 기본값 조정.
- **의존성 누락/빌드 실패** — 네이티브 확장 모듈 빌드 실패. 빌드 도구(`gcc`, `python3-devel`) 설치 확인.
- **엔트리 포인트 미매치** — 비표준 경로면 엔진이 못 찾는다. 지원되는 엔트리 패턴(5.4의 표)에 맞게 레포 구조 조정.

### 11.5 `No space left on device` (이미 해결됨)

현재 엔진은 deploy 시 `TMPDIR=$APP_DIR/.pip-tmp`로 EBS 디스크를 사용하도록 설정되어 있어 tmpfs 부족 문제는 자동 회피된다. 여전히 발생하면 해당 앱 디렉터리의 여유 공간 확인(`df -h`).

### 11.6 `test: skipped (pytest collected no tests)`

정상 동작이다. 다음 중 하나:
- 레포의 test 디렉터리가 `__init__.py`만 있는 스캐폴드라 실제 테스트 파일이 없음
- pytest configfile에 `testpaths`가 잘못 지정되어 수집이 안 됨
- exit code 5를 skipped로 매핑하므로 파이프라인은 계속 진행됨

---

## 12. 검증된 시나리오

- [x] FastAPI 단일 레포 (루트 `app/main.py` → uvicorn)
- [x] FastAPI 모노레포 (`backend/app/main.py` → uvicorn, backend를 project_root로 인식)
- [x] `[tool.poetry]` 선언 + poetry 호스트 미설치 + `requirements.txt` 존재 → pip 자동 폴백
- [x] `requirements.txt`에 `semgrep>=1.50.0` 포함 → 자동 필터링 후 정상 설치 (tmpfs OOM 회피)
- [x] `poetry-core` 빌드 백엔드 + 패키지 레이아웃 누락 → 소스 fallback 빌드 success
- [x] `backend/tests/` 스캐폴드(__init__.py만 존재) → skipped 처리
- [x] venv 기반 pip install (PEP 668 externally-managed 환경)
- [x] ASGI 프레임워크 자동 감지 (fastapi → uvicorn)
- [x] 엔트리 자동 탐색 (`app/main.py` → `app.main:app`)
- [x] EC2 배포 후 nginx 프록시 연결 및 헬스 체크

미검증 / 수동 테스트 필요:
- [ ] Flask WSGI 앱 (코드상 지원, 실레포 미테스트)
- [ ] uv / pdm / hatch 실레포 (로직 구현만 되어 있음)
- [ ] WebSocket 엔드포인트 (nginx Upgrade 헤더는 이미 설정됨)
