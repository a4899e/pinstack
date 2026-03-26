"""SARIF 2.1.0 output builder (stdlib json only)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pinstack.core import Finding

import pinstack

_NIST_INTEGRITY_NOTE = (
    "This project pins dependencies by version but does not verify artifact "
    "integrity. NIST SP 800-218 (SSDF), Practice PS.2 recommends verifying "
    "software releases using cryptographic hashes to ensure they have not "
    "been tampered with."
)

# security-severity scores per checker (GitHub Code Scanning extension).
# 9.0+ = critical, 7.0-8.9 = high, 4.0-6.9 = medium, 0.1-3.9 = low.
_SECURITY_SEVERITY = {
    # Critical (9.0): direct code execution vectors
    "dockerfile": "9.0",
    "compose": "9.0",
    "github_actions": "9.0",
    # High (8.0): missing integrity hashes on production dependencies
    "requirements": "8.0",
    "package_lock": "8.0",
    "yarn_lock": "8.0",
    "pnpm_lock": "8.0",
    "cargo": "8.0",
    "go": "8.0",
    "terraform": "8.0",
    # High (7.0): missing or stale lock file
    "pyproject": "7.0",
    "package_json": "7.0",
    "gemfile": "7.0",
    "helm": "7.0",
    "gradle": "7.0",
    # Medium (6.0): no native lock file mechanism
    "maven": "6.0",
}


def format_sarif(findings: list[Finding]) -> str:
    rules: dict = {}
    results: list = []

    for f in findings:
        rule_id = f.checker
        if rule_id not in rules:
            rule = {
                "id": rule_id,
                "shortDescription": {"text": rule_id},
            }
            severity = _SECURITY_SEVERITY.get(rule_id)
            if severity:
                rule["properties"] = {"security-severity": severity}
            rules[rule_id] = rule

        message_text = f.message
        if f.integrity:
            message_text = "{}\n\n{}".format(f.message, _NIST_INTEGRITY_NOTE)

        result = {
            "ruleId": rule_id,
            "level": "error",
            "message": {"text": message_text},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": f.path},
                        "region": {"startLine": max(f.line, 1)},
                    }
                }
            ],
        }
        results.append(result)

    sarif = {
        "$schema": (
            "https://raw.githubusercontent.com/oasis-tcs/sarif-spec"
            "/main/sarif-2.1/schema/sarif-schema-2.1.0.json"
        ),
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "pinstack",
                        "version": pinstack.__version__,
                        "informationUri": "https://github.com/a4899e/pinstack",
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2)
