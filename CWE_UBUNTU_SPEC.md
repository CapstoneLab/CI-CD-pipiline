# 로컬 CI 엔진 상세 명세

## 1. 문서 목적

이 문서는 아래 두 가지를 구현하기 위한 상세 명세를 정의한다.

- CWE 기반 취약점 선택 검사 기능
- 로컬 Windows 중심 MVP를 Ubuntu 중심 실행 환경으로 최소 이관

중요 원칙:

- DB 추가 없음
- API 서버 추가 없음
- 기존 단일 실행 구조 유지
- 결과는 기존처럼 파일 기반(JSON + 로그) 유지

## 2. 범위

### 2.1 포함

- 사용자가 원하는 CWE 항목만 선택해서 deep security scan(semgrep) 결과를 판정
- 선택 항목 기반 fail/pass 정책 적용
- Ubuntu에서 엔진 실행에 필요한 최소 운영 명세 정의

### 2.2 제외

- 사용자 인증/권한
- 웹 UI
- DB 저장
- 분산 Runner
- 큐 시스템
- Webhook

## 3. CWE 선택 검사 명세

## 3.1 기능 목표

사용자는 실행 시 "어떤 CWE를 검사 기준으로 삼을지"를 선택할 수 있어야 한다.

예시 요구:

- CWE-79, CWE-89만 게이트로 사용
- 나머지 탐지는 리포트에는 남기되 차단하지 않음

## 3.2 입력 인터페이스

CLI 옵션(권장):

- --cwe CWE-79,CWE-89,CWE-798
- --cwe-file ./cwe_policy.json
- --cwe-preset top25
- --cwe-strict true|false

동작 우선순위:

1. --cwe
2. --cwe-file
3. --cwe-preset
4. 기본값

기본값(권장):

- 선택 CWE 없음 = 전체 탐지는 수행하되 차단은 CVSS 정책으로만 수행

## 3.3 정책 파일 스키마

파일명 예시: cwe_policy.json

```json
{
  "enabled": true,
  "selected_cwe": ["CWE-79", "CWE-89", "CWE-798"],
  "block_mode": "selected_only",
  "cvss_threshold": 9.0,
  "treat_missing_cwe_as": "report_only",
  "presets": ["owasp-api", "cwe-top25"]
}
```

필드 설명:

- enabled: CWE 정책 사용 여부
- selected_cwe: 사용자 선택 CWE 목록
- block_mode:
  - selected_only: 선택 CWE만 차단 판정
  - all: 전체 CWE 대상 차단
- cvss_threshold: 차단 임계치
- treat_missing_cwe_as:
  - report_only: CWE 매핑 불가 항목은 리포트만
  - block: 매핑 불가 항목도 차단 대상으로 간주
- presets: 사전 정의 집합 이름

## 3.4 엔진 처리 흐름

deep_security_scan 단계 처리 순서:

1. semgrep JSON 생성
2. finding 단위로 메타데이터 파싱
3. finding의 CWE 목록 추출
4. selected_cwe와 교집합 계산
5. 차단 대상 finding 결정
6. cvss_threshold 적용
7. step status 산출

판정 규칙(권장 기본):

- 차단 대상 finding 중 cvss >= 9.0 1건 이상이면 failed
- 그 외는 success
- 모든 finding은 security_findings.json에 기록

## 3.5 finding 파싱 규칙

semgrep finding에서 우선 추출할 키:

- extra.metadata.cwe
- extra.metadata.owasp
- extra.severity
- extra.metadata.security-severity

CWE 값 정규화 규칙:

- CWE-79 형태로 통일
- 중복 제거
- 대소문자/공백/괄호 제거

정규화 예시:

- "CWE-89: Improper Neutralization..." -> CWE-89
- "cwe-79" -> CWE-79

## 3.6 결과 저장 확장

pipeline_result.json에 추가:

```json
{
  "policy": {
    "cwe": {
      "enabled": true,
      "selected_cwe": ["CWE-79", "CWE-89"],
      "block_mode": "selected_only",
      "cvss_threshold": 9.0,
      "treat_missing_cwe_as": "report_only"
    }
  }
}
```

security_summary.json 항목 확장:

```json
{
  "scanner_name": "semgrep",
  "selected_cwe_hit_count": 12,
  "selected_cwe_block_count": 2,
  "selected_cwe_list": ["CWE-79", "CWE-89"]
}
```

security_findings.json finding 확장:

```json
{
  "scanner_name": "semgrep",
  "rule_id": "...",
  "cwe_ids": ["CWE-79"],
  "matched_selected_cwe": true,
  "is_blocking_by_policy": true,
  "cvss_score": 9.3
}
```

## 3.7 경량/심화 정책 관계

권장 정책:

- lightweight_security_scan(gitleaks): non-blocking
- deep_security_scan(semgrep): CWE + CVSS 기준으로 blocking

