"""Tests for pinstack/sarif.py — SARIF 2.1.0 output builder."""

import json

import jsonschema
import pinstack
from pinstack.core import Finding
from pinstack.sarif import format_sarif


# ---------------------------------------------------------------------------
# Minimal SARIF 2.1.0 structural schema for validation
# ---------------------------------------------------------------------------

SARIF_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["$schema", "version", "runs"],
    "properties": {
        "$schema": {"type": "string"},
        "version": {"type": "string", "const": "2.1.0"},
        "runs": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["tool", "results"],
                "properties": {
                    "tool": {
                        "type": "object",
                        "required": ["driver"],
                        "properties": {
                            "driver": {
                                "type": "object",
                                "required": ["name", "version", "rules"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "version": {"type": "string"},
                                    "informationUri": {"type": "string", "format": "uri"},
                                    "rules": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "required": ["id", "shortDescription"],
                                            "properties": {
                                                "id": {"type": "string"},
                                                "shortDescription": {
                                                    "type": "object",
                                                    "required": ["text"],
                                                    "properties": {"text": {"type": "string"}},
                                                },
                                            },
                                        }
                                    },
                                },
                            }
                        },
                    },
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["ruleId", "level", "message", "locations"],
                            "properties": {
                                "ruleId": {"type": "string"},
                                "level": {
                                    "type": "string",
                                    "enum": ["none", "note", "warning", "error"],
                                },
                                "message": {
                                    "type": "object",
                                    "required": ["text"],
                                    "properties": {"text": {"type": "string"}},
                                },
                                "locations": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "required": ["physicalLocation"],
                                        "properties": {
                                            "physicalLocation": {
                                                "type": "object",
                                                "required": ["artifactLocation"],
                                                "properties": {
                                                    "artifactLocation": {
                                                        "type": "object",
                                                        "required": ["uri"],
                                                        "properties": {
                                                            "uri": {"type": "string"}
                                                        },
                                                    },
                                                    "region": {
                                                        "type": "object",
                                                        "properties": {
                                                            "startLine": {
                                                                "type": "integer",
                                                                "minimum": 1,
                                                            }
                                                        },
                                                    },
                                                },
                                            }
                                        },
                                    },
                                },
                            },
                        }
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(checker="requirements", path="req.txt", line=1,
             message="missing pin"):
    # type: (...) -> Finding
    return Finding(
        checker=checker,
        path=path,
        line=line,
        message=message,
    )


def _parse(findings):
    # type: (list) -> dict
    return json.loads(format_sarif(findings))


# ---------------------------------------------------------------------------
# test_empty_findings
# ---------------------------------------------------------------------------

class TestEmptyFindings:
    def test_empty_findings(self):
        data = _parse([])
        run = data["runs"][0]
        assert run["results"] == []
        assert run["tool"]["driver"]["rules"] == []


# ---------------------------------------------------------------------------
# test_single_finding
# ---------------------------------------------------------------------------

class TestSingleFinding:
    def test_rule_id(self):
        data = _parse([_finding(checker="requirements")])
        result = data["runs"][0]["results"][0]
        assert result["ruleId"] == "requirements"

    def test_level_error(self):
        data = _parse([_finding()])
        result = data["runs"][0]["results"][0]
        assert result["level"] == "error"

    def test_message_text(self):
        data = _parse([_finding(message="'foo' is not pinned")])
        result = data["runs"][0]["results"][0]
        assert result["message"]["text"] == "'foo' is not pinned"

    def test_location_uri(self):
        data = _parse([_finding(path="subdir/requirements.txt")])
        loc = data["runs"][0]["results"][0]["locations"][0]
        assert loc["physicalLocation"]["artifactLocation"]["uri"] == "subdir/requirements.txt"

    def test_location_start_line(self):
        data = _parse([_finding(line=7)])
        loc = data["runs"][0]["results"][0]["locations"][0]
        assert loc["physicalLocation"]["region"]["startLine"] == 7

    def test_one_rule_created(self):
        data = _parse([_finding(checker="requirements")])
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        assert len(rules) == 1
        assert rules[0]["id"] == "requirements"


