#!/usr/bin/env bash
cd "$(dirname "$0")"
export PYTHONUTF8=1
PY="${PYTHON:-python3}"
if ! "$PY" -c "import PySide6.QtWidgets" 2>/dev/null; then
  if ! "$PY" -m pip install -r requirements-pc.txt; then
    "$PY" -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install -r requirements-pc.txt
    PY=".venv/bin/python"
  fi
fi
exec "$PY" -m folio.desk