예외:

- gitleaks 실행 자체 실패(도구 없음/비정상 종료)는 failed

## 3.8 수용 기준

- selected_cwe 미설정: 기존 동작과 호환
- selected_cwe 설정: 선택 CWE에만 차단 적용
- 선택되지 않은 CWE 탐지는 리포트에 기록되지만 차단하지 않음
- 파이프라인 결과 파일에 정책 정보가 남음

## 4. Ubuntu 최소 이관 명세

## 4.1 이관 목표

다듬기 목적의 최소 이관:

- 엔진 실행 환경만 Ubuntu로 이동
- 호출/관리 주체는 기존 로컬에서도 가능

## 4.2 경계 정의

Ubuntu가 담당하는 범위:

- main 실행
- orchestrator
- clone/install/lightweight/test/deep/build
- 결과 파일 저장

Ubuntu 밖 범위:

- 사용자 입력 UI
- 향후 API/DB

## 4.3 디렉토리 표준

권장 경로:

- /opt/local-ci-engine
- /opt/local-ci-engine/app
- /opt/local-ci-engine/runs
- /opt/local-ci-engine/workspace
- /opt/local-ci-engine/scripts
- /var/log/local-ci-engine

권한 원칙:

- ci-engine 전용 유저 생성
- runs/workspace만 쓰기 허용

## 4.4 필수 패키지

Ubuntu 필수:

- git
- python3
- nodejs
- npm
- semgrep
- gitleaks
- chromium 또는 테스트 실행 브라우저

버전 고정 권장:

- node LTS 고정
- semgrep/gitleaks 최소 버전 고정

## 4.5 실행 스크립트

파일: scripts/run_pipeline.sh

필수 내용:

- set -euo pipefail
- 작업 경로 이동
- 도구 존재 체크
- main.py 실행
- 종료코드 전달

예시:

```bash
#!/usr/bin/env bash
set -euo pipefail

cd /opt/local-ci-engine

command -v git >/dev/null
command -v node >/dev/null
command -v npm >/dev/null
command -v gitleaks >/dev/null
command -v semgrep >/dev/null

python3 main.py --repo "$1" --branch "${2:-}"
```

## 4.6 최소 운영 방식

1차(가장 최소):

- 수동 실행
- 실행마다 run 폴더 생성

2차(여전히 최소):

- systemd service 1개
- 필요 시 systemd timer 1개

## 4.7 로그/결과 관리

유지 원칙:

- 단계 로그: runs/<run_id>/logs/*.log
- 결과 JSON: runs/<run_id>/*.json

보관 정책(권장):

- 최근 50개 run 유지
- 그 외 자동 정리

## 4.8 보안/안정성 최소 기준

- 네트워크 아웃바운드 허용 도메인 제한(가능 시)
- 실행 사용자 최소 권한
- 워크스페이스 디렉토리 외 쓰기 금지
- 디스크 사용량 임계치 경고

## 4.9 이관 수용 기준

- Ubuntu에서 동일 repo 실행 시 단계 순서 동일
- clone/install/test/deep/build 종료코드 일관
- 결과 JSON 스키마 동일
- 실패 원인이 로그에서 즉시 식별 가능

## 5. 최소 작업 체크리스트

1. Ubuntu 서버/VM 준비
2. ci-engine 유저 생성
3. 필수 패키지 설치
4. 엔진 코드 배포
5. runs/workspace 디렉토리 생성 및 권한 설정
6. 실행 스크립트 추가
7. smoke run 1회 성공 확인
8. 보관 정책 스크립트 추가
9. 정책 파일(cwe_policy.json) 배치
10. 재실행 결과 비교 검증

## 6. 단계별 도입 계획

1주차:

- Ubuntu에서 현재 파이프라인 실행 재현
- 도구 버전 고정
- smoke 테스트 고정

2주차:

- CWE 선택 정책 CLI 입력 추가
- semgrep parsing에 CWE 필터 적용
- 결과 JSON 필드 확장

3주차:

- 시스템 서비스화(systemd)
- 로그 보관 정책 자동화
- 운영 체크리스트 문서 확정

## 7. 리스크와 대응

리스크:

- semgrep 룰/결과 메타데이터에 CWE가 없는 finding 존재
- 리포지토리마다 build 스크립트 이름 상이
- 브라우저 테스트 환경 차이

대응:

- treat_missing_cwe_as 정책 제공
- build 스크립트 fallback 유지
- Chromium/Headless 의존 패키지 표준화

## 8. 최종 정의

최소 이관 완료의 정의:

- Ubuntu에서 단일 명령으로 파이프라인이 실행되고,
- CWE 선택 정책이 deep scan 차단 조건에 반영되며,
- 결과와 로그가 파일로 일관되게 남는 상태
