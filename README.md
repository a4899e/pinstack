# pinstack

Supply-chain dependency pinning enforcement for every ecosystem.

pinstack scans a project directory and reports unpinned or loosely-pinned
dependencies across the most common package managers, container formats, and
infrastructure tools. It is designed to drop into any CI pipeline with a single
command and zero setup beyond `pip install pinstack`.

---

## Why This Matters

A version range is a promise someone else can rewrite. `requests>=2.28`,
`FROM python:3.11-slim`, and `uses: actions/checkout@v4` all resolve to
whatever bytes the registry serves at the moment of the build, and that answer
can change without the version string changing at all. A hijacked maintainer
account, a re-pushed tag, a typosquat that wins a resolution race, or a
compromised mirror each produce a build that is green, correct according to the
manifest, and running code nobody reviewed.

Pinning to an exact version narrows that window. Pinning to a *hash* closes it:
a digest names the content itself, so substituted bytes fail the build loudly
instead of shipping silently. That is the difference between a build that is
reproducible and one that merely looks reproducible.

The catch is that every ecosystem spells this differently -- `==` plus `--hash`
in pip, `integrity` in npm, `@sha256:` in Docker, a 40-character commit SHA in
GitHub Actions, `h1:` in Go and Terraform. A real repository uses several at
once, and one unpinned entry reopens the window. pinstack checks all of them in
a single pass, so "is this repository actually pinned?" becomes a question with
a yes-or-no answer you can put in CI.

---

## Project Goals

**1. Broad ecosystem coverage**

pinstack understands as many project and language types as possible. A single
invocation on a monorepo surfaces pinning issues in Python packages, Node
modules, Docker images, Go modules, Rust crates, Ruby gems, Terraform providers,
Helm charts, and more.

**2. Zero dependencies**

pinstack has no runtime dependencies outside the Python standard library. It
runs on any system's `python3` (3.9 or later) without a virtual environment or
any other setup step. This makes it practical to adopt in non-Python projects
where adding a Python dependency tree would be unwelcome.

**3. CLI-only configuration**

There are no config files to maintain. Every behaviour is controlled by flags
passed on the command line. What you see in the command is what it does, making
pinstack easy to audit, reproduce, and reason about in CI logs.

---

## Installation

```
pip install pinstack
```

Because pinstack has zero runtime dependencies, it can also be run directly
from a checkout without installation. Run it from the checkout root:

```
git clone https://github.com/a4899e/pinstack.git
cd pinstack
python3 -m pinstack .
```

To scan a project elsewhere without installing, put the checkout root on
`PYTHONPATH`:

```
PYTHONPATH=/path/to/pinstack python3 -m pinstack /path/to/project
```

---

## Quick Start

Scan the current directory:

```
pinstack .
```

Only check Python and Docker dependency files:

```
pinstack . --check requirements,pyproject,dockerfile
```

Output as SARIF for GitHub Code Scanning integration:

```
pinstack . --format sarif > results.sarif
```

---

## Supported Ecosystems

Sixteen checkers, run in a single pass. Where a manifest and a lock file both
exist, pinstack also cross-references them: a dependency declared in the
manifest but absent from the lock file means the lock file is stale, which
silently defeats the pinning it was supposed to guarantee.

