#!/bin/bash
# Parse local input with an isolated Python interpreter; never source config files.
set -eu
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)"
if [ -x /usr/bin/python3 ]; then
  PYTHON=/usr/bin/python3
else
  PYTHON="$(command -v python3)" || { printf '%s\n' 'Python 3.9+ is required.' >&2; exit 2; }
fi
exec "$PYTHON" -I "$ROOT/scripts/doctor.py" "$@"
