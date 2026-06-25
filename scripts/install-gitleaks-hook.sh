#!/usr/bin/env bash
# Install a local pre-commit hook that scans staged changes with gitleaks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$ROOT/.git/hooks/pre-commit"

GITLEAKS_BIN="${GITLEAKS:-}"
if [ -z "$GITLEAKS_BIN" ]; then
  if command -v gitleaks >/dev/null 2>&1; then
    GITLEAKS_BIN="$(command -v gitleaks)"
  elif [ -x /opt/homebrew/bin/gitleaks ]; then
    GITLEAKS_BIN=/opt/homebrew/bin/gitleaks
  elif [ -x /usr/local/bin/gitleaks ]; then
    GITLEAKS_BIN=/usr/local/bin/gitleaks
  else
    echo "gitleaks not found. Install with: brew install gitleaks" >&2
    exit 1
  fi
fi

cat > "$HOOK" <<EOF
#!/bin/sh
exec "$GITLEAKS_BIN" protect --staged --redact --config .gitleaks.toml --verbose
EOF
chmod +x "$HOOK"

if command -v pre-commit >/dev/null 2>&1 && [ -f .pre-commit-config.yaml ]; then
  pre-commit install
  echo "Installed native git hook and pre-commit framework hooks."
else
  echo "Installed native git pre-commit hook (gitleaks protect --staged)."
  echo "Optional: brew install pre-commit && pre-commit install"
fi
