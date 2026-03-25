"""Invoke task definitions for pinstack."""

from invoke import task


@task
def clean(c):
    """Remove .pyc files, __pycache__, build artifacts, and caches."""
    c.run("find . -type f -name '*.pyc' -delete")
    c.run("find . -type d -name '__pycache__' -exec rm -rf {} +")
    c.run("rm -rf build dist *.egg-info .pytest_cache .ruff_cache")


@task
def test(c):
    """Run the test suite."""
    c.run("python -m pytest tests/ -v --tb=short")


@task(pre=[test])
def build(c):
    """Lint, type-check, and run tests."""
    c.run("ruff check .")
    c.run("pyright")