# ---------------------------------------------------------------------------
# test_multiple_findings_same_checker
# ---------------------------------------------------------------------------

class TestMultipleFindingsSameChecker:
    def test_only_one_rule(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="requirements", line=5),
            _finding(checker="requirements", line=10),
        ]
        data = _parse(findings)
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        assert len(rules) == 1

    def test_multiple_results(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="requirements", line=5),
        ]
        data = _parse(findings)
        results = data["runs"][0]["results"]
        assert len(results) == 2

    def test_rule_id_matches(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="requirements", line=5),
        ]
        data = _parse(findings)
        for result in data["runs"][0]["results"]:
            assert result["ruleId"] == "requirements"


# ---------------------------------------------------------------------------
# test_error_level
# ---------------------------------------------------------------------------

class TestErrorLevel:
    def test_error_level(self):
        data = _parse([_finding()])
        result = data["runs"][0]["results"][0]
        assert result["level"] == "error"


# ---------------------------------------------------------------------------
# test_line_zero_clamped
# ---------------------------------------------------------------------------

class TestLineZeroClamped:
    def test_line_zero_becomes_one(self):
        data = _parse([_finding(line=0)])
        loc = data["runs"][0]["results"][0]["locations"][0]
        assert loc["physicalLocation"]["region"]["startLine"] == 1

    def test_line_positive_unchanged(self):
        data = _parse([_finding(line=42)])
        loc = data["runs"][0]["results"][0]["locations"][0]
        assert loc["physicalLocation"]["region"]["startLine"] == 42


# ---------------------------------------------------------------------------
# test_schema_and_version
# ---------------------------------------------------------------------------

class TestSchemaAndVersion:
    def test_schema_url_present(self):
        data = _parse([])
        assert "$schema" in data

    def test_schema_url_contains_sarif(self):
        data = _parse([])
        assert "sarif" in data["$schema"].lower()

    def test_schema_url_contains_2_1_0(self):
        data = _parse([])
        assert "2.1.0" in data["$schema"]

    def test_version_field(self):
        data = _parse([])
        assert data["version"] == "2.1.0"


# ---------------------------------------------------------------------------
# test_tool_name_and_version
# ---------------------------------------------------------------------------

class TestToolNameAndVersion:
    def test_tool_name_pinstack(self):
        data = _parse([])
        driver = data["runs"][0]["tool"]["driver"]
        assert driver["name"] == "pinstack"

    def test_tool_version_matches(self):
        data = _parse([])
        driver = data["runs"][0]["tool"]["driver"]
        assert driver["version"] == pinstack.__version__

    def test_tool_version_nonempty(self):
        data = _parse([])
        driver = data["runs"][0]["tool"]["driver"]
        assert driver["version"]


# ---------------------------------------------------------------------------
# test_valid_json
# ---------------------------------------------------------------------------

class TestValidJson:
    def test_empty_is_valid_json(self):
        output = format_sarif([])
        json.loads(output)  # must not raise

    def test_single_finding_is_valid_json(self):
        output = format_sarif([_finding()])
        json.loads(output)  # must not raise

    def test_mixed_findings_is_valid_json(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="dockerfile", line=3),
            _finding(checker="requirements", line=9),
        ]
        output = format_sarif(findings)
        json.loads(output)  # must not raise

    def test_output_is_string(self):
        output = format_sarif([])
        assert isinstance(output, str)


# ---------------------------------------------------------------------------
# test_multiple_checkers_multiple_rules
# ---------------------------------------------------------------------------

