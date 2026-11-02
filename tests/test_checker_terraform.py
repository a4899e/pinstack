"""Tests for the Terraform lock file checker."""

import os

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
        assert findings == [], (
            "All providers with h1: hashes should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )

    def test_no_lock_file_no_findings(self):
        assert _check_empty() == []


class TestTerraformBadMissingH1:
    def setup_method(self):
        self.findings = _check("bad_missing_h1")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for provider missing h1:, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_has_one_finding(self):
        assert len(self.findings) == 1

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
        assert len(self.findings) == 1, (
            "Expected 1 finding for null provider missing h1:, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_null_provider_flagged(self):
        assert "null" in self.findings[0].message

    def test_aws_not_flagged(self):
        messages = [f.message for f in self.findings]
        assert not any("aws" in m for m in messages)

    def test_random_not_flagged(self):
        messages = [f.message for f in self.findings]
        assert not any("random" in m for m in messages)


def _scan(subpath):
    # type: (str) -> list
    """Index a fixture tree the way the CLI does and run the checker over it."""
    from pinstack.core import build_index

    dirpath = os.path.join(TERRAFORM_FIXTURES, subpath)
    index = build_index(dirpath, set(TerraformChecker.patterns))
    return TerraformChecker().check(index, dirpath)


class TestTerraformMissingLock:
    """Review repro M3: a root module with no .terraform.lock.hcl."""

    def test_required_providers_without_lock_flagged(self):
        findings = _scan("no_lock")
        assert len(findings) == 1
        assert findings[0].path == "main.tf"
        assert findings[0].line == 2
        assert "no .terraform.lock.hcl" in findings[0].message
        assert findings[0].integrity

    def test_locally_called_child_module_not_flagged(self):
        """modules/vpc is locked by the root that calls it with source = "./modules/vpc"."""
        assert _scan("with_modules") == []

    def test_module_repo_root_called_from_examples_not_flagged(self):
        """Standard module layout: examples/basic calls the root with source = "../../"."""
        assert _scan("module_repo") == []

    def test_commented_out_blocks_ignored(self):
        assert _scan("commented") == []

    def test_existing_lock_fixtures_unchanged(self):
        """Lock-only directories have no .tf files, so no new findings."""
        assert _scan("good") == []

    def test_tf_pattern_indexed(self):
        assert "*.tf" in TerraformChecker.patterns


class TestTerraformParsing:
    def test_comment_markers_do_not_hide_blocks(self):
        """`/*` inside a `#` comment opens nothing; a resource `source = "./"` calls no module."""
        findings = _scan("comment_tricks")
        assert [(f.path, f.line) for f in findings] == [("main.tf", 3)]

    def test_heredoc_body_is_not_configuration(self):
        from pinstack.checkers.terraform import _ROOT_SIGNAL_RE, _strip_comments

        text = 'x = <<EOT\nprovider "fake" {\nEOT\n'
        assert not _ROOT_SIGNAL_RE.search(_strip_comments(text))

    def test_strings_keep_comment_markers(self):
        from pinstack.checkers.terraform import _strip_comments

        text = 'a = "x # y /* z"\n'
        assert _strip_comments(text) == text

    def test_only_module_block_sources_count(self):
        from pinstack.checkers.terraform import _local_module_sources, _strip_comments

        text = _strip_comments(
            'resource "r" "x" {\n  source = "./f"\n}\n'
            'module "m" {\n  source = "../m" // note\n  x = { a = "}" }\n}\n'
        )
        assert _local_module_sources(text) == ["../m"]


def _scan_tree(tree):
    # type: (dict) -> list
    import shutil
    import tempfile

    from pinstack.core import build_index

    d = tempfile.mkdtemp()
    try:
        for rel, content in tree.items():
            path = os.path.join(d, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(content)
        index = build_index(d, set(TerraformChecker.patterns))
        return [(f.path, f.message) for f in TerraformChecker().check(index, d)]
    finally:
        shutil.rmtree(d, ignore_errors=True)


_AWS_ROOT = (
    "terraform {\n  required_providers {\n"
    '    aws = { source = "hashicorp/aws", version = "~> 5.0" }\n'
    "  }\n}\n"
)
_AWS_LOCK = (
    'provider "registry.terraform.io/hashicorp/aws" {\n'
    '  version = "5.31.0"\n'
    '  hashes = [\n    "h1:abc=",\n  ]\n}\n'
)


class TestTerraformLockCoverage:
    """Codex review of #13: a lock file must actually lock the required providers."""

    def test_zero_byte_lock_does_not_satisfy(self):
        findings = _scan_tree({"main.tf": _AWS_ROOT, ".terraform.lock.hcl": ""})
        assert findings == [
            (
                ".terraform.lock.hcl",
                ".terraform.lock.hcl does not lock 1 required provider"
                " (hashicorp/aws); run 'terraform init' and commit the lock file",
            )
        ]

    def test_covering_lock_accepted(self):
        assert (
            _scan_tree({"main.tf": _AWS_ROOT, ".terraform.lock.hcl": _AWS_LOCK}) == []
        )

    def test_partial_lock_names_missing_provider(self):
        tf = _AWS_ROOT + 'provider "random" {}\n'
        findings = _scan_tree({"main.tf": tf, ".terraform.lock.hcl": _AWS_LOCK})
        assert len(findings) == 1
        assert "(hashicorp/random)" in findings[0][1]

    def test_opentofu_registry_lock_equivalent(self):
        lock = _AWS_LOCK.replace("registry.terraform.io", "registry.opentofu.org")
        assert _scan_tree({"main.tf": _AWS_ROOT, ".terraform.lock.hcl": lock}) == []

    def test_local_name_and_legacy_constraint_forms(self):
        from pinstack.checkers.terraform import _required_providers, _strip_comments

        tf = _strip_comments(
            "terraform {\n  required_providers {\n"
            '    mycloud = {\n      source = "acme/cloud"\n    }\n'
            '    null = "~> 3.0"\n'
            "  }\n}\n"
            'provider "mycloud" {}\n'
            'provider "google" {}\n'
            'data "terraform_remote_state" "x" {}\n'
        )
        assert _required_providers(tf) == {
            "acme/cloud",
            "hashicorp/null",
            "hashicorp/google",
        }

    def test_builtin_provider_not_required(self):
        from pinstack.checkers.terraform import _required_providers

        tf = (
            "terraform {\n  required_providers {\n"
            '    terraform = { source = "terraform.io/builtin/terraform" }\n'
            "  }\n}\n"
        )
        assert _required_providers(tf) == set()
