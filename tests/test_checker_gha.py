"""Tests for the GitHub Actions checker."""

import os

from pinstack.checkers.github_actions import GitHubActionsChecker

GHA_FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "gha")


def _check(subpath):
    # type: (str) -> list
    """Walk subpath and build an index for all .yml/.yaml files, rooted at subpath."""
    root = os.path.join(GHA_FIXTURES, subpath)
    index = {}
    for dirpath, dirnames, filenames in os.walk(root):
        matched = {f for f in filenames if f.endswith(".yml") or f.endswith(".yaml")}
        if matched:
            index[dirpath] = matched
    return GitHubActionsChecker().check(index, root)


def _check_empty():
    # type: () -> list
    return GitHubActionsChecker().check({}, "/tmp")


class TestGHAGood:
    def test_sha_pinned_no_findings(self):
        findings = _check("good")
        assert findings == [], (
            "All SHA-pinned refs should produce 0 findings, got: {}".format(
                [f.message for f in findings]
            )
        )

    def test_local_action_skipped(self):
        """./local-action refs should not be flagged."""
        findings = _check("good")
        msgs = [f.message for f in findings]
        assert not any("local-action" in m for m in msgs)

    def test_docker_ref_with_digest_not_flagged(self):
        """docker:// refs with @sha256: digest should not be flagged."""
        findings = _check("good")
        msgs = [f.message for f in findings]
        assert not any("docker://" in m for m in msgs)

    def test_empty_index_no_findings(self):
        assert _check_empty() == []


class TestGHABad:
    def setup_method(self):
        self.findings = _check("bad")

    def test_three_findings(self):
        assert len(self.findings) == 3, (
            "Expected 3 findings (2 tag refs + 1 docker), got {}: {}".format(
                len(self.findings), [f.message for f in self.findings]
            )
        )

    def test_checkout_v4_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("actions/checkout" in m for m in msgs)

    def test_setup_python_v4_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("actions/setup-python" in m for m in msgs)

    def test_docker_without_digest_flagged(self):
        msgs = [f.message for f in self.findings]
        assert any("docker://alpine" in m for m in msgs)

    def test_sha_pinned_not_flagged(self):
        """actions/cache with full SHA should not appear in findings."""
        msgs = [f.message for f in self.findings]
        assert not any("actions/cache" in m for m in msgs)

    def test_checker_name(self):
        for f in self.findings:
            assert f.checker == "github_actions"

    def test_paths_are_relative(self):
        for f in self.findings:
            assert not os.path.isabs(f.path)

    def test_line_numbers_positive(self):
        for f in self.findings:
            assert f.line > 0


class TestGHANoWorkflowDir:
    def test_no_github_workflows_no_findings(self):
        """A directory with no .github/workflows/ should produce 0 findings."""
        findings = _check("no_gha")
        assert findings == []


class TestGHANotInWorkflows:
    def test_yml_not_in_workflows_ignored(self):
        """YAML files outside .github/workflows/ must not be checked."""
        root = os.path.join(GHA_FIXTURES, "not_in_workflows")
        index = {root: {"some_config.yml"}}
        findings = GitHubActionsChecker().check(index, root)
        assert findings == [], "Files outside .github/workflows/ should be ignored"


_SHA = "ab12" * 10
_DIGEST = "@sha256:" + "a1b2c3d4" * 8


def _scan(subpath):
    # type: (str) -> list
    """Index a fixture tree the way the CLI does and run the checker over it."""
    from pinstack.core import build_index

    root = os.path.join(GHA_FIXTURES, subpath)
    index = build_index(root, set(GitHubActionsChecker.patterns))
    return [
        (f.path.replace(os.sep, "/"), f.line, f.message)
        for f in GitHubActionsChecker().check(index, root)
    ]


