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


def format_sarif(findings: list[Finding]) -> str:
    rules: dict = {}
    results: list = []

    for f in findings:
        rule_id = f.checker
        if rule_id not in rules:
            rules[rule_id] = {
                "id": rule_id,
                "shortDescription": {"text": rule_id},
            }

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
