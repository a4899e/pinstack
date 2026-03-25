"""Tests for the Terraform lock file checker."""

import os

from pinstack.core import Severity
from pinstack.checkers.terraform import TerraformChecker

TERRAFORM_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "terraform")


def _check(subpath):
    # type: (str) -> list
    dirpath = os.path.join(TERRAFORM_FIXTURES, subpath)
    index = {dirpath: {".terraform.lock.hcl"}}
    return TerraformChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return TerraformChecker().check({}, "/tmp")


class TestTerraformGood:
    def test_all_providers_have_h1_no_findings(self):
        findings = _check("good")
        assert findings == [], "All providers with h1: hashes should produce 0 findings, got: {}".format(
            [f.message for f in findings]
        )

    def test_no_lock_file_no_findings(self):
        assert _check_empty() == []


class TestTerraformBadMissingH1:
    def setup_method(self):
        self.findings = _check("bad_missing_h1")

    def test_one_finding(self):
        assert len(self.findings) == 1, "Expected 1 finding for provider missing h1:, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_is_warning(self):
        assert self.findings[0].severity == Severity.WARNING

    def test_provider_name_in_message(self):
        assert "aws" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "terraform"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_line_number_positive(self):
        assert self.findings[0].line > 0


class TestTerraformMultiProvider:
    def setup_method(self):
        self.findings = _check("multi_provider")

    def test_one_finding_only_null_missing(self):
        assert len(self.findings) == 1, "Expected 1 finding for null provider missing h1:, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_null_provider_flagged(self):
        assert "null" in self.findings[0].message

    def test_aws_not_flagged(self):
        messages = [f.message for f in self.findings]
        assert not any("aws" in m for m in messages)

    def test_random_not_flagged(self):
        messages = [f.message for f in self.findings]
        assert not any("random" in m for m in messages)
