"""Tests for dockerfile and compose checkers."""

from __future__ import annotations

import os
import shutil
import tempfile

from pinstack.checkers.dockerfile import DockerfileChecker
from pinstack.checkers.compose import ComposeChecker

DOCKER_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "docker")


def _check_dockerfile(subpath):
    # type: (str) -> list
    dirpath = os.path.join(DOCKER_FIXTURES, subpath)
    files = set(os.listdir(dirpath))
    index = {dirpath: files}
    return DockerfileChecker().check(index, dirpath)


def _check_compose(subpath, fname="docker-compose.yml"):
    # type: (str, str) -> list
    dirpath = os.path.join(DOCKER_FIXTURES, subpath)
    index = {dirpath: {fname}}
    return ComposeChecker().check(index, dirpath)


def _check_empty(checker):
    # type: (object,) -> list
    return checker.check({}, "/tmp")


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------

class TestDockerfileGood:
    def test_sha256_pinned_no_findings(self):
        findings = _check_dockerfile("good")
        assert findings == [], "Digest-pinned FROM should produce 0 findings"

    def test_empty_index_no_findings(self):
        assert _check_empty(DockerfileChecker()) == []


class TestDockerfileBad:
    def setup_method(self):
        self.findings = _check_dockerfile("bad")

    def test_two_findings(self):
        assert len(self.findings) == 2, "Expected 2 findings (python:3.11 and ubuntu:22.04), got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_has_two_findings(self):
        assert len(self.findings) == 2

    def test_python_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("python:3.11" in m for m in msgs)

    def test_ubuntu_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("ubuntu:22.04" in m for m in msgs)

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "dockerfile"

    def test_paths_are_relative(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)

    def test_line_numbers_positive(self):
        for f in self.findings:
            assert f.line > 0


class TestDockerfileScratch:
    def test_from_scratch_no_findings(self):
        findings = _check_dockerfile("scratch")
        assert findings == [], "FROM scratch should not produce findings"


class TestDockerfileNamed:
    def test_dockerfile_prod_checked(self):
        """Dockerfile.prod matches Dockerfile* pattern and should be checked."""
        dirpath = os.path.join(DOCKER_FIXTURES, "named")
        index = {dirpath: {"Dockerfile.prod"}}
        findings = DockerfileChecker().check(index, dirpath)
        assert findings == [], "Digest-pinned Dockerfile.prod should produce 0 findings"


class TestDockerfileMultistage:
    def test_stage_alias_skipped(self):
        """AS <alias> introduces bare alias name in subsequent FROM — should be skipped."""
        findings = _check_dockerfile("multistage")
        # golang@sha256: is pinned (ok), runner is a bare alias (skipped)
        assert findings == [], "Build stage aliases should be skipped, got: {}".format(
            [f.message for f in findings]
        )


class TestDockerfilePlatformFlag:
    def _run(self, content):
        # type: (str) -> list
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "Dockerfile")
            with open(path, "w") as fh:
                fh.write(content)
            index = {d: {"Dockerfile"}}
            return DockerfileChecker().check(index, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_from_with_platform(self):
        """FROM --platform=linux/amd64 python:3.11 should be flagged (no digest)."""
        findings = self._run("FROM --platform=linux/amd64 python:3.11\n")
        assert len(findings) == 1, (
            "Expected 1 finding for unpinned platform image, got {}: {}".format(
                len(findings), [f.message for f in findings]
            )
        )
        assert "python:3.11" in findings[0].message
        assert findings[0].checker == "dockerfile"

    def test_from_with_platform_and_digest(self):
        """FROM --platform=linux/amd64 python:3.11@sha256:abc... should produce 0 findings."""
        findings = self._run(
            "FROM --platform=linux/amd64 "
            "python:3.11@sha256:abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890\n"
        )
        assert findings == [], (
            "Digest-pinned platform image should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------

class TestComposeGood:
    def test_sha256_pinned_no_findings(self):
        findings = _check_compose("compose_good")
        assert findings == []

    def test_empty_index_no_findings(self):
        assert _check_empty(ComposeChecker()) == []


class TestComposeBad:
    def setup_method(self):
        self.findings = _check_compose("compose_bad")

    def test_one_finding(self):
        assert len(self.findings) == 1, "Expected 1 finding for nginx:1.25.0, got {}: {}".format(
            len(self.findings), [f.message for f in self.findings]
        )

    def test_nginx_flagged(self):
        assert "nginx:1.25.0" in self.findings[0].message

    def test_has_one_finding(self):
        assert len(self.findings) == 1

    def test_checker_name(self):
        assert self.findings[0].checker == "compose"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)

    def test_line_number_positive(self):
        assert self.findings[0].line > 0


class TestComposeBuildDirective:
    def test_build_section_not_image_not_flagged(self):
        """build: lines should not be matched as image: lines."""
        findings = _check_compose("compose_build")
        assert findings == [], "build: directive should not produce findings, got: {}".format(
            [f.message for f in findings]
        )


class TestComposeAltFilename:
    def test_compose_yml_filename(self):
        """compose.yml (without docker-) should match compose*.yml pattern."""
        findings = _check_compose("compose_alt", fname="compose.yml")
        assert findings == []