| Checker | Files Examined | What Is Checked |
|---|---|---|
| `requirements` | `requirements*.txt` | `==` pinning and `--hash=` integrity markers |
| `pyproject` | `pyproject.toml`, plus `requirements*.txt`, `poetry.lock`, `pdm.lock`, `uv.lock` | `==` exact pins in `[project]`, `[project.optional-dependencies]`, `[dependency-groups]`, and `[tool.poetry]`; a companion lock file must exist and must not be stale |
| `package_json` | `package.json`, plus `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml` | No `^`, `~`, or range specifiers in any dependency section; declared packages must appear in the lock file |
| `package_lock` | `package-lock.json` | `integrity` hashes present on all packages |
| `yarn_lock` | `yarn.lock` | Integrity checksums present |
| `pnpm_lock` | `pnpm-lock.yaml` | `integrity` hashes present |
| `dockerfile` | `Dockerfile*` | `@sha256:` digest on every `FROM` |
| `compose` | `docker-compose*.yml`, `docker-compose*.yaml`, `compose*.yml`, `compose*.yaml` | `@sha256:` digest on `image:` references |
| `github_actions` | `*.yml` and `*.yaml` under `.github/workflows/` | 40-char commit SHA after `@` in `uses:` |
| `go` | `go.mod`, `go.sum` | `go.sum` exists alongside `go.mod`; `h1:` hashes present; every `require` appears in `go.sum` |
| `cargo` | `Cargo.lock`, `Cargo.toml` | `checksum` field on each registry `[[package]]`; every `Cargo.toml` dependency appears in `Cargo.lock` |
| `gemfile` | `Gemfile`, `Gemfile.lock` | Lock file exists alongside `Gemfile`; every declared gem appears in it |
| `terraform` | `.terraform.lock.hcl` | `h1:` hashes present per provider |
| `helm` | `Chart.yaml`, `Chart.lock` | Lock file exists when `Chart.yaml` has dependencies; `digest:` present |
| `maven` | `pom.xml` | Exact `<version>` required -- no ranges, `LATEST`/`RELEASE`, `SNAPSHOT`, unresolvable `${property}` references, or missing versions |
| `gradle` | `build.gradle`, `build.gradle.kts`, `gradle.lockfile` | Exact versions -- no dynamic (`+`) or range versions; `gradle.lockfile` must exist and must not be stale |

Run `pinstack --list-checkers` to print this list from the installed version.

---|---|---|
| `requirements` | `requirements*.txt` | `==` pinning and `--hash` verification |
| `pyproject` | `pyproject.toml` | `==` exact pins in `[project.dependencies]` and `[project.optional-dependencies]` |
| `package_json` | `package.json` | No `^`, `~`, or range specifiers in any dependency section |
| `package_lock` | `package-lock.json` | `integrity` hashes present on all packages |
| `yarn_lock` | `yarn.lock` | Integrity checksums present |
| `pnpm_lock` | `pnpm-lock.yaml` | `integrity` hashes present |
| `dockerfile` | `Dockerfile*` | `@sha256:` digest on every `FROM` |
| `github_actions` | `.github/workflows/*.yml` | 40-char commit SHA after `@` in `uses:` |
| `go` | `go.mod`, `go.sum` | `go.sum` exists alongside `go.mod`; `h1:` hashes present |
| `cargo` | `Cargo.lock` | `checksum` field on each registry `[[package]]` |
| `gemfile` | `Gemfile`, `Gemfile.lock` | Lock file exists alongside Gemfile |
| `terraform` | `.terraform.lock.hcl` | `h1:` hashes present per provider |
| `helm` | `Chart.yaml`, `Chart.lock` | Lock file exists when Chart.yaml has dependencies; `digest:` present |
| `compose` | `docker-compose*.yml`, `compose*.yml` | `@sha256:` digest on `image:` references |

---

## CLI Reference

```
pinstack [PATH] [OPTIONS]
```

| Option | Description |
|---|---|
| `PATH` | Directory to scan. Defaults to `.` (current directory). |
| `--check NAME,NAME` | Only run these checkers (comma-separated). Mutually exclusive with `--exclude`. |
| `--exclude NAME,NAME` | Skip these checkers (comma-separated). Mutually exclusive with `--check`. |
| `--format FORMAT` | Output format: `text` (default) or `sarif`. |
| `--exit-zero` | Always exit 0, even when findings are present. Useful for advisory-only runs. |
| `--max-depth N` | Maximum directory depth to recurse into. Default: 4. Increase for deep monorepos. |
| `--max-files N` | Maximum number of dependency files to index. Default: 384. Increase for large monorepos. A warning is printed to stderr if the limit is reached. |
| `--exclude-dir DIR,DIR` | Additional directory names to skip during traversal (comma-separated). |
| `--list-checkers` | Print all available checkers and exit. |
| `--version` | Print the pinstack version and exit. |

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | No findings (or `--exit-zero` was set). |
| `1` | One or more findings reported. |
| `2` | Runtime error. |

---

## Output Formats

### text (default)

Human-readable output suitable for local development and CI logs:

```
FAIL  Dockerfile:1  FROM 'python:3.11-slim' is not pinned with @sha256: digest
FAIL  go.mod  go.mod has no corresponding go.sum; run 'go mod tidy'
FAIL  requirements.txt:1  'requests' is not pinned with ==; use package==version

3 errors in 3 files
```

