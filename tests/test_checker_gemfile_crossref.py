"""Tests for Gemfile checker cross-referencing Gemfile deps against Gemfile.lock."""

import os
import tempfile

from pinstack.checkers.gemfile import GemfileChecker


def _make_index(tmpdir, files):
    # type: (str, dict) -> dict
    """Write files dict {filename: content} into tmpdir and return index."""
    for fname, content in files.items():
        with open(os.path.join(tmpdir, fname), "w") as fh:
            fh.write(content)
    return {tmpdir: set(files.keys())}


GEMFILE_LOCK_RAILS_PUMA = """\
GEM
  remote: https://rubygems.org/
  specs:
    rails (7.0.8)
    puma (6.4.0)

BUNDLED WITH
   2.4.10
"""

GEMFILE_LOCK_RAILS_ONLY = """\
GEM
  remote: https://rubygems.org/
  specs:
    rails (7.0.8)

BUNDLED WITH
   2.4.10
"""


class TestDepInGemfileMissingFromLock:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        index = _make_index(self.tmpdir, {
            "Gemfile": (
                'source "https://rubygems.org"\n'
                '\n'
                'gem "rails", "7.0.8"\n'
                'gem "sidekiq"\n'
            ),
            "Gemfile.lock": GEMFILE_LOCK_RAILS_ONLY,
        })
        self.findings = GemfileChecker().check(index, self.tmpdir)

    def test_dep_in_gemfile_missing_from_lock(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        msgs = [f.message for f in cross_ref]
        assert any("sidekiq" in m for m in msgs), (
            "Expected finding for sidekiq, got: {}".format(msgs)
        )

    def test_present_dep_not_flagged(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        msgs = [f.message for f in cross_ref]
        assert not any("rails" in m for m in msgs), (
            "rails should NOT be flagged, got: {}".format(msgs)
        )

    def test_checker_name(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        assert len(cross_ref) >= 1
        assert cross_ref[0].checker == "gemfile"

    def test_path_is_relative(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        assert not os.path.isabs(cross_ref[0].path)


class TestDepInGemfilePresentInLock:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        index = _make_index(self.tmpdir, {
            "Gemfile": (
                'source "https://rubygems.org"\n'
                '\n'
                'gem "rails", "7.0.8"\n'
                'gem "puma", "6.4.0"\n'
            ),
            "Gemfile.lock": GEMFILE_LOCK_RAILS_PUMA,
        })
        self.findings = GemfileChecker().check(index, self.tmpdir)

    def test_dep_in_gemfile_present_in_lock(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        assert cross_ref == [], (
            "No cross-ref findings expected when all gems are in lock, got: {}".format(
                [f.message for f in cross_ref]
            )
        )


class TestGemfileVariousFormats:
    """Single/double quotes and with/without version constraints all parse correctly."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        index = _make_index(self.tmpdir, {
            "Gemfile": (
                'source "https://rubygems.org"\n'
                '\n'
                'gem "rails", "7.0"\n'          # double quotes with version
                "gem 'sidekiq'\n"                # single quotes no version
                "gem 'puma', '~> 6.0'\n"         # single quotes with constraint
            ),
            "Gemfile.lock": GEMFILE_LOCK_RAILS_PUMA,  # has rails and puma, not sidekiq
        })
        self.findings = GemfileChecker().check(index, self.tmpdir)

    def test_gemfile_various_formats(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        msgs = [f.message for f in cross_ref]
        # sidekiq is missing from lock
        assert any("sidekiq" in m for m in msgs), (
            "Expected sidekiq to be flagged, got: {}".format(msgs)
        )

    def test_double_quoted_dep_found(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        msgs = [f.message for f in cross_ref]
        assert not any("rails" in m for m in msgs), "rails (double-quoted) should be found in lock"

    def test_single_quoted_with_constraint_found(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        msgs = [f.message for f in cross_ref]
        assert not any("puma" in m for m in msgs), "puma (single-quoted with constraint) should be found in lock"

    def test_only_one_missing(self):
        cross_ref = [f for f in self.findings if "stale" in f.message]
        assert len(cross_ref) == 1, (
            "Expected exactly 1 cross-ref finding, got {}: {}".format(
                len(cross_ref), [f.message for f in cross_ref]
            )
        )
