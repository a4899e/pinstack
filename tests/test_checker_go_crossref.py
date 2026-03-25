"""Tests for Go checker cross-referencing go.mod deps against go.sum."""

import os
import tempfile

from pinstack.checkers.go import GoChecker


def _make_index(tmpdir, files):
    # type: (str, dict) -> dict
    """Write files dict {filename: content} into tmpdir and return index."""
    for fname, content in files.items():
        with open(os.path.join(tmpdir, fname), "w") as fh:
            fh.write(content)
    return {tmpdir: set(files.keys())}


class TestGoDepMissingFromGoSum:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        index = _make_index(self.tmpdir, {
            "go.mod": (
                "module example.com/myapp\n"
                "\n"
                "go 1.21\n"
                "\n"
                "require github.com/missing/dep v1.2.3\n"
            ),
            "go.sum": (
                "github.com/other/thing v0.1.0 h1:abc=\n"
                "github.com/other/thing v0.1.0/go.mod h1:def=\n"
            ),
        })
        self.findings = GoChecker().check(index, self.tmpdir)

    def test_dep_in_gomod_missing_from_gosum(self):
        msgs = [f.message for f in self.findings]
        assert any("github.com/missing/dep" in m for m in msgs), (
            "Expected finding for missing dep, got: {}".format(msgs)
        )

    def test_message_format(self):
        msgs = [f.message for f in self.findings]
        assert any("not found in go.sum" in m for m in msgs)

    def test_checker_name(self):
        cross_ref = [f for f in self.findings if "not found in go.sum" in f.message]
        assert len(cross_ref) >= 1
        assert cross_ref[0].checker == "go"

    def test_path_is_relative(self):
        cross_ref = [f for f in self.findings if "not found in go.sum" in f.message]
        assert not os.path.isabs(cross_ref[0].path)


class TestGoDepPresentInGoSum:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        index = _make_index(self.tmpdir, {
            "go.mod": (
                "module example.com/myapp\n"
                "\n"
                "go 1.21\n"
                "\n"
                "require github.com/present/dep v1.0.0\n"
            ),
            "go.sum": (
                "github.com/present/dep v1.0.0 h1:abc=\n"
                "github.com/present/dep v1.0.0/go.mod h1:def=\n"
            ),
        })
        self.findings = GoChecker().check(index, self.tmpdir)

    def test_dep_in_gomod_present_in_gosum(self):
        cross_ref = [f for f in self.findings if "not found in go.sum" in f.message]
        assert cross_ref == [], (
            "No cross-ref findings expected when dep is in go.sum, got: {}".format(
                [f.message for f in cross_ref]
            )
        )


class TestGoModRequireBlockParsed:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        index = _make_index(self.tmpdir, {
            "go.mod": (
                "module example.com/myapp\n"
                "\n"
                "go 1.21\n"
                "\n"
                "require (\n"
                "\tgithub.com/in/sum v1.0.0\n"
                "\tgithub.com/not/insum v2.0.0\n"
                "\tgithub.com/also/missing v3.0.0\n"
                ")\n"
            ),
            "go.sum": (
                "github.com/in/sum v1.0.0 h1:abc=\n"
                "github.com/in/sum v1.0.0/go.mod h1:def=\n"
            ),
        })
        self.findings = GoChecker().check(index, self.tmpdir)

    def test_gomod_require_block_parsed(self):
        cross_ref = [f for f in self.findings if "not found in go.sum" in f.message]
        missing = [f.message for f in cross_ref]
        assert any("github.com/not/insum" in m for m in missing), (
            "Expected github.com/not/insum to be flagged, got: {}".format(missing)
        )
        assert any("github.com/also/missing" in m for m in missing), (
            "Expected github.com/also/missing to be flagged, got: {}".format(missing)
        )

    def test_present_dep_not_flagged(self):
        cross_ref = [f for f in self.findings if "not found in go.sum" in f.message]
        missing = [f.message for f in cross_ref]
        assert not any("github.com/in/sum" in m for m in missing), (
            "github.com/in/sum should NOT be flagged, got: {}".format(missing)
        )

    def test_two_missing_deps_flagged(self):
        cross_ref = [f for f in self.findings if "not found in go.sum" in f.message]
        assert len(cross_ref) == 2, (
            "Expected exactly 2 cross-ref findings, got {}: {}".format(
                len(cross_ref), [f.message for f in cross_ref]
            )
        )