Findings without a meaningful line number (a missing lock file, for instance)
are reported against the file alone.

### sarif

SARIF 2.1.0 JSON, consumable by GitHub Code Scanning, GitLab SAST, and other
tools that support the standard.

---

## CI Integration

pinstack is a standalone CLI tool, not a library. It works with any project
regardless of language. Python 3.9+ is the only requirement, and it ships on
virtually every CI runner out of the box.

### GitHub Actions

```yaml
name: Supply Chain Check
on: [push, pull_request]

jobs:
  pinstack:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@a5ac7e51b41094c92402da3b24376905380afc29

      - name: Set up Python
        uses: actions/setup-python@82c7e631bb3cdc910f68e0081d67478d79c6982d
        with:
          python-version: "3.x"

      - name: Install pinstack
        run: pip install pinstack

      - name: Check dependency pinning
        run: pinstack .
```

To upload results to GitHub Code Scanning:

```yaml
      - name: Check dependency pinning (SARIF)
        run: pinstack . --format sarif > pinstack.sarif
        continue-on-error: true

      - name: Upload SARIF
        uses: github/codeql-action/upload-sarif@b56ba49b26e50535fa1e7f7db0f4f7b4bf65d80d
        with:
          sarif_file: pinstack.sarif
```

### GitLab CI

```yaml
pinstack:
  stage: test
  image: python:3-slim
  before_script:
    - pip install pinstack
  script:
    - pinstack .
```

To integrate with GitLab SAST, output SARIF and use the
[SAST report artifact](https://docs.gitlab.com/ee/ci/yaml/artifacts_reports.html):

```yaml
pinstack:
  stage: test
  image: python:3-slim
  before_script:
    - pip install pinstack
  script:
    - pinstack . --format sarif > gl-sast-report.json || true
  artifacts:
    reports:
      sast: gl-sast-report.json
```

### Python Projects (Invoke)

Add a task to your `tasks.py`:

```python
from invoke import task


@task
def supply_chain(c):
    """Check dependency pinning."""
    c.run("pinstack .")
```

Then run it with `invoke supply-chain` or include it in your build task:

```python
@task(pre=[lint, typecheck, test, supply_chain])
def build(c):
    """Full build pipeline."""
    pass
```

### Any Project

pinstack runs anywhere Python 3.9+ is available. No virtual environment needed,
no dependencies to install beyond pinstack itself.

**With pip:**

```
pip install pinstack
pinstack .
```

**Without pip (clone and run directly):**

```
git clone https://github.com/a4899e/pinstack.git /tmp/pinstack
PYTHONPATH=/tmp/pinstack python3 -m pinstack .
```

`PYTHONPATH` must point at the checkout root, not at the inner `pinstack/`
package directory.

**Check only specific ecosystems:**

```
# Java project -- only check Maven and Gradle
pinstack . --check maven,gradle

# Node project -- only check JS ecosystem
pinstack . --check package_json,package_lock,yarn_lock,pnpm_lock

# Docker-only check
pinstack . --check dockerfile,compose
```

**Skip directories that contain third-party code:**

```
pinstack . --exclude-dir vendor,third_party,checkouts
```

---

## Contributing

1. Fork the repository at https://github.com/a4899e/pinstack
2. Create a branch from `main`
3. Add tests for any new checker or behaviour
4. Open a pull request against `main`

Development setup (requires Python 3.10+ and [uv](https://docs.astral.sh/uv/)):

```
./scripts/dev-setup.sh
```

> **Note:** pinstack *runs* on Python 3.9+, but *development* requires 3.10+
> because some build-chain tools (bandit) need it.

### Running Tests

```
uv run inv test                              # run the full test suite
uv run python -m pytest tests/ -v            # or run pytest directly
uv run python -m pytest tests/test_checker_go.py  # run a single test file
```

### Build Pipeline

The pre-commit hook runs `uv run inv build` before each commit. You can also
run it manually:

```
uv run inv lint          # ruff check + ruff format --check + pyright
uv run inv security      # pinstack self-scan + bandit + pip-audit + detect-secrets
uv run inv test          # pytest
uv run inv build         # clean + lint + security + test (full CI gate)
uv run inv clean         # remove .pyc, caches, build artifacts
```

---

## License

Apache 2.0. See [LICENSE](LICENSE) for the full text.

Copyright 2024 Trevor T. <trevort@scantonomous.ai>