class TestMultipleCheckersMultipleRules:
    def test_two_checkers_two_rules(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="dockerfile", line=2),
        ]
        data = _parse(findings)
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        rule_ids = {r["id"] for r in rules}
        assert rule_ids == {"requirements", "dockerfile"}

    def test_three_checkers_three_rules(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="dockerfile", line=2),
            _finding(checker="pyproject", line=3),
            _finding(checker="requirements", line=9),  # duplicate checker
        ]
        data = _parse(findings)
        rules = data["runs"][0]["tool"]["driver"]["rules"]
        assert len(rules) == 3

    def test_results_count_matches_findings(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="dockerfile", line=2),
            _finding(checker="pyproject", line=3),
        ]
        data = _parse(findings)
        assert len(data["runs"][0]["results"]) == 3

    def test_runs_list_has_one_run(self):
        data = _parse([_finding(), _finding(checker="dockerfile")])
        assert len(data["runs"]) == 1

    def test_each_result_has_correct_rule_id(self):
        findings = [
            _finding(checker="alpha", line=1),
            _finding(checker="beta", line=2),
            _finding(checker="alpha", line=5),
        ]
        data = _parse(findings)
        results = data["runs"][0]["results"]
        assert results[0]["ruleId"] == "alpha"
        assert results[1]["ruleId"] == "beta"
        assert results[2]["ruleId"] == "alpha"


# ---------------------------------------------------------------------------
# test_sarif_schema_validation
# ---------------------------------------------------------------------------

def _validate(findings):
    # type: (list) -> dict
    """Parse SARIF output and validate against the structural schema."""
    output = format_sarif(findings)
    data = json.loads(output)
    jsonschema.validate(data, SARIF_SCHEMA)
    return data


def _integrity_finding(**kwargs):
    # type: (...) -> Finding
    defaults = dict(checker="package_lock", path="package-lock.json", line=1,
                    message="'lodash' is missing an integrity hash in package-lock.json",
                    integrity=True)
    defaults.update(kwargs)
    return Finding(**defaults)


class TestSarifSchemaValidation:
    """Validate SARIF output against structural schema requirements."""

    def test_empty_findings_validates(self):
        _validate([])

    def test_single_finding_validates(self):
        _validate([_finding()])

    def test_multiple_findings_validates(self):
        findings = [
            _finding(checker="requirements", line=1),
            _finding(checker="dockerfile", line=3),
            _finding(checker="requirements", line=9),
        ]
        _validate(findings)

    def test_integrity_finding_validates(self):
        _validate([_integrity_finding()])

    def test_mixed_findings_validates(self):
        findings = [
            _finding(checker="requirements", line=1),
            _integrity_finding(checker="package_lock", line=5),
            _finding(checker="pyproject", line=10),
            _integrity_finding(checker="yarn_lock", line=20),
        ]
        _validate(findings)

    def test_startLine_is_positive_integer(self):
        data = _validate([_finding(line=0)])
        loc = data["runs"][0]["results"][0]["locations"][0]
        start_line = loc["physicalLocation"]["region"]["startLine"]
        assert isinstance(start_line, int)
        assert start_line >= 1


# ---------------------------------------------------------------------------
# test_integrity_nist_message
# ---------------------------------------------------------------------------

class TestIntegrityNistMessage:
    """Verify NIST reference is appended to integrity findings and not to others."""

    def test_integrity_finding_has_nist_reference(self):
        data = _parse([_integrity_finding()])
        message_text = data["runs"][0]["results"][0]["message"]["text"]
        assert "NIST" in message_text

    def test_integrity_finding_nist_text_contains_sp800(self):
        data = _parse([_integrity_finding()])
        message_text = data["runs"][0]["results"][0]["message"]["text"]
        assert "SP 800-218" in message_text

    def test_non_integrity_finding_no_nist(self):
        data = _parse([_finding()])
        message_text = data["runs"][0]["results"][0]["message"]["text"]
        assert "NIST" not in message_text

    def test_integrity_message_starts_with_original(self):
        original_msg = "'lodash' is missing an integrity hash in package-lock.json"
        data = _parse([_integrity_finding(message=original_msg)])
        message_text = data["runs"][0]["results"][0]["message"]["text"]
        assert message_text.startswith(original_msg)

    def test_non_integrity_message_unchanged(self):
        msg = "'foo' is not pinned with ==; use package==version"
        data = _parse([_finding(message=msg)])
        message_text = data["runs"][0]["results"][0]["message"]["text"]
        assert message_text == msg
