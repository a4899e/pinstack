# Changelog

## Unreleased

### Added

- **`--build-context NAME,NAME`** declares BuildKit named contexts that the build supplies with `docker buildx build --build-context NAME=...`. A Dockerfile may then refer to those names in `FROM`, `COPY`/`ADD --from` and `RUN --mount=from=` as it would a stage. In 0.3.0 such a reference was reported as an unpinned Docker Hub image, which is what BuildKit pulls if the build omits the flag, and there was no way to say otherwise. Names must be valid stage names, so a tagged image such as `alpine:3.18` cannot be declared, and the `# syntax=` frontend still needs a digest.

## 0.3.0 (2026-11-02)

This release closes gaps where pinstack passed dependency references it should have reported. Nearly every checker is stricter, so **a repository that passes on 0.2.0 may fail on 0.3.0**. Read *Upgrading* before you roll it out.

pinstack still has no runtime dependencies and still runs on Python 3.9 and later.

### Upgrading

These are the changes most likely to turn a passing run into a failing one. Each checker's own changes are listed further down.

- **Files beyond the scan limits are now findings.** A dependency file below `--max-depth` (default 4, so for example `apps/team/svc/docker/Dockerfile`) used to be skipped without a word. It is now a `scan incomplete` finding naming the first skipped file. Reaching `--max-files` is a finding too, not just a warning. Raise the limit or `--exclude-dir` the directory.
- **Lock files must exist and contain packages.** An empty or unreadable `package-lock.json`, `uv.lock`, `poetry.lock` or `pdm.lock` no longer counts as a lock. Cargo and Terraform now report a missing lock, as the other ecosystems already did.
- **Image digests are validated.** `@sha256:` must be followed by exactly 64 lowercase hex characters and end the reference. A truncated digest such as `@sha256:aaaa` used to pass. This applies to Dockerfiles, compose files, `docker://` actions and workflow containers.
- **Variable references are reported as unverifiable**, because the build command, not the file, decides what they resolve to: `FROM ${BASE}` in a Dockerfile (even when the `ARG` default is pinned), `$v` in Gradle, `${VAR}` in compose images and requirements files, `${{ }}` in workflow images.
- **requirements.txt lines pip would refuse are reported.** A line with an option pip does not accept in requirements files (for example `--install-option`, which current pip no longer supports), an ambiguous or incomplete option, or an unclosed quote is now a finding, since pinstack cannot read the rest of that line.

### Checkers

**Dockerfile**
- A bare image name (`FROM alpine`) is skipped only when an earlier `FROM ... AS <name>` declared that stage. Before, any name without `:`, `/` or `.` was assumed to be a stage.
- `COPY --from=`, `ADD --from=`, `RUN --mount=...,from=` and their `ONBUILD` forms are checked.
- The `# syntax=` frontend image must be pinned by digest.
- Line continuations, heredocs, the `# escape=` directive and a UTF-8 BOM are handled.
- `Containerfile*`, `*.dockerfile` and `*.Dockerfile` are scanned.

**docker compose**
- Compose files are read as YAML rather than line by line, so flow style (`services: {web: {image: nginx:1.25}}`), anchors and aliases are checked.
- `${REGISTRY}/app@sha256:<digest>` passes, since the digest still names the content.

**GitHub Actions**
- Workflows are read as YAML, so flow-style steps (`- {uses: actions/checkout@v4}`), quoted keys, anchors and aliases are checked.
- Job containers, service containers and reusable workflows (`jobs.<id>.uses`) are checked.
- Composite and Docker actions (`action.yml` / `action.yaml`) are scanned, including their steps' `uses:` and a Docker action's `runs.image`.
- `uses:` counts only under `steps` or a job, so a matrix variable named `uses` is not mistaken for one.

