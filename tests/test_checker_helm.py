"""Tests for the Helm chart checker."""

from __future__ import annotations

import os
import shutil
import tempfile

from pinstack.checkers.helm import HelmChecker

HELM_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "helm")


def _check(subpath, files=None):
    # type: (str, set) -> list
    dirpath = os.path.join(HELM_FIXTURES, subpath)
    if files is None:
        # Auto-detect which fixture files exist
        present = set()
        for fname in ("Chart.yaml", "Chart.lock"):
            if os.path.exists(os.path.join(dirpath, fname)):
                present.add(fname)
        files = present
    index = {dirpath: files}
    return HelmChecker().check(index, dirpath)


def _check_empty():
    # type: () -> list
    return HelmChecker().check({}, "/tmp")


class TestHelmGood:
    def test_chart_yaml_with_lock_and_digest_no_findings(self):
        findings = _check("good")
        assert findings == [], (
            "Chart.yaml with deps + Chart.lock with digest should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )

    def test_no_chart_files_no_findings(self):
        assert _check_empty() == []

    def test_chart_yaml_without_dependencies_no_findings(self):
        findings = _check("no_deps")
        assert findings == [], (
            "Chart.yaml without dependencies section should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )


class TestHelmNoLock:
    def setup_method(self):
        self.findings = _check("no_lock")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for missing Chart.lock, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_has_one_finding(self):
        assert len(self.findings) == 1

    def test_message_mentions_lock(self):
        assert "Chart.lock" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "helm"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestHelmBadDigest:
    def setup_method(self):
        self.findings = _check("bad_digest")

    def test_one_finding(self):
        assert len(self.findings) == 1, (
            "Expected 1 finding for missing digest, got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_has_one_finding(self):
        assert len(self.findings) == 1

    def test_message_mentions_digest(self):
        assert "digest" in self.findings[0].message

    def test_checker_name(self):
        assert self.findings[0].checker == "helm"

    def test_path_is_relative(self):
        assert not os.path.isabs(self.findings[0].path)


class TestHelmCrossRef:
    """Cross-reference: Chart.yaml deps must all appear in Chart.lock."""

    def _make_index(self, chart_yaml_content, chart_lock_content=None):
        # type: (str, str) -> list
        import tempfile

        d = tempfile.mkdtemp()
        chart_yaml = os.path.join(d, "Chart.yaml")
        with open(chart_yaml, "w") as fh:
            fh.write(chart_yaml_content)
        files = {"Chart.yaml"}
        if chart_lock_content is not None:
            chart_lock = os.path.join(d, "Chart.lock")
            with open(chart_lock, "w") as fh:
                fh.write(chart_lock_content)
            files.add("Chart.lock")
        index = {d: files}
        return HelmChecker().check(index, d)

    def test_dep_in_chart_yaml_missing_from_lock(self):
        chart_yaml = (
            "apiVersion: v2\n"
            "name: my-app\n"
            "dependencies:\n"
            "  - name: nginx\n"
            '    version: "15.0.0"\n'
            "    repository: https://charts.bitnami.com/bitnami\n"
        )
        chart_lock = (
            "dependencies:\n"
            "- name: redis\n"
            '  version: "17.0.0"\n'
            "  repository: https://charts.bitnami.com/bitnami\n"
            "  digest: sha256:abc123\n"
            'generated: "2024-01-01T00:00:00Z"\n'
        )
        findings = self._make_index(chart_yaml, chart_lock)
        cross_ref = [f for f in findings if "stale" in f.message]
        assert len(cross_ref) == 1, "Expected 1 cross-ref finding, got: {}".format(
            [f.message for f in findings]
        )
        assert "nginx" in cross_ref[0].message
        assert cross_ref[0].checker == "helm"

    def test_dep_in_chart_yaml_present_in_lock(self):
        chart_yaml = (
            "apiVersion: v2\n"
            "name: my-app\n"
            "dependencies:\n"
            "  - name: nginx\n"
            '    version: "15.0.0"\n'
            "    repository: https://charts.bitnami.com/bitnami\n"
        )
        chart_lock = (
            "dependencies:\n"
            "- name: nginx\n"
            '  version: "15.0.0"\n'
            "  repository: https://charts.bitnami.com/bitnami\n"
            "  digest: sha256:abc123\n"
            'generated: "2024-01-01T00:00:00Z"\n'
        )
        findings = self._make_index(chart_yaml, chart_lock)
        cross_ref = [f for f in findings if "stale" in f.message]
        assert cross_ref == [], (
            "All deps present in lock — expected no cross-ref findings, got: {}".format(
                [f.message for f in findings]
            )
        )

    def test_top_level_digest_accepted(self):
        """Chart.lock with a single top-level digest: (not per-dep) should produce 0 findings."""
        chart_yaml = (
            "apiVersion: v2\n"
            "name: my-app\n"
            "dependencies:\n"
            "  - name: nginx\n"
            '    version: "15.0.0"\n'
            "    repository: https://charts.bitnami.com/bitnami\n"
            "  - name: redis\n"
            '    version: "17.0.0"\n'
            "    repository: https://charts.bitnami.com/bitnami\n"
        )
        # Top-level digest covers both deps — no per-dep digests present
        chart_lock = (
            "dependencies:\n"
            "- name: nginx\n"
            '  version: "15.0.0"\n'
            "  repository: https://charts.bitnami.com/bitnami\n"
            "- name: redis\n"
            '  version: "17.0.0"\n'
            "  repository: https://charts.bitnami.com/bitnami\n"
            "digest: sha256:toplevelabc123\n"
            'generated: "2024-01-01T00:00:00Z"\n'
        )
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "Chart.yaml"), "w") as fh:
                fh.write(chart_yaml)
            with open(os.path.join(d, "Chart.lock"), "w") as fh:
                fh.write(chart_lock)
            index = {d: {"Chart.yaml", "Chart.lock"}}
            findings = HelmChecker().check(index, d)
            digest_findings = [f for f in findings if "digest" in f.message]
            assert digest_findings == [], (
                "Top-level digest should be accepted, got: {}".format(
                    [f.message for f in findings]
                )
            )
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_multiple_deps_one_missing(self):
        chart_yaml = (
            "apiVersion: v2\n"
            "name: my-app\n"
            "dependencies:\n"
            "  - name: nginx\n"
            '    version: "15.0.0"\n'
            "    repository: https://charts.bitnami.com/bitnami\n"
            "  - name: redis\n"
            '    version: "17.0.0"\n'
            "    repository: https://charts.bitnami.com/bitnami\n"
        )
        chart_lock = (
            "dependencies:\n"
            "- name: nginx\n"
            '  version: "15.0.0"\n'
            "  repository: https://charts.bitnami.com/bitnami\n"
            "  digest: sha256:abc123\n"
            'generated: "2024-01-01T00:00:00Z"\n'
        )
        findings = self._make_index(chart_yaml, chart_lock)
        cross_ref = [f for f in findings if "stale" in f.message]
        assert len(cross_ref) == 1, (
            "Expected 1 cross-ref finding for missing redis, got: {}".format(
                [f.message for f in findings]
            )
        )
        assert "redis" in cross_ref[0].message
