# Java 런타임 지원 매뉴얼

이 문서는 CI/CD 엔진에 **Java 런타임 대응으로 추가된 기능만** 정리한 것이다. 기존 Node / Python 런타임 동작은 변경되지 않았으며 이 문서의 대상이 아니다.

---

## 1. 개요

파이프라인은 이제 Java 레포를 Node/Python 레포와 동일한 단계(`install → lightweight_security → test → deep_security → build → deploy`)로 처리한다. 워크플로 YAML의 `runtime.type: java` 선언으로 활성화되며, 명시가 없어도 레포 마커로 자동 감지된다.

### 활성화 방법

```yaml
# .localci/workflow.yml
name: my-java-workflow
runtime:
  type: java
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

파일이 없으면 엔진이 레포를 스캔해서 Java 프로젝트로 판단될 경우 위 템플릿을 자동 생성한다.

---

## 2. 지원 빌드 도구

Java는 별도의 "패키지 매니저" 대신 빌드 도구가 의존성 관리까지 담당한다. 현재 엔진은 두 가지 빌드 도구를 지원한다.

| 빌드 도구 | 감지 기준 | install(의존성) | test | build |
|---|---|---|---|---|
| **Maven** | `pom.xml` | `mvn -B -ntp -DskipTests dependency:go-offline` | `mvn -B -ntp test` | `mvn -B -ntp -DskipTests package` |
| **Gradle** | `build.gradle` / `build.gradle.kts` / `settings.gradle*` | `gradle --no-daemon -q dependencies` | `gradle --no-daemon test` | `gradle --no-daemon -x test build` 또는 `bootJar` (Spring Boot) |

- `-B` / `-ntp`: Maven batch 모드(비대화식) + no-transfer-progress (로그 깔끔하게)
- `--no-daemon`: Gradle 데몬 비활성화 (CI 환경에서 백그라운드 프로세스 남지 않도록)
- `-x test`: Gradle `build` 태스크에서 테스트 재실행 건너뛰기 (이미 test 단계에서 실행됨)

### 2.1 빌드 도구 감지 우선순위

1. `build.gradle`, `build.gradle.kts`, `settings.gradle`, `settings.gradle.kts` 중 하나라도 존재 → **gradle**
2. `pom.xml` 존재 → **maven**
3. 둘 다 없으면 **maven** (기본값)

드물지만 Maven/Gradle 마커가 같이 존재하는 멀티빌드 레포는 Gradle을 우선한다.

### 2.2 Wrapper 우선 사용

프로젝트에 동봉된 빌드 도구 wrapper를 **시스템 전역 설치보다 우선**해서 사용한다. 이렇게 하면 각 레포가 자체 버전을 고정할 수 있고, 엔진 호스트에 Maven/Gradle이 설치되지 않아도 동작한다.

| 빌드 도구 | 우선순위 1 (wrapper) | 우선순위 2 (호스트) |
|---|---|---|
| Maven | `./mvnw` (Unix) / `./mvnw.cmd` (Windows) | `mvn` |
| Gradle | `./gradlew` (Unix) / `./gradlew.bat` (Windows) | `gradle` |

**권한 복구**: Git on Windows 또는 zip 배포 등으로 wrapper의 `+x` 실행 비트가 소실된 경우, 엔진이 install/test/build 직전에 자동으로 `chmod +x`를 적용한다 (`ensure_wrapper_executable`).

### 2.3 호스트 도구 미설치 시

wrapper가 없고 시스템에도 도구가 없으면 다음 에러로 중단:

```
Command not found: maven (install it on the engine host or include the maven wrapper in the repo)
```

해결 방법:
- 레포에 `mvnw` / `gradlew` wrapper 포함 (권장)
- 또는 엔진 호스트에 `apt install maven` / `apt install gradle` 등

---

## 3. 프로젝트 구조 감지

### 3.1 단일 레포

레포 루트에 다음 마커 중 하나가 있으면 Java 프로젝트로 간주한다:

- `pom.xml` (Maven)
- `build.gradle` (Gradle, Groovy DSL)
- `build.gradle.kts` (Gradle, Kotlin DSL)
- `settings.gradle`
- `settings.gradle.kts`

### 3.2 모노레포

루트에 마커가 없으면 BFS 얕은 스캔(기본 최대 깊이 3)으로 하위 디렉터리를 탐색한다. 첫 번째로 마커가 발견된 디렉터리가 **project_root**가 되며, install/test/build의 모든 명령은 그 디렉터리를 `cwd`로 사용한다.

예시 — 다음 구조에서 `backend/`가 project_root가 된다:

```
repo/
├── docs/
├── frontend/         (무시됨: 마커 없음)
├── backend/
│   ├── pom.xml          ← 마커
│   ├── mvnw
│   └── src/
│       └── main/java/com/example/App.java
└── README.md
```

스캔에서 제외되는 디렉터리:
`.git`, `.gradle`, `.idea`, `.vscode`, `build`, `target`, `out`, `bin`, `node_modules`, `.mvn`, `.cache`, `.`으로 시작하는 디렉터리 전부

---

## 4. 런타임 자동 감지 및 reconcile

### 4.1 감지 규칙

```
1. package.json 존재             → node
2. Python 마커 (pyproject.toml 등) → python
3. Java 마커 (pom.xml / build.gradle*) → java
4. 그 외                          → node (기본값)
```

### 4.2 Reconcile (자동 교체)

레포에 `.localci/workflow.yml`이 이미 존재하더라도, 선언된 런타임의 마커가 레포에 **없고** 다른 런타임 마커만 **있으면** 자동으로 교체한다.

예: 커밋된 워크플로가 `runtime.type: node`인데 레포에는 `package.json`이 없고 `backend/pom.xml`만 있음 → `runtime.type`이 `java`로 교체되어 진행.

---

## 5. 단계별 상세 동작

### 5.1 install

```
1. find_java_project_root(repo_dir)로 모노레포 대응 project_root 결정
2. detect_build_tool(project_root)로 Maven/Gradle 판별
3. ensure_wrapper_executable(project_root, build_tool)로 wrapper 권한 복구
4. has_wrapper 확인 → wrapper 없고 호스트에도 도구 없으면 명확한 에러로 중단
5. 의존성 해결 명령 실행 (project_root를 cwd로):
   - maven: mvn -B -ntp -DskipTests dependency:go-offline
   - gradle: gradle --no-daemon -q dependencies