**package.json**
- Only exact versions pass: `1.2.3` (optionally `=` or `v` prefixed), local references (`workspace:`, `file:`, `link:`, relative paths), `npm:` aliases to an exact version, and git sources pinned to a full 40-character commit SHA.
- Newly reported: `*`, `latest` and other dist-tags, `1`, `1.x`, hyphen and `||` ranges, ranges inside `npm:` aliases, and non-string specs.
- `overrides`, `resolutions` and `pnpm.overrides` are checked by the same rules.
- Git dependencies pinned to a full SHA (`user/repo#<sha>`, `git+https://...#<sha>`) now pass; before, some were flagged.
- Lock parsing fixes: `package-lock.json` version 1, and `pnpm-lock.yaml` v5, v6+ peer suffixes and v9 keys.

**pyproject.toml**
- Wildcard pins (`==2.*`, Poetry `2.*`) are reported.
- Inline-table and dotted-key forms of `[project]`, `optional-dependencies` and `dependency-groups` are read.
- Poetry table form (`[tool.poetry.dependencies.<name>]`) and multi-constraint arrays are checked.

**requirements.txt**
- `-r` / `--requirement` includes are followed and checked, whatever the included file is called. Includes that are missing, unreadable, outside the scan root (including through a symlink), remote, or built from a variable are reported. Include cycles are handled.
- `--hash` values must use `sha256`, `sha384` or `sha512` with a full-length hex digest.
- Lines are read as pip reads them: continuations, comments (so a URL's `#egg=` fragment no longer hides the `--hash` after it), several options per line, and option prefixes.
- A requirement pinstack does not recognise is reported instead of skipped. A bare URL or local path passes only with a valid `--hash`.
- `foo == 1.0` (spaces around `==`) passes.
- Constraint files (`-c`) are not checked as requirements, since they install nothing; their own `-r` includes are still followed.

**Maven**
- POMs are read in any namespace, so Maven 4 (`4.1.0`) POMs are checked.
- Plugins (build, `pluginManagement`, reporting), extensions, `.mvn/extensions.xml`, `<parent>`, and artifacts resolved from plugin configuration (annotation processor paths, javadoc doclets and taglets) are checked with the same rules as dependencies.
- A POM with a `DOCTYPE` is reported and not parsed, so no entity can be declared or expanded.
- Files in any encoding Python can decode are read, deep nesting is handled, and a `pom.xml` whose root isn't `<project>` is reported.

**Gradle**
- `-SNAPSHOT` versions, `platform(...)` / `enforcedPlatform(...)` / `testFixtures(...)` wrappers, Kotlin named arguments, and one-line `dependencies { ... }` blocks are checked.
- Scripts pulled in with `apply from:` (`*.gradle`, `*.gradle.kts`) and version catalogs (`*.versions.toml`) are scanned.
- More configurations are recognised, including `classpath` and `ksp`.
- Map-notation parsing is linear: a ~940 KB line takes 0.04 s instead of minutes.

**Helm**
- `Chart.yaml` dependency versions must be exact. Ranges (`17.x.x`, `^1.2.0`, `~1.2`, `>=1.0`, `1.2`) are reported, except for charts from this repository (`file://` or vendored in `charts/`).
- `Chart.lock` must record exact versions matching `Chart.yaml`.
- A flow-style dependency list is reported instead of being read as empty.
- The `Chart.lock` digest is reported as drift detection, not content integrity, because Helm computes it over the dependency list rather than the charts.

**Cargo**
- A `Cargo.toml` with dependencies and no `Cargo.lock` in its own or a parent directory is reported.

**Terraform**
- A root module (one that declares `required_providers` or configures a provider, backend or cloud block) without `.terraform.lock.hcl` is reported. Modules called from elsewhere in the repository through a local `source` are exempt.

### Output

- Scan-limit findings use the checker name `pinstack` and have SARIF security severity 7.0.
- Helm findings are no longer marked as integrity findings, so SARIF no longer attaches the integrity note to them.

### Development

- Developing pinstack needs Python 3.11 or later, because a build-tool security fix requires it. The package itself still supports Python 3.9 and later.
- Build tooling is refreshed and clear of known advisories, and the ruff rule selection is pinned so a ruff upgrade cannot change the lint gate.
