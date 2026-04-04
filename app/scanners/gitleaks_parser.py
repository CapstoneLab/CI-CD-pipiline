from __future__ import annotations

import json
from pathlib import Path

from app.models import SecurityFinding, SecuritySummary


def parse_gitleaks_report(report_file: Path) -> tuple[SecuritySummary, list[SecurityFinding]]:
    if not report_file.exists():
        summary = SecuritySummary(
            scanner_name="gitleaks",
            scan_type="lightweight",
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
            scanner_name="gitleaks",
            scan_type="lightweight",
            critical_count=0,
            high_count=0,
            medium_count=0,
            low_count=0,
            max_detected_severity="none",
        )
        return summary, []

    findings: list[SecurityFinding] = []
    for item in data if isinstance(data, list) else []:
        findings.append(
            SecurityFinding(
                scanner_name="gitleaks",
                rule_id=str(item.get("RuleID", "unknown")),
                severity="high",
                title=str(item.get("Description", "Potential secret detected")),
                file_path=str(item.get("File", "")),
                line_number=int(item.get("StartLine", item.get("Line", 0)) or 0),
                message="Potential secret pattern detected",
                cvss_score=None,
            )
        )

    count = len(findings)
    summary = SecuritySummary(
        scanner_name="gitleaks",
        scan_type="lightweight",
        critical_count=0,
        high_count=count,
        medium_count=0,
        low_count=0,
        max_detected_severity="high" if count > 0 else "none",
    )
    return summary, findings
