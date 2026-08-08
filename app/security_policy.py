from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from app.models import SecurityFinding
from app.security_catalog import (
    GRADE_CAP,
    GRADE_CRITICAL,
    GRADE_DEDUCTION,
    GRADE_HIGH,
    lookup_by_cwe,
    normalize_selected_cwes,
    resolve_item,
    selected_items_summary,
)


# Gate verdicts (hierarchy-driven; score is advisory only and never gates).
VERDICT_BLOCK = "block"                              # Critical >= 1 (hard block, no exception)
VERDICT_BLOCK_PENDING_APPROVAL = "block_pending_approval"  # High >= 1 (block, approval exception only)
VERDICT_WARN = "warn"                                # Medium >= 1
VERDICT_PASS = "pass"                                # Low only / none

# Gauge color follows the GATE verdict, not a score band, so it can never
# contradict the verdict (see manual §4-4 fix).
GAUGE_COLOR = {
    VERDICT_BLOCK: "red",
    VERDICT_BLOCK_PENDING_APPROVAL: "orange",
    VERDICT_WARN: "yellow",
    VERDICT_PASS: "green",
}

# Environments that apply EXTRA strictness on top of the manual hierarchy.
# In these, a Medium-only WARN is escalated to require acknowledgment/approval.
# The hierarchy is never relaxed — environment can only tighten.
STRICT_ENVIRONMENTS = {"production", "staging"}

ENVIRONMENTS = {"production", "staging", "development", "feature"}


@dataclass
class SecurityVerdict:
    verdict: str
    score: float
    score_label: str
    gauge_color: str
    environment: str
    counts: dict[str, int]
    selected_count: int
    selected_items: list[dict[str, str]]
    out_of_scope_count: int
    requires_approval: bool
    score_breakdown: dict[str, float]
    scanned_commit_sha: str | None = None
    acknowledged_cwes: list[str] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)
    warn_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_environment(value: str | None) -> str:
    if not value:
        return "development"
    candidate = value.strip().lower()
    if candidate in ENVIRONMENTS:
        return candidate
    aliases = {
        "prod": "production",
        "stage": "staging",
        "dev": "development",
        "develop": "development",
        "feat": "feature",
        "feature_branch": "feature",
    }
    return aliases.get(candidate, "development")


def _mark_scope(findings: Iterable[SecurityFinding], selected_cwes: set[str]) -> None:
    """Tag each finding with in_scope per the user's selected items.

    A finding is in scope only if it maps to a catalog item (by policy_item or
    CWE) whose CWE is in the selected set. Findings outside the 16-item catalog
    are out of scope: the policy is defined only over selected items, and
    unselected items are reported as "not scanned", never as "safe".
    """
    for finding in findings:
        item = lookup_by_cwe(finding.cwe)
        if item is None:
            finding.in_scope = False
            continue
        # catalog is the single source of truth for grade + classification
        finding.policy_item = item.key
        finding.severity = item.grade
        finding.in_scope = item.cwe in selected_cwes


