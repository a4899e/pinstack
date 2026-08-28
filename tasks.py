"""Invoke task definitions for pinstack.

Standard targets: clean, lint, security, test, build.
All tools are invoked via the project venv (use `uv run inv <task>`).
"""

import glob
import os
import shutil

from invoke import Context, task


@task
def clean(ctx: Context) -> None:
    """Remove build artifacts, caches, and compiled files."""
    for pattern in ["**/__pycache__", ".pytest_cache", ".ruff_cache", "dist", "build"]:
        for path in glob.glob(pattern, recursive=True):
            if os.path.isdir(path):
                shutil.rmtree(path)
                print(f"  removed {path}/")

    for path in glob.glob("**/*.pyc", recursive=True):
        os.remove(path)

    for path in glob.glob("*.egg-info"):
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"  removed {path}/")


@task
def lint(ctx: Context) -> None:
    """Run code quality checks: ruff lint, ruff format, pyright."""
    ctx.run("ruff check .", pty=True)
    ctx.run("ruff format --check .", pty=True)
    ctx.run("pyright", pty=True)


@task
def security(ctx: Context) -> None:
    """Run security checks: pinstack, bandit, pip-audit, detect-secrets."""
    ctx.run("pinstack . --exclude-dir fixtures", pty=True)
    ctx.run("bandit -r pinstack/ -q", pty=True)
    # pip-audit can't read uv.lock, so we export runtime-only deps to a requirements
    # file it can consume. --no-dev excludes the build-chain dependency group.
    # pinstack has no runtime deps today, so this produces an empty file — pip-audit
    # passes with nothing to audit. If runtime deps are added later, they'll be
    # audited automatically.
    ctx.run(
        "uv export --no-dev --no-emit-project --format requirements-txt"
        " -o .runtime-deps.txt",
        pty=True,
    )
    ctx.run(
        "pip-audit --desc --require-hashes --disable-pip -r .runtime-deps.txt",
        pty=True,
    )
    # Use detect-secrets-hook, not `detect-secrets scan --baseline`. `scan`
    # UPDATES the baseline in place: it exits 0 on a newly committed secret and
    # silently writes it into .secrets.baseline as an accepted entry, so the
    # only symptom was a dirty baseline that everyone discards. The hook entry
    # point checks against the baseline instead -- it exits non-zero on a new
    # secret and never writes to the file.
    ctx.run(
        "git ls-files -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline",
        pty=True,
    )


@task
def test(ctx: Context) -> None:
    """Run unit tests."""
    ctx.run("python -m pytest tests/ -v --tb=short", pty=True)


@task(pre=[clean, lint, security, test])
def build(ctx: Context) -> None:
    """Full local CI gate: clean + lint + security + test."""
    print("  build passed")
