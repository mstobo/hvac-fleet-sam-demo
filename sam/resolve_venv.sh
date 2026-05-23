#!/usr/bin/env bash
# Shared venv + SAM CLI resolution for demo scripts.
# Source from sam/:  source "$(dirname "$0")/resolve_venv.sh" && resolve_demo_venv

resolve_demo_venv() {
  local root="${1:-$(pwd)}"
  DEMO_VENV_PY=""
  DEMO_VENV_SAM=""
  DEMO_VENV_ROOT=""

  # Prefer this repo's .venv when present (avoids mqtt5SRDemo venv mismatch).
  if [[ -x "${root}/.venv/bin/python" ]]; then
    DEMO_VENV_ROOT="${root}/.venv"
    DEMO_VENV_PY="${root}/.venv/bin/python"
  elif [[ -n "${VIRTUAL_ENV:-}" ]] && [[ -x "${VIRTUAL_ENV}/bin/python" ]]; then
    DEMO_VENV_ROOT="${VIRTUAL_ENV}"
    DEMO_VENV_PY="${VIRTUAL_ENV}/bin/python"
  fi

  if [[ -z "$DEMO_VENV_PY" ]]; then
    return 1
  fi

  local expected="${root}/.venv"
  if [[ -n "${VIRTUAL_ENV:-}" ]] && [[ "$(cd "$VIRTUAL_ENV" 2>/dev/null && pwd)" != "$(cd "$expected" 2>/dev/null && pwd)" ]]; then
    echo "Note: active venv is not ${expected} — using VIRTUAL_ENV=${VIRTUAL_ENV}" >&2
  fi

  for candidate in \
    "${DEMO_VENV_ROOT}/bin/sam" \
    "${DEMO_VENV_ROOT}/bin/solace-agent-mesh"; do
    if [[ -x "$candidate" ]]; then
      DEMO_VENV_SAM="$candidate"
      return 0
    fi
  done

  if "$DEMO_VENV_PY" -c "import solace_agent_mesh.cli.main" >/dev/null 2>&1; then
    DEMO_VENV_SAM="__PY_MODULE__"
    return 0
  fi

  return 1
}

sam_cli_exec() {
  # Usage: sam_cli_exec run configs/agents/foo.yaml ...
  if [[ "${DEMO_VENV_SAM}" == "__PY_MODULE__" ]]; then
    exec "$DEMO_VENV_PY" -m solace_agent_mesh.cli.main "$@"
  fi
  exec "$DEMO_VENV_SAM" "$@"
}

sam_cli_nohup() {
  # Usage: sam_cli_nohup >> log 2>&1 &  — caller sets redirection before &
  if [[ "${DEMO_VENV_SAM}" == "__PY_MODULE__" ]]; then
    "$DEMO_VENV_PY" -m solace_agent_mesh.cli.main "$@"
  else
    "$DEMO_VENV_SAM" "$@"
  fi
}
