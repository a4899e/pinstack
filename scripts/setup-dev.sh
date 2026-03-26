#!/usr/bin/env bash
# Set up the development environment for pinstack.
# Usage: ./scripts/setup-dev.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "Creating venv..."
python3 -m venv .venv
source .venv/bin/activate

echo "Installing pinstack with dev dependencies..."
pip install -e ".[dev]"

echo "Configuring git hooks..."
git config core.hooksPath .githooks

echo ""
echo "Done. Activate the venv with:"
echo "  source .venv/bin/activate"
