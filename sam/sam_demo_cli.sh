#!/usr/bin/env bash
# Invoke SAM CLI from this project's venv (sam binary or python -m fallback).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
# shellcheck source=resolve_venv.sh
source "${ROOT}/resolve_venv.sh"
if ! resolve_demo_venv "$ROOT"; then
  echo "SAM not installed in this project's venv. Run: ./setup_venv.sh" >&2
  exit 1
fi
sam_cli_nohup "$@"
