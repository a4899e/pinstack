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


def _scan(subpath):
    # type: (str) -> list
    from pinstack.core import build_index

    dirpath = os.path.join(HELM_FIXTURES, subpath)
    index = build_index(dirpath, set(HelmChecker.patterns))
    return HelmChecker().check(index, dirpath)


def _chart(chart_yaml, chart_lock=None):
    # type: (str, str) -> list
    d = tempfile.mkdtemp()
    try:
        files = {"Chart.yaml"}
        with open(os.path.join(d, "Chart.yaml"), "w") as fh:
            fh.write(chart_yaml)
        if chart_lock is not None:
            files.add("Chart.lock")
            with open(os.path.join(d, "Chart.lock"), "w") as fh:
                fh.write(chart_lock)
        return [(f.path, f.line, f.message) for f in HelmChecker().check({d: files}, d)]
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestHelmVersionRanges:
    """Review L5: Chart.yaml dependency versions are Masterminds constraints."""

    def test_ranges_and_partial_versions(self):
        findings = _scan("ranges")
        assert [(f.line, f.message) for f in findings] == [
            (5, "dependency 'redis' uses version range '1.x'; pin to an exact version"),
            (
                8,
                "dependency 'postgresql' uses version range '>=1.0'; pin to an exact version",
            ),
            (
                11,
                "dependency 'nginx' uses version range '^2.1.0'; pin to an exact version",
            ),
            (
                14,
                "dependency 'mysql' uses version range '4.2'; pin to an exact version",
            ),
        ]

    def test_local_file_dependency_is_exempt(self):
        """The `local` file:// chart in the fixture has no version and is not reported."""
        assert not any("'local'" in f.message for f in _scan("ranges"))

    def test_missing_version(self):
        findings = _chart(
            "dependencies:\n  - name: redis\n    repository: https://x.invalid\n",
            "dependencies:\n- name: redis\n  version: 1.0.0\ndigest: sha256:x\n",
        )
        assert [m for _, _, m in findings] == [
            "dependency 'redis' has no version; pin to an exact version"
        ]

    def test_non_exact_lock_version(self):
        findings = _chart(
            "dependencies:\n  - name: redis\n    version: 1.0.0\n",
            "dependencies:\n- name: redis\n  version: 1.x\ndigest: sha256:x\n",
        )
        assert [m for _, _, m in findings] == [
            "dependency 'redis' in Chart.lock has version '1.x', which is not"
            " exact; run 'helm dependency update'"
        ]

    def test_flow_style_dependencies_reported(self):
        findings = _chart("dependencies: [{name: redis, version: ^1.0.0}]\n")
        assert [m for _, _, m in findings] == [
            "dependencies are written in flow style, which pinstack cannot read;"
            " write them as a block list",
            "Chart.yaml declares dependencies but Chart.lock is missing;"
            " run 'helm dependency update'",
        ]


class TestHelmDigestIsNotIntegrity:
    """Review L5: Chart.lock's digest detects drift; it does not hash chart content."""

    def test_findings_are_not_integrity(self):
        findings = _scan("bad_digest") + _scan("no_lock") + _scan("ranges")
        assert findings and not any(f.integrity for f in findings)

    def test_missing_digest_message(self):
        assert [f.message for f in _scan("bad_digest")] == [
            "Chart.lock has no digest: line, so Helm cannot tell when it is out of"
            " date with Chart.yaml; run 'helm dependency update'"
        ]

    def test_sarif_has_no_nist_integrity_note(self):
        import json

        from pinstack.sarif import format_sarif

        sarif = json.loads(format_sarif(_scan("bad_digest")))
        text = sarif["runs"][0]["results"][0]["message"]["text"]
        assert "NIST" not in text


_LOCK_REDIS = (
    "dependencies:\n- name: redis\n  repository: https://x.invalid\n"
    "  version: 1.0.0\ndigest: sha256:x\n"
)


class TestHelmReviewFollowUps:
    """Local review of the L5 change."""

    def test_nested_lists_stay_inside_their_dependency(self):
        findings = _chart(
            "dependencies:\n"
            "  - name: redis\n"
            "    repository: https://x.invalid\n"
            "    tags:\n"
            "      - cache\n"
            "    import-values:\n"
            "      - child: a\n"
            "        parent: b\n"
            "    version: 1.0.0\n",
            _LOCK_REDIS,
        )
        assert findings == []

    def test_tab_after_dash(self):
        findings = _chart(
            "dependencies:\n  -\tname: redis\n    repository: https://x.invalid\n"
            "    version: ^1.0.0\n",
            _LOCK_REDIS,
        )
        assert [m for _, _, m in findings] == [
            "dependency 'redis' uses version range '^1.0.0'; pin to an exact version"
        ]

    def test_flow_style_item(self):
        findings = _chart(
            "dependencies:\n  - {name: redis, version: ^1.0.0, repository: https://x}\n",
            _LOCK_REDIS,
        )
        assert [m for _, _, m in findings] == [
            "dependencies are written in flow style, which pinstack cannot read;"
            " write them as a block list"
        ]

    def test_quoted_keys(self):
        findings = _chart(
            'dependencies:\n  - "name": redis\n    "repository": https://x.invalid\n'
            '    "version": 1.0.0\n',
            _LOCK_REDIS,
        )
        assert findings == []

    def test_lock_at_a_different_exact_version_is_stale(self):
        findings = _chart(
            "dependencies:\n  - name: redis\n    repository: https://x.invalid\n"
            "    version: 2.0.0\n",
            _LOCK_REDIS,
        )
        assert [m for _, _, m in findings] == [
            "Chart.lock is stale: 'redis' is locked at 1.0.0 but Chart.yaml asks"
            " for 2.0.0; run 'helm dependency update'"
        ]

    def test_vendored_subchart_without_repository_is_exempt(self):
        findings = _chart(
            "dependencies:\n  - name: sub\n    version: ^0.1.0\n",
            "dependencies:\n- name: sub\n  version: 0.1.0\ndigest: sha256:x\n",
        )
        assert findings == []

    def test_unreadable_lock_is_reported(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "Chart.yaml"), "w") as fh:
                fh.write(
                    "dependencies:\n  - name: redis\n    repository: https://x\n"
                    "    version: 1.0.0\n"
                )
            os.mkdir(os.path.join(d, "Chart.lock"))
            findings = HelmChecker().check({d: {"Chart.yaml", "Chart.lock"}}, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [f.message for f in findings] == ["Chart.lock could not be read"]
