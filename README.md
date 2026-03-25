# pinstack

Supply-chain dependency pinning enforcement for every ecosystem.

pinstack scans a project directory and reports unpinned or loosely-pinned
dependencies across the most common package managers, container formats, and
infrastructure tools. It is designed to drop into any CI pipeline with a single
command and zero setup beyond `pip install pinstack`.

---

## Project Goals

**1. Broad ecosystem coverage**

pinstack understands as many project and language types as possible. A single
invocation on a monorepo surfaces pinning issues in Python packages, Node
modules, Docker images, Go modules, Rust crates, Ruby gems, Terraform providers,
Helm charts, and more.

**2. Zero dependencies**

pinstack has no runtime dependencies outside the Python standard library. It
runs on any system's `python3` (3.8 or later) without a virtual environment or
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
from a checkout without installation:

```
python -m pinstack .
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

| Checker | Files Examined | What Is Checked |
|---|---|---|
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
| `--max-depth N` | Maximum directory depth to recurse into. Default: 4. |
| `--max-files N` | Maximum number of files to index. Default: 384. |
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
FAIL  Dockerfile:3  FROM without @sha256: digest: python:3.11-slim
FAIL  requirements.txt:7  not pinned with ==: requests>=2.28
FAIL  go.mod  go.sum missing alongside go.mod

3 errors in 3 files
```

### sarif

SARIF 2.1.0 JSON, consumable by GitHub Code Scanning, GitLab SAST, and other
tools that support the standard.

---

## CI Integration

pinstack is a standalone CLI tool, not a library. It works with any project
regardless of language. Python 3.8+ is the only requirement, and it ships on
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

pinstack runs anywhere Python 3.8+ is available. No virtual environment needed,
no dependencies to install beyond pinstack itself.

**With pip:**

```
pip install pinstack
pinstack .
```

**Without pip (clone and run directly):**

```
git clone https://github.com/a4899e/pinstack.git /tmp/pinstack
python3 /tmp/pinstack/pinstack .
```

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
2. Create a branch from `develop` (not `main`)
3. Add tests for any new checker or behaviour
4. Open a pull request against `develop`

Development setup:

```
pip install -e ".[dev]"
pytest
ruff check .
pyright
```

---

## License

Apache 2.0. See [LICENSE](LICENSE) for the full text.

Copyright 2024 smthmlk
