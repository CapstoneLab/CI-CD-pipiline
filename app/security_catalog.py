"""16-item security policy catalog.

Single source of truth mapping CWE -> policy item -> grade -> deduction,
per the security policy manual. The scanner severity from semgrep/gitleaks is
NOT trusted for these 16 items; grade is derived from the CWE here so that, e.g.,
SQL Injection (CWE-89) is always Critical regardless of how the tool labelled it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


GRADE_CRITICAL = "critical"
GRADE_HIGH = "high"
GRADE_MEDIUM = "medium"
GRADE_LOW = "low"

# Per-finding deduction by grade.
GRADE_DEDUCTION: dict[str, float] = {
    GRADE_CRITICAL: 20.0,
    GRADE_HIGH: 5.0,
    GRADE_MEDIUM: 1.0,
    GRADE_LOW: 0.2,
}

# Per-grade caps: the total deduction of a lower grade can never reach a single
# finding of the next grade up (19<20, 4<5, 0.9<1). Critical is uncapped (it
# forces the danger zone via the gate instead).
GRADE_CAP: dict[str, float] = {
    GRADE_HIGH: 19.0,
    GRADE_MEDIUM: 4.0,
    GRADE_LOW: 0.9,
}


@dataclass(frozen=True)
class CatalogItem:
    key: str          # stable slug used by frontend/backend selection
    name: str         # human-readable item name
    cwe: str          # canonical "CWE-###"
    grade: str        # critical | high | medium | low
    description: str

    @property
    def deduction(self) -> float:
        return GRADE_DEDUCTION[self.grade]


# Order: Critical 4 / High 4 / Medium 4 / Low 4 = 16 items.
CATALOG: tuple[CatalogItem, ...] = (
    # ---- Critical (-20) : attacker needs nothing ----
    CatalogItem("sql-injection", "SQL Injection", "CWE-89", GRADE_CRITICAL,
                "입력값이 SQL 쿼리에 직접 삽입, 인증 우회·DB 조작"),
    CatalogItem("command-injection", "Command Injection", "CWE-78", GRADE_CRITICAL,
                "입력값이 시스템 명령어로 실행됨"),
    CatalogItem("hardcoded-secret", "Hardcoded Secret", "CWE-798", GRADE_CRITICAL,
                "코드에 비밀번호·API 키 하드코딩"),
    CatalogItem("code-injection", "Code Injection", "CWE-94", GRADE_CRITICAL,
                "입력값이 코드로 직접 실행됨 (eval 등)"),
    # ---- High (-5) : one logged-in account is enough ----
    CatalogItem("insecure-deserialization", "Insecure Deserialization", "CWE-502", GRADE_HIGH,
                "역직렬화 시 악의적 객체 주입"),
    CatalogItem("idor", "IDOR", "CWE-639", GRADE_HIGH,
                "권한 검증 없이 타인 리소스 접근"),
    CatalogItem("improper-jwt", "Improper JWT Verification", "CWE-347", GRADE_HIGH,
                "JWT 서명 검증 누락으로 토큰 위변조"),
    CatalogItem("cleartext-transmission", "Cleartext Transmission", "CWE-319", GRADE_HIGH,
                "HTTP로 민감 정보 평문 전송"),
    # ---- Medium (-1) : needs victim action or extra conditions ----
    CatalogItem("path-traversal", "Path Traversal", "CWE-22", GRADE_MEDIUM,
                "../ 삽입으로 서버 내부 파일 접근"),
    CatalogItem("xss", "Cross-Site Scripting (XSS)", "CWE-79", GRADE_MEDIUM,
                "입력값이 HTML에 출력돼 악성 스크립트 실행"),
    CatalogItem("weak-crypto", "Weak Cryptography", "CWE-327", GRADE_MEDIUM,
                "MD5·SHA1 등 취약한 암호화 사용"),
    CatalogItem("ssrf", "SSRF", "CWE-918", GRADE_MEDIUM,
                "서버가 내부 네트워크로 요청을 보내도록 유도"),
    # ---- Low (-0.2) : not directly exploitable, useful for recon ----
    CatalogItem("error-info-exposure", "Error Message Info Exposure", "CWE-209", GRADE_LOW,
                "에러 메시지에 DB 버전·내부 IP 노출"),
    CatalogItem("missing-httponly", "Missing HttpOnly Flag", "CWE-1004", GRADE_LOW,
                "쿠키 HttpOnly 미설정으로 JS 접근 가능"),
    CatalogItem("missing-secure-flag", "Missing Secure Flag", "CWE-614", GRADE_LOW,
                "쿠키 Secure 플래그 미설정"),
    CatalogItem("weak-password-policy", "Weak Password Requirements", "CWE-521", GRADE_LOW,
                "비밀번호 복잡도 정책 미흡"),
)

_BY_CWE: dict[str, CatalogItem] = {item.cwe: item for item in CATALOG}
_BY_KEY: dict[str, CatalogItem] = {item.key: item for item in CATALOG}

ALL_CWES: tuple[str, ...] = tuple(item.cwe for item in CATALOG)
ALL_KEYS: tuple[str, ...] = tuple(item.key for item in CATALOG)

_CWE_PATTERN = re.compile(r"CWE[-_\s]?(\d+)", re.IGNORECASE)


def normalize_cwe(value: object) -> str | None:
    """Return a canonical 'CWE-89' from a string / list / number, or None.

    Accepts semgrep metadata shapes like ["CWE-89: SQL Injection ..."],
    "CWE-89", "cwe-89", or a bare number "89".
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        for entry in value:
            found = normalize_cwe(entry)
            if found:
                return found
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _CWE_PATTERN.search(text)
    if match:
        return f"CWE-{int(match.group(1))}"
    if text.isdigit():
        return f"CWE-{int(text)}"
    return None


def lookup_by_cwe(cwe: object) -> CatalogItem | None:
    normalized = normalize_cwe(cwe)
    if normalized is None:
        return None
    return _BY_CWE.get(normalized)


def lookup_by_key(key: object) -> CatalogItem | None:
    if key is None:
        return None
    return _BY_KEY.get(str(key).strip().lower())


def resolve_item(value: object) -> CatalogItem | None:
    """Resolve a selection token that may be a CWE id or an item key."""
    return lookup_by_cwe(value) or lookup_by_key(value)


def normalize_selected_cwes(items: Iterable[object] | None) -> set[str]:
    """Map a selection list (CWE ids and/or item keys) to a set of CWE ids.

    None or empty -> all 16 items selected (full scan).
    Unknown tokens are ignored.
    """
    if not items:
        return set(ALL_CWES)
    selected: set[str] = set()
    for token in items:
        item = resolve_item(token)
        if item is not None:
            selected.add(item.cwe)
    if not selected:
        return set(ALL_CWES)
    return selected


def selected_items_summary(selected_cwes: set[str]) -> list[dict[str, str]]:
    """Ordered list of the selected catalog items for display on the result screen."""
    return [
        {"key": item.key, "name": item.name, "cwe": item.cwe, "grade": item.grade}
        for item in CATALOG
        if item.cwe in selected_cwes
    ]