6. exit 0 → success, 그 외 → failed
```

**왜 `dependency:go-offline`인가**: Java는 "의존성 설치"와 "빌드"가 한 번에 이루어지는 도구가 대부분이지만, CI 환경에서는 먼저 의존성을 전부 로컬 저장소 캐시로 내려받아 두면 이후 test/build가 오프라인 친화적으로 동작하고, 네트워크 의존성 실패가 install 단계에서 명확히 드러난다.

### 5.2 test

```
1. find_java_project_root로 project_root 재결정
2. has_test_files(project_root) 검사
   └─ src/test/java, src/test/kotlin, src/test/groovy, test/ 탐색
   └─ .java / .kt / .kts / .groovy 파일이 하나라도 있으면 True
3. 테스트 파일이 없으면 skipped
4. wrapper 권한 복구
5. 실행:
   - maven: mvn -B -ntp test
   - gradle: gradle --no-daemon test
6. exit 0 → success, 그 외 → failed
```

JUnit 4/5, TestNG, Spock 모두 빌드 도구의 `test` 태스크가 자동으로 처리하므로 엔진 수준에서 별도 분기는 없다.

### 5.3 build

```
1. find_java_project_root로 project_root 재결정
2. wrapper 권한 복구 및 가용성 확인
3. 빌드 명령 결정:
   - maven          → mvn -B -ntp -DskipTests package
   - gradle (일반)   → gradle --no-daemon -x test build
   - gradle (Spring) → gradle --no-daemon -x test bootJar   ← 자동 감지
