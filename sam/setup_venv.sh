#!/usr/bin/env bash
# Create sam/.venv with a SAM-compatible Python (3.10.16–3.13.x).
# macOS default `python3` is often 3.14+, which cannot install solace-agent-mesh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PY=""
for candidate in python3.13 python3.12 python3.11 python3.10; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver="$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${ver%%.*}"
    minor="${ver#*.}"
    if [[ "$major" -eq 3 ]] && [[ "$minor" -ge 10 ]] && [[ "$minor" -le 13 ]]; then
      PY="$candidate"
      break
    fi
  fi
done

if [[ -z "$PY" ]]; then
  echo "No compatible Python found (need 3.10–3.13). Install e.g.:" >&2
  echo "  brew install python@3.13" >&2
  exit 1
fi

echo "Using $($PY --version) at $(command -v "$PY")"
if [[ -d .venv ]] && [[ "${1:-}" != "--keep" ]]; then
  echo "Removing existing .venv (use --keep to skip delete)..."
  rm -rf .venv
fi
"$PY" -m venv .venv
.venv/bin/pip install -U pip wheel
.venv/bin/pip install -r requirements.txt paho-mqtt

# shellcheck source=resolve_venv.sh
source "${ROOT}/resolve_venv.sh"
if ! resolve_demo_venv "$ROOT"; then
  echo "ERROR: solace-agent-mesh did not install correctly (no sam CLI)." >&2
  exit 1
fi

echo ""
echo "SAM CLI ready (${DEMO_VENV_SAM})."
echo ""
echo "Next:"
echo "  source .venv/bin/activate"
echo "  cp .env.example .env    # or copy your existing .env"
echo "  ./start_demo_stack.sh --fresh"