def _workflow(text, fname="ci.yml"):
    # type: (str, str) -> list
    import shutil
    import tempfile

    d = tempfile.mkdtemp()
    try:
        wf = os.path.join(d, ".github", "workflows")
        os.makedirs(wf)
        with open(os.path.join(wf, fname), "w") as fh:
            fh.write(text)
        return [
            (f.line, f.message) for f in GitHubActionsChecker().check({wf: {fname}}, d)
        ]
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestGHAFlowStyleAndContainers:
    """Review repro L1: flow style, quoted keys, containers, composite actions."""

    def test_repro(self):
        assert _scan("flow_and_containers") == [
            (
                ".github/actions/setup/action.yml",
                5,
                "action 'actions/setup-python' is not pinned to a full-length SHA (got 'v5')",
            ),
            (
                ".github/workflows/ci.yml",
                6,
                "container image 'node:20' is not pinned with @sha256: digest",
            ),
            (
                ".github/workflows/ci.yml",
                8,
                "action 'actions/checkout' is not pinned to a full-length SHA (got 'v4')",
            ),
            (
                ".github/workflows/ci.yml",
                9,
                "action 'actions/setup-node' is not pinned to a full-length SHA (got 'v4')",
            ),
        ]

    def test_container_string_form_and_services(self):
        findings = _workflow(
            "jobs:\n  t:\n    runs-on: ubuntu-latest\n    container: node:18\n"
            "    services:\n      redis:\n        image: 'redis:7'\n"
            '      db: {image: "postgres:16", ports: [5432]}\n'
            "      ok:\n        image: postgres" + _DIGEST + "\n"
        )
        assert findings == [
            (4, "container image 'node:18' is not pinned with @sha256: digest"),
            (7, "service 'redis' image 'redis:7' is not pinned with @sha256: digest"),
            (8, "service 'db' image 'postgres:16' is not pinned with @sha256: digest"),
        ]

    def test_container_expression_unverifiable(self):
        findings = _workflow(
            "jobs:\n  t:\n    container:\n      image: ${{ matrix.image }}\n"
        )
        assert [m for _, m in findings] == [
            "container image '${{ matrix.image }}' uses an expression which cannot"
            " be verified; write the image reference with an @sha256: digest"
        ]

    def test_reusable_workflow_job(self):
        findings = _workflow(
            "jobs:\n  call:\n    uses: org/repo/.github/workflows/x.yml@main\n"
        )
        assert [m for _, m in findings] == [
            "action 'org/repo/.github/workflows/x.yml' is not pinned to a full-length SHA (got 'main')"
        ]

    def test_with_inputs_and_run_scripts_are_not_refs(self):
        findings = _workflow(
            "jobs:\n  t:\n    steps:\n"
            "      - uses: actions/checkout@" + _SHA + "\n"
            "        with:\n          image: node:20\n          uses: not-a-ref\n"
            "      - run: |\n          uses: fake/action@v1\n"
            "          - uses: also/fake@v1\n"
        )
        assert findings == []

    def test_values_on_next_line_and_block_scalars(self):
        findings = _workflow(
            "jobs:\n  t:\n    steps:\n      - uses:\n          actions/cache@v4\n"
            "      - uses: >-\n          actions/upload-artifact@v4\n"
        )
        assert [line for line, _ in findings] == [5, 6]

    def test_aliases(self):
        findings = _workflow(
            "jobs:\n  t:\n    steps:\n      - uses: &co actions/checkout@v4\n"
            "      - uses: *co\n      - uses: *nowhere\n"
        )
        assert findings == [
            (
                4,
                "action 'actions/checkout' is not pinned to a full-length SHA (got 'v4')",
            ),
            (
                5,
                "action 'actions/checkout' is not pinned to a full-length SHA (got 'v4')",
            ),
            (6, "uses: '*nowhere' is a YAML alias pinstack could not resolve"),
        ]

    def test_docker_action_digest_must_be_64_hex(self):
        """Review L3: `@sha256:` must be followed by a full digest."""
        findings = _workflow(
            "jobs:\n  t:\n    steps:\n      - uses: docker://alpine@sha256:abc\n"
            "      - uses: docker://alpine" + _DIGEST + "\n"
        )
        assert findings == [
            (
                4,
                "docker action 'docker://alpine@sha256:abc' has a malformed @sha256:"
                " digest; expected 64 lowercase hex characters at the end of the reference",
            )
        ]

    def test_docker_action_metadata_image(self):
        import shutil
        import tempfile

        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "action.yaml"), "w") as fh:
                fh.write("runs:\n  using: docker\n  image: docker://alpine:3\n")
            findings = GitHubActionsChecker().check({d: {"action.yaml"}}, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [f.message for f in findings] == [
            "docker action image 'docker://alpine:3' is not pinned with @sha256: digest"
        ]


class TestGHAReviewFollowUps:
    """Local review of the L1/L3 change."""

    def test_apostrophe_in_flow_scalar_does_not_swallow_lines(self):
        findings = _workflow(
            "jobs:\n  t:\n    container: node:18\n    steps:\n"
            "      - {name: Don't fail, run: echo}\n"
            "      - uses: actions/checkout@v4\n"
        )
        assert [line for line, _ in findings] == [3, 6]

    def test_bom_prefixed_workflow(self):
        findings = _workflow(
            "\ufeffjobs:\n  call:\n    uses: o/r/.github/workflows/x.yml@main\n"
            "  t:\n    container: node:18\n"
        )
        assert [line for line, _ in findings] == [3, 5]

    def test_key_only_flow_entry(self):
        findings = _workflow(
            "jobs:\n  t:\n    steps:\n      - {continue-on-error, uses: actions/checkout@v4}\n"
        )
        assert [line for line, _ in findings] == [4]

    def test_document_marker_with_content(self):
        findings = _workflow("--- {jobs: {b: {container: node:18}}}\n")
        assert [m for _, m in findings] == [
            "container image 'node:18' is not pinned with @sha256: digest"
        ]

    def test_action_image_alias_is_reported(self):
        import shutil
        import tempfile

        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "action.yml"), "w") as fh:
                fh.write("runs:\n  using: docker\n  image: *img\n")
            findings = GitHubActionsChecker().check({d: {"action.yml"}}, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [f.message for f in findings] == [
            "docker action image '*img' is a YAML alias pinstack could not resolve"
        ]

    def test_multiline_flow_parses_in_linear_time(self):
        import time

        from pinstack.yaml_scan import scalars

        def run(n):
            body = "".join(
                "  {{name: s{0}, uses: a/b@v{0}}},\n".format(i) for i in range(n)
            )
            text = "steps: [\n" + body + "]\n"
            start = time.perf_counter()
            assert len(scalars(text)) == 2 * n
            return time.perf_counter() - start

        small, big = run(1000), run(4000)
        assert big < small * 12, (small, big)


class TestGHASecondReview:
    """Second local review of the L1/L3 change: multi-line scalars and flow."""

    def test_plain_continuation_starting_with_bracket(self):
        findings = _workflow(
            "jobs:\n  t:\n    container: node:20\n    steps:\n"
            "      - run: echo\n          [x\n"
            "      - uses: evil/act@main\n"
        )
        assert [line for line, _ in findings] == [3, 7]

    def test_multiline_double_quoted_value(self):
        findings = _workflow(
            'jobs:\n  t:\n    steps:\n      - name: "a\n          {"\n'
            "      - uses: evil/act@main\n"
        )
        assert [line for line, _ in findings] == [6]

    def test_stray_closer_in_flow_sequence_terminates(self):
        from pinstack.yaml_scan import scalars

        scalars("steps: [ } ]\n")
        scalars("jobs: {b: {steps: [a, }]}}\n")

    def test_quoted_string_spanning_lines_in_flow(self):
        findings = _workflow(
            'jobs:\n  t:\n    env: {X: ["a\n      [" ]}\n    steps:\n'
            "      - uses: evil/act@main\n"
        )
        assert [line for line, _ in findings] == [6]

    def test_indented_marker_does_not_reset_path(self):
        findings = _workflow(
            "jobs:\n  b:\n    env:\n      A: x\n        %y\n    container: node:20\n"
        )
        assert [m for _, m in findings] == [
            "container image 'node:20' is not pinned with @sha256: digest"
        ]

    def test_implicit_pair_in_flow_sequence(self):
        findings = _workflow("jobs:\n  t:\n    steps: [uses: evil/act@main]\n")
        assert [line for line, _ in findings] == [3]

    def test_sequence_block_scalar_content_is_not_structure(self):
        findings = _workflow("jobs:\n  t:\n    steps:\n    - |\n     uses: x\n")
        assert findings == []

    def test_matrix_variable_named_uses_is_data(self):
        findings = _workflow(
            "jobs:\n  t:\n    strategy:\n      matrix:\n        include:\n"
            "          - uses: foo\n"
        )
        assert findings == []


class TestGHAStepAliases:
    """Codex review of #19: an alias standing for a whole step or job."""

    def test_repro_step_alias(self):
        """The anchor sits in matrix data, but the alias is a real step."""
        assert _scan("step_alias") == [
            (
                ".github/workflows/ci.yml",
                12,
                "action 'actions/checkout' is not pinned to a full-length SHA"
                " (got 'v4')",
            )
        ]

    def test_merged_step(self):
        findings = _workflow(
            "x-base: &base\n  uses: actions/cache@v4\n"
            "jobs:\n  t:\n    steps:\n      - <<: *base\n        with: {}\n"
        )
        assert findings == [
            (6, "action 'actions/cache' is not pinned to a full-length SHA (got 'v4')")
        ]

    def test_aliased_job_and_steps(self):
        findings = _workflow(
            "jobs:\n  a: &job\n    runs-on: x\n    steps: &steps\n"
            "      - uses: actions/checkout@" + _SHA + "\n"
            "    container: node:20\n"
            "  b: *job\n"
            "  c:\n    steps: *steps\n"
        )
        assert findings == [
            (6, "container image 'node:20' is not pinned with @sha256: digest"),
            (7, "container image 'node:20' is not pinned with @sha256: digest"),
        ]

    def test_unresolved_aliases_at_structural_positions(self):
        findings = _workflow(
            "jobs:\n  a: *j\n  b:\n    steps:\n      - *s\n      - <<: *m\n"
            "    services: *svc\n    with:\n      x: *data\n"
        )
        assert findings == [
            (2, "'*j' at jobs.a is a YAML alias pinstack could not resolve"),
            (5, "'*s' at jobs.b.steps.- is a YAML alias pinstack could not resolve"),
            (6, "'*m' at jobs.b.steps.-.<< is a YAML alias pinstack could not resolve"),
            (7, "'*svc' at jobs.b.services is a YAML alias pinstack could not resolve"),
        ]

    def test_unresolved_step_alias_in_action(self):
        import shutil
        import tempfile

        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(d, "action.yml"), "w") as fh:
                fh.write("runs:\n  using: composite\n  steps:\n    - *s\n")
            findings = GitHubActionsChecker().check({d: {"action.yml"}}, d)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        assert [f.message for f in findings] == [
            "'*s' at runs.steps.- is a YAML alias pinstack could not resolve"
        ]

    def test_too_deep_flow_is_reported(self):
        findings = _workflow("jobs:\n  t:\n    steps: " + "[" * 500 + "]" * 500 + "\n")
        assert findings == [
            (
                3,
                "ci.yml has flow collections nested more than 100 deep, which"
                " pinstack cannot read",
            )
        ]

    def test_inner_anchor_of_same_name(self):
        findings = _workflow(
            "x: &a\n  y: &a actions/checkout@v4\n"
            "jobs: {t: {runs-on: x, steps: [{uses: *a}]}}\n"
        )
        assert [line for line, _ in findings] == [3]

    def test_anchored_flow_step_and_tagged_key(self):
        findings = _workflow(
            "jobs:\n  t:\n    steps:\n      - &c {uses: actions/checkout@v4}\n"
            "      - !!str uses: actions/cache@v4\n"
        )
        assert [line for line, _ in findings] == [4, 5]