4. 빌드 실행
5. 아티팩트 수집:
   - maven  → target/*.jar, *.war, *.ear
   - gradle → build/libs/*.jar, build/distributions/*.jar
6. is_deployable_artifact 필터링:
   - 제외: *-sources.jar, *-javadoc.jar, *-tests.jar, original-*.jar
7. artifacts_dir로 평탄 복사 (run_dir/artifacts/<파일명>.jar)
8. build_meta.json 기록 (빌드 도구, 수집된 아티팩트 목록)
```

**Spring Boot 자동 감지** (`is_spring_boot_project`): `pom.xml` / `build.gradle*`에 `spring-boot` 또는 `org.springframework.boot` 문자열이 포함되어 있으면 Gradle에서는 일반 `build` 대신 `bootJar` 태스크를 사용한다. 이렇게 하면 실행 가능한 fat JAR만 생성되고, `-plain.jar` 같은 보조 아카이브가 만들어지지 않는다.

**실패 처리**: Java build는 Python과 달리 소스 트리 fallback을 만들지 않는다. Java 앱은 컴파일된 JAR/WAR 없이는 배포할 수 없기 때문이다. 빌드가 성공했는데 아티팩트가 하나도 없으면 `failed`로 명확히 표시한다.

### 5.4 deploy

EC2에서 SSM으로 실행되는 bash 스크립트가 Java 전용 분기로 동작한다.

```
1. APP_ROOT 결정:
   Java는 빌드 단계에서 JAR을 artifacts_dir로 이미 평탄 복사했으므로
   APP_ROOT = $APP_DIR (아티팩트가 바로 배포 디렉터리 루트에 위치)

2. JVM 선택:
   /usr/lib/jvm/java-21-amazon-corretto/bin/java →
   /usr/lib/jvm/java-17-amazon-corretto/bin/java →
   /usr/lib/jvm/default-java/bin/java →
   /usr/bin/java
   순으로 첫 번째 실행 가능한 파일 사용. 없으면 exit 1.

3. 배포 아카이브 선택:
   find $APP_ROOT -maxdepth 2 -type f \( -name "*.jar" -o -name "*.war" -o -name "*.ear" \)
   → 파일 크기 내림차순 정렬
   → *-sources.jar / *-javadoc.jar / *-tests.jar / original-* 건너뛰기
   → 첫 번째로 매치되는 파일을 ARCHIVE로 선택
   (Spring Boot fat JAR은 보통 가장 크기가 크므로 우선 선택됨)

4. 기존 프로세스 정리:
   - $APP_DIR/.app.pid → kill → kill -9
   - $APP_DIR/.java.pid (레거시 경로)
   - pkill -f "java.*<owner>--<repo>"

5. 백그라운드 실행:
   SERVER_PORT=$PORT PORT=$PORT nohup $JAVA_BIN \
     -Dserver.port=$PORT \
     -Dspring.profiles.active=${SPRING_PROFILES_ACTIVE:-prod} \
     -jar "$ARCHIVE" \
     --server.port=$PORT \
     > $APP_DIR/app.stdout.log 2> $APP_DIR/app.stderr.log < /dev/null &

   APP_PID → $APP_DIR/.app.pid

6. 헬스 체크 (25초):
   Python은 8초, Java는 Spring Boot warmup을 고려해 25초로 확장.
   1초 간격으로 ss -tlnp에서 :$PORT 바인딩 확인.
   프로세스가 바인딩 전 사망하면(kill -0 실패) 즉시 중단.
   25초 내 바인딩 실패 시:
     - stderr 마지막 80줄 출력
     - stdout 마지막 40줄 출력
     - exit 1 (파이프라인 failed 표시)

7. nginx 프록시 등록:
   /opt/deployments/nginx/<owner>__<repo>.conf에 proxy_pass 작성
   nginx -t && systemctl reload nginx
```

**포트 전달 이중화**: Spring Boot는 `--server.port=<n>` CLI 인자, `-Dserver.port=<n>` 시스템 속성, `SERVER_PORT` 환경변수 모두를 인식한다. 일반 Java 앱은 `--server.port`를 무시하지만 `PORT` 환경변수는 읽는 경우가 많으므로 세 가지 방식을 모두 넣어서 최대한 호환되게 했다.

**Spring profile**: `SPRING_PROFILES_ACTIVE` 환경변수가 설정되어 있으면 그 값을, 아니면 `prod`를 기본값으로 활성화한다.

---

## 6. 엔진 관리 패키지 자동 필터링

Python/Node과 달리 Java는 **자동 필터링을 적용하지 않는다.** 이유:

- Java 의존성은 `pom.xml`/`build.gradle`의 구조화된 XML/DSL에 선언되어 있어, 안전한 in-place 재작성이 복잡하다 (들여쓰기, 코멘트, 주석, 동적 버전 처리 등)
- Java 생태계에서는 `semgrep`/`gitleaks`가 Maven/Gradle 의존성으로 선언되는 경우가 매우 드물다 (주로 CI 단계에서 별도 실행)
- 대부분의 Java 레포는 보안 스캐너를 `plugins` 섹션이나 별도 `profile`로 관리하는데, 이것들은 `mvn test`/`gradle test` 실행 중 `-DskipTests` 또는 일반 빌드 태스크에서 자동으로 비활성화된다

엔진은 대신 `ENGINE_MANAGED_JAVA_ARTIFACTS = {"semgrep", "gitleaks"}` 상수를 유지해 향후 필요 시 플러그인/의존성 필터링을 추가할 수 있도록 준비해두었다. 현재는 문서상 표식 역할만 한다.

---

## 7. 자동 생성되는 Java 워크플로 템플릿

레포에 워크플로 파일이 없을 때, Java로 감지되면 다음 템플릿이 `.localci/workflow.yml`로 생성된다:

```yaml
name: default-generated-workflow
runtime:
  type: java
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

- **Spring Boot** — 자동 감지(`spring-boot` / `org.springframework.boot` 문자열), Gradle에서 `bootJar` 태스크 우선, 실행 시 `--server.port` / `-Dserver.port` / `SERVER_PORT` 포트 바인딩
- **Spring Framework** (Boot 미사용) — 일반 `build` / `package`로 생성된 JAR/WAR로 실행되며 포트는 `PORT` 환경변수로 전달
- **Micronaut / Quarkus / Helidon** — 빌드 도구 표준 태스크(`mvn package`, `gradle build`)를 따르므로 일반 JAR 흐름으로 동작. 단, 포트 설정은 프레임워크별 환경변수(`MICRONAUT_SERVER_PORT`, `QUARKUS_HTTP_PORT` 등)를 별도로 설정해야 할 수 있다.
- **Dropwizard / Plain JAX-RS / 순수 HTTP 서버** — fat JAR로 빌드되면 그대로 실행됨
- **Kotlin / Groovy** 테스트 소스 — `src/test/kotlin`, `src/test/groovy` 탐색에 포함

### 8.2 제한적 지원

- **WAR 배포** — `*.war`를 찾아 그대로 `java -jar`로 실행하지만, 일부 WAR은 Tomcat/Jetty 같은 서블릿 컨테이너 필수. 이런 경우 별도 컨테이너 설정 필요.
- **Micronaut / Quarkus** 포트 설정 — 위에서 언급한 프레임워크별 환경변수는 엔진이 자동으로 설정하지 않음

### 8.3 미지원

- **Ant** (`build.xml`) — 감지/빌드 로직 없음. 최신 프로젝트에서 거의 사용되지 않음.
- **Bazel** — 감지/빌드 로직 없음.
- **멀티 모듈 Maven 하위 특정 모듈 선택** — `mvn -pl submodule` 같은 세부 제어는 워크플로 YAML의 custom `run` 스텝으로 수동 작성 필요.

---

## 9. 주요 코드 레퍼런스

| 기능 | 파일 | 핵심 함수/상수 |
|---|---|---|
| Java 유틸리티 | `app/utils/java.py` | `find_java_project_root`, `is_java_project`, `detect_build_tool`, `has_wrapper`, `build_tool_executable`, `ensure_wrapper_executable`, `install_command`, `test_command`, `build_command`, `is_spring_boot_project`, `artifact_directories`, `is_deployable_artifact`, `has_test_files`, `java_home_hint` |
| install 단계 | `app/steps/install.py` | `_run_java_install` |
| test 단계 | `app/steps/test.py` | `_run_java_test` |
| build 단계 | `app/steps/build.py` | `_run_java_build`, `_collect_java_build_artifacts` |
| deploy 단계 | `app/steps/deploy.py` | `_build_ec2_deploy_script` 내 `elif [ "$RUNTIME" = "java" ]` 분기, `_detect_runtime`, `run_deploy`의 runtime_type 파라미터 |
| 워크플로 reconcile | `app/workflow.py` | `detect_repo_runtime`, `_reconcile_workflow_runtime`, `_runtime_markers_present`, `_default_template_yaml_text` |
| 런타임 상수 | `app/constants.py` | `SUPPORTED_RUNTIMES = {"node", "python", "java"}` |

---

## 10. 알려진 제약사항

1. **외부 서비스 의존**
   Spring Boot 등이 startup 시 Postgres/Redis/외부 API에 필수로 연결하는 앱은 기동되지 않는다. 엔진은 환경변수(`DATABASE_URL`, `SPRING_DATASOURCE_URL` 등)를 자동 주입하지 않으며, 이런 앱은 레포 코드 또는 EC2 인프라에서 별도로 처리해야 한다.

2. **멀티 모듈 프로젝트**
   Maven `<modules>` 또는 Gradle `include()` 기반 멀티 모듈은 루트에서 빌드만 수행한다. 특정 서브모듈만 빌드/배포하고 싶으면 워크플로 YAML에 custom `run` 스텝으로 지정해야 한다.

3. **프레임워크별 포트 환경변수**
   Micronaut(`MICRONAUT_SERVER_PORT`), Quarkus(`QUARKUS_HTTP_PORT`) 등은 엔진이 자동으로 설정하지 않는다. Spring Boot 계열이 아니면 레포에서 `PORT` 또는 `SERVER_PORT` 환경변수를 읽도록 설정하거나, `.localci/workflow.yml`에서 deploy 스텝을 custom `run`으로 덮어써야 한다.

4. **Startup 타임아웃 25초**
   Spring Boot warmup을 고려해 25초로 설정되어 있지만, 초기 DB 스키마 마이그레이션이나 Liquibase 같은 기동 시 로직이 긴 앱은 타임아웃될 수 있다. 필요 시 deploy 스크립트의 헬스 체크 루프 상수를 조정.

5. **빌드 도구 자동 설치 미지원**
   wrapper(`mvnw`/`gradlew`)가 레포에 없고 호스트에 `mvn`/`gradle`도 없으면 엔진이 임의로 설치하지 않는다. 명확한 에러로 중단된다. 해결: 레포에 wrapper 커밋 (권장) 또는 호스트에 직접 설치.

6. **Ant / Bazel 미지원**
   `build.xml` / `BUILD`(Bazel) 파일은 감지도 빌드도 처리하지 않는다.

7. **의존성 필터링 미지원**
   Python/Node과 달리 `pom.xml` / `build.gradle`에서 `semgrep`/`gitleaks`를 자동 제거하지 않는다. Java 레포에서 이 두 도구가 의존성으로 선언되는 경우가 드물기 때문에 현재로선 필요성이 낮다.

8. **fat JAR이 아닌 일반 JAR**
   Spring Boot가 아닌 일반 Java 앱에서 `gradle build`가 만드는 `thin JAR`은 런타임 클래스패스에 의존 라이브러리가 없어 `NoClassDefFoundError`가 발생할 수 있다. 해결: `gradle shadow` 플러그인 사용 또는 `application` 플러그인의 `distTar` 사용.

---

## 11. 트러블슈팅

### 11.1 `install: failed (Command not found: maven ...)`

wrapper도 없고 호스트에도 Maven이 설치되어 있지 않다. 해결:
- 레포에 Maven wrapper 포함: `mvn -N wrapper:wrapper` 실행 후 `mvnw`, `mvnw.cmd`, `.mvn/` 디렉터리 커밋
- 또는 엔진 호스트에 `apt install maven` / `yum install maven` 등

Gradle도 동일: `gradle wrapper` 후 `gradlew`, `gradlew.bat`, `gradle/` 커밋.

### 11.2 `install: failed (java dependency resolution failed ...)`

의존성을 Maven Central이나 회사 내부 저장소에서 받는 중 실패했다. 확인:
- 엔진 호스트에서 외부 네트워크 접근 가능한가
- `pom.xml` / `build.gradle`의 repository URL이 유효한가
- 인증이 필요한 내부 저장소면 `~/.m2/settings.xml` / `~/.gradle/gradle.properties`에 자격증명 필요

### 11.3 `build: failed (java build produced no deployable JAR/WAR)`

빌드는 성공했지만 `target/`이나 `build/libs/`에 수집할 JAR/WAR가 없다. 자주 보는 원인:
- `pom.xml`의 `<packaging>pom</packaging>` — 부모 pom은 아티팩트를 만들지 않음. 실제 실행 가능한 서브모듈을 빌드해야 함.
- Gradle `settings.gradle`만 있고 실제 모듈이 없는 경우
- 빌드 태스크가 `classes`만 컴파일하고 JAR 패키징을 생략한 경우 (`-x jar` 등)

### 11.4 `deploy: failed` + `no deployable JAR/WAR/EAR found under $APP_ROOT`

build 단계에서 아티팩트를 artifacts_dir로 복사했는데 EC2에서 찾지 못했다. 확인:
- `runs/run-XXX/artifacts/`에 `*.jar`이 있는가
- S3 업로드 로그에 해당 파일이 올라갔는가
- EC2의 `/opt/deployments/apps/<owner>/<repo>/`에 `*.jar`이 있는가

### 11.5 `deploy: failed` + `java app failed to bind port ... within 25s`

JVM 프로세스가 기동되지 못했거나 너무 느리다. `/opt/deployments/apps/<owner>/<repo>/app.stderr.log`를 확인한다. 자주 보는 원인:

- **DB 연결 실패** — `Unable to acquire JDBC Connection` / `Connection refused`. Spring Boot 가 `spring.datasource.url`로 Postgres에 접속하려다 실패. DB 준비 또는 레포에서 `spring.datasource.initialization-mode=never` 같은 옵션 필요.
- **포트 충돌** — `Address already in use: bind`. 이전 프로세스가 제대로 종료되지 않았을 때. 엔진은 배포 시작 시 pid/pkill로 정리하지만 드물게 누락될 수 있다.
- **JVM 메모리 부족** — t3.micro(1GB RAM)에서 기본 힙 크기가 크면 기동 중 OOM. `-Xmx256m` 같은 JVM 옵션을 deploy 스텝에서 custom run으로 덮어써야 함.
- **Liquibase / Flyway 마이그레이션 지연** — 대규모 스키마 변경이 25초 이상 걸리면 헬스 체크 타임아웃. 헬스 체크 루프 상수를 늘리거나 마이그레이션을 배포 전 단계로 분리.
- **`main` 메서드 없음** — 일반 JAR(thin JAR)을 `java -jar`로 실행하려고 할 때. Spring Boot fat JAR 또는 Gradle `shadow` 플러그인으로 실행 가능한 JAR을 만들어야 함.

### 11.6 `test: skipped (No java test sources found)`

정상 동작이다. `src/test/java` (또는 kotlin/groovy) 아래에 `.java`/`.kt`/`.groovy` 파일이 하나도 없으면 skipped로 처리한다. `test/` 같은 비표준 디렉터리는 감지하지만 수집 가능한 소스가 실제로 있어야 한다.

### 11.7 Spring Boot fat JAR과 `-plain.jar`이 함께 생성됨

Gradle `bootJar`와 `jar` 태스크가 같이 실행되면 `myapp-0.0.1-SNAPSHOT.jar` (fat) + `myapp-0.0.1-SNAPSHOT-plain.jar` (일반)이 둘 다 생성된다. 엔진은 배포 시 파일 크기 내림차순으로 첫 번째 유효 JAR을 선택하므로 fat JAR이 우선 선택된다. 일반적으로는 자동 처리되지만 혼란을 피하려면 `build.gradle`에 다음을 추가:

```gradle
tasks.named('jar') {
    enabled = false
}
```

---

## 12. 검증된 시나리오

- [x] Maven 모노레포 (`backend/pom.xml`) + Spring Boot + `mvnw` wrapper
- [x] Gradle 단일 레포 (`build.gradle.kts`) + Spring Boot + `gradlew` wrapper
- [x] 빌드 도구 자동 감지 (gradle > maven 우선순위)
- [x] Spring Boot 자동 감지 → `bootJar` 태스크 선택
- [x] 커밋된 node 워크플로 + 실제로는 Java 레포 → reconcile로 `java` 자동 교체
- [x] 자동 생성되는 Java 워크플로 템플릿 (install → lightweight → test → deep → build → deploy)
- [x] `src/test/java` 테스트 소스 탐색
- [x] Maven `target/` 및 Gradle `build/libs/` 아티팩트 수집
- [x] `-sources.jar` / `-javadoc.jar` / `-tests.jar` / `original-*` 자동 제외

미검증 / 수동 테스트 필요:
- [ ] 실제 Spring Boot 레포 EC2 배포 후 `/health` 응답 확인
- [ ] WAR 배포 (Tomcat 임베디드 또는 외부 서블릿 컨테이너)
- [ ] Micronaut / Quarkus 레포
- [ ] Kotlin/Groovy 메인 소스 레포
- [ ] 멀티 모듈 Maven 프로젝트
- [ ] 25초 이상 걸리는 느린 startup (Liquibase, 대형 schema init)