def count_by_severity(findings: Iterable[SecurityFinding]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity = (finding.severity or "low").lower()
        if severity not in counts:
            severity = "low"
        counts[severity] += 1
    return counts


def calculate_score(counts: dict[str, int]) -> tuple[float, dict[str, float]]:
    """Capped score per manual §4. Returns (score, breakdown).

    Caps keep the hierarchy intact: a lower grade's total deduction can never
    reach a single finding of the grade above it (19<20, 4<5, 0.9<1).
    With Critical present the score is forced into the 0~40 danger zone.
    """
    high_ded = min(counts.get("high", 0) * GRADE_DEDUCTION["high"], GRADE_CAP["high"])
    med_ded = min(counts.get("medium", 0) * GRADE_DEDUCTION["medium"], GRADE_CAP["medium"])
    low_ded = min(counts.get("low", 0) * GRADE_DEDUCTION["low"], GRADE_CAP["low"])
    crit = counts.get("critical", 0)

    if crit >= 1:
        raw = 100.0 - crit * GRADE_DEDUCTION["critical"] - high_ded - med_ded - low_ded
        score = max(0.0, min(40.0, raw))
    else:
        score = max(0.0, 100.0 - high_ded - med_ded - low_ded)

    breakdown = {
        "critical": round(crit * GRADE_DEDUCTION["critical"], 2),
        "high": round(high_ded, 2),
        "medium": round(med_ded, 2),
        "low": round(low_ded, 2),
    }
    return round(score, 1), breakdown


_GRADE_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low"}


def _gate_reasons(findings: list[SecurityFinding], grade: str) -> list[str]:
    """Item-specific gate-rule strings (manual §6 rule 1), one per distinct item."""
    seen: dict[str, SecurityFinding] = {}
    for f in findings:
        if (f.severity or "").lower() != grade:
            continue
        key = f.policy_item or f.cwe or f.rule_id
        seen.setdefault(key, f)
    reasons: list[str] = []
    policy = "즉시 차단 정책" if grade == "critical" else "승인 필요 정책"
    for f in seen.values():
        item = lookup_by_cwe(f.cwe)
        name = item.name if item else (f.title or f.rule_id)
        cwe = (item.cwe if item else f.cwe) or "CWE-?"
        reasons.append(f"{name} ({cwe}, {_GRADE_LABEL[grade]}) 검출 — {policy}")
    return reasons


def _normalize_approved_cwes(approved: Iterable[object] | None) -> set[str]:
    """Resolve approval tokens (CWE ids or item keys) to a set of CWE ids.
    Critical items are dropped here — Critical can never be acknowledged."""
    result: set[str] = set()
    for token in approved or []:
        item = resolve_item(token)
        if item is not None and item.grade != GRADE_CRITICAL:
            result.add(item.cwe)
    return result


def evaluate(
    findings: list[SecurityFinding],
    selected_items: Iterable[object] | None = None,
    environment: str = "development",
    approved_cwes: Iterable[object] | None = None,
    scanned_commit_sha: str | None = None,
) -> SecurityVerdict:
    env = normalize_environment(environment)
    selected_cwes = normalize_selected_cwes(selected_items)
    approved_set = _normalize_approved_cwes(approved_cwes)

    _mark_scope(findings, selected_cwes)
    in_scope = [f for f in findings if f.in_scope]
    out_of_scope_count = sum(1 for f in findings if not f.in_scope)

    # High findings whose CWE was approved are waived at the gate (acknowledged).
    # Critical is never in approved_set, so it always keeps blocking.
    acknowledged = [
        f for f in in_scope
        if (f.severity or "").lower() == GRADE_HIGH and f.cwe in approved_set
    ]
    acknowledged_cwes = sorted({f.cwe for f in acknowledged if f.cwe})
    active_high = [
        f for f in in_scope
        if (f.severity or "").lower() == GRADE_HIGH and f.cwe not in approved_set
    ]

    # score reflects all in-scope findings — accepted risk still counts toward health
    counts = count_by_severity(in_scope)
    score, breakdown = calculate_score(counts)

    selected_summary = selected_items_summary(selected_cwes)
    selected_count = len(selected_summary)
    score_label = f"{score}/100 (검사 항목 {selected_count}개 기준)"

    block_reasons: list[str] = []
    warn_reasons: list[str] = []
    requires_approval = False

    # ----- hierarchy gate (manual §3): first match wins -----
    # acknowledged High no longer triggers the gate.
    if counts["critical"] >= 1:
        verdict = VERDICT_BLOCK
        block_reasons.extend(_gate_reasons(in_scope, "critical"))
    elif active_high:
        verdict = VERDICT_BLOCK_PENDING_APPROVAL
        requires_approval = True
        block_reasons.extend(_gate_reasons(active_high, "high"))
    elif counts["medium"] >= 1:
        verdict = VERDICT_WARN
        warn_reasons.append(f"Medium {counts['medium']}건 검출 — 경고(배포 통과)")
    else:
        verdict = VERDICT_PASS

    if acknowledged_cwes:
        warn_reasons.append(
            f"승인 수용된 High: {', '.join(acknowledged_cwes)} (게이트 통과 처리)"
        )

    # ----- environment tightening (never relaxes) -----
    if verdict == VERDICT_WARN and env in STRICT_ENVIRONMENTS:
        verdict = VERDICT_BLOCK_PENDING_APPROVAL
        requires_approval = True
        block_reasons.append(
            f"Medium {counts['medium']}건 — {env} 환경 엄격정책으로 승인 필요"
        )

    return SecurityVerdict(
        verdict=verdict,
        score=score,
        score_label=score_label,
        gauge_color=GAUGE_COLOR[verdict],
        environment=env,
        counts=counts,
        selected_count=selected_count,
        selected_items=selected_summary,
        out_of_scope_count=out_of_scope_count,
        requires_approval=requires_approval,
        score_breakdown=breakdown,
        scanned_commit_sha=scanned_commit_sha,
        acknowledged_cwes=acknowledged_cwes,
        block_reasons=block_reasons,
        warn_reasons=warn_reasons,
    )


def is_blocking(verdict: SecurityVerdict | str) -> bool:
    """Both hard block and pending-approval halt the pipeline."""
    value = verdict.verdict if isinstance(verdict, SecurityVerdict) else verdict
    return value in (VERDICT_BLOCK, VERDICT_BLOCK_PENDING_APPROVAL)


def format_summary(verdict: SecurityVerdict) -> str:
    counts = verdict.counts
    base = (
        f"verdict={verdict.verdict.upper()} {verdict.score_label} env={verdict.environment} "
        f"critical={counts['critical']} high={counts['high']} "
        f"medium={counts['medium']} low={counts['low']}"
    )
    if verdict.out_of_scope_count:
        base += f" (out_of_scope={verdict.out_of_scope_count})"
    if verdict.verdict in (VERDICT_BLOCK, VERDICT_BLOCK_PENDING_APPROVAL):
        suffix = " | approval required" if verdict.requires_approval else ""
        return f"{base} | blocked: {'; '.join(verdict.block_reasons)}{suffix}"
    if verdict.verdict == VERDICT_WARN:
        return f"{base} | warn: {'; '.join(verdict.warn_reasons)}"
    return base
