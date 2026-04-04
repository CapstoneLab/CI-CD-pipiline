from __future__ import annotations

import json
import re
from pathlib import Path

from app.models import SecurityFinding, SecuritySummary


def _normalize_severity(extra: dict) -> str:
    candidates = [
        str(extra.get("severity", "")).lower(),
        str(extra.get("metadata", {}).get("severity", "")).lower(),
        str(extra.get("metadata", {}).get("impact", "")).lower(),
    ]

    for value in candidates:
        if "critical" in value:
            return "critical"
        if "high" in value or "error" in value:
            return "high"
        if "medium" in value or "warning" in value:
            return "medium"
        if "low" in value or "info" in value:
            return "low"

    return "low"


def _max_severity(critical: int, high: int, medium: int, low: int) -> str:
    if critical > 0:
        return "critical"
    if high > 0:
        return "high"
    if medium > 0:
        return "medium"
    if low > 0:
        return "low"
    return "none"


def _extract_cvss_score(extra: dict) -> float | None:
    metadata = extra.get("metadata", {})

    candidates = [
        metadata.get("cvss_score"),
        metadata.get("security-severity"),
        metadata.get("cvss"),
        extra.get("cvss_score"),
    ]

    for candidate in candidates:
        if candidate is None:
            continue

        if isinstance(candidate, (int, float)):
            return float(candidate)

        if isinstance(candidate, dict):
            nested = candidate.get("score")
            if isinstance(nested, (int, float)):
                return float(nested)
            if isinstance(nested, str):
                try:
                    return float(nested)
                except ValueError:
                    continue

        if isinstance(candidate, str):
            try:
                return float(candidate)
            except ValueError:
                match = re.search(r"(\d+(?:\.\d+)?)", candidate)
                if match:
                    try:
                        return float(match.group(1))
                    except ValueError:
                        continue

    return None


def parse_semgrep_report(report_file: Path) -> tuple[SecuritySummary, list[SecurityFinding]]:
    if not report_file.exists():
        summary = SecuritySummary(
            scanner_name="semgrep",
            scan_type="deep",
            critical_count=0,
            high_count=0,
            medium_count=0,
            low_count=0,
            max_detected_severity="none",
        )
        return summary, []

    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        summary = SecuritySummary(
            scanner_name="semgrep",
            scan_type="deep",
            critical_count=0,
            high_count=0,
            medium_count=0,
            low_count=0,
            max_detected_severity="none",
        )
        return summary, []

    findings: list[SecurityFinding] = []
    critical = 0
    high = 0
    medium = 0
    low = 0
    max_cvss_score: float | None = None

    for item in data.get("results", []):
        extra = item.get("extra", {})
        severity = _normalize_severity(extra)
        cvss_score = _extract_cvss_score(extra)

        if cvss_score is not None:
            max_cvss_score = cvss_score if max_cvss_score is None else max(max_cvss_score, cvss_score)

        if severity == "critical":
            critical += 1
        elif severity == "high":
            high += 1
        elif severity == "medium":
            medium += 1
        else:
            low += 1

        findings.append(
            SecurityFinding(
                scanner_name="semgrep",
                rule_id=str(item.get("check_id", "unknown")),
                severity=severity,
                title=str(extra.get("message", "Semgrep finding")),
                file_path=str(item.get("path", "")),
                line_number=int(item.get("start", {}).get("line", 0) or 0),
                message=str(extra.get("message", "Semgrep finding")),
                cvss_score=cvss_score,
            )
        )

    summary = SecuritySummary(
        scanner_name="semgrep",
        scan_type="deep",
        critical_count=critical,
        high_count=high,
        medium_count=medium,
        low_count=low,
        max_detected_severity=_max_severity(critical, high, medium, low),
        max_cvss_score=max_cvss_score,
    )

    return summary, findings
