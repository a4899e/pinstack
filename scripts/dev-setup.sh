#!/bin/sh
# Dev environment setup for pinstack.
#
# Creates a .venv, installs runtime + build-chain deps (hash-verified from
# uv.lock), and configures git hooks.
#
# Usage: ./scripts/dev-setup.sh

set -e

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found. Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "Installing runtime + build-chain deps (hash-verified from uv.lock)..."
uv sync --locked --group build

echo "Configuring git hooks..."
git config core.hooksPath .githooks

echo "Done. Run tasks with: uv run inv build"
