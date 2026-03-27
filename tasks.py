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
    """Run security checks: bandit."""
    ctx.run("bandit -r pinstack/ -q", pty=True)


@task
def test(ctx: Context) -> None:
    """Run unit tests."""
    ctx.run("python -m pytest tests/ -v --tb=short", pty=True)


@task(pre=[clean, lint, security, test])
def build(ctx: Context) -> None:
    """Full local CI gate: clean + lint + security + test."""
    print("  build passed")
