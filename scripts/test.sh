#!/bin/bash
# Entire default suite is offline and uses synthetic inputs only.
set -eu
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$ROOT"
PYTHON="$(command -v python3)" || exit 2
bash -n bin/ccr-doctor.sh scripts/test.sh
node --check plugins/tool-result-compat/index.mjs
"$PYTHON" -I -c 'import ast; from pathlib import Path; [ast.parse(p.read_text(), filename=str(p)) for d in ("scripts", "tests") for p in Path(d).glob("*.py")]; print("Python syntax: PASS")'
node --test --test-reporter=tap tests/plugin.test.mjs
# Includes temporary local Git repositories; no fetch, remote helper, or server.
# Readiness regressions mock sandbox checks; never run the real-host probe or CCR.
for suite in tests/test_doctor.py tests/test_public_content.py tests/test_real_host_runner.py; do
  "$PYTHON" -I "$suite"
done
"$PYTHON" -I scripts/check_public_content.py
