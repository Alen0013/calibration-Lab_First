#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3.12 -m venv .venv
  .venv/bin/python -m pip install -e '.[dev]'
fi
export PATH="$PWD/.venv/bin:$PATH"
exec .venv/bin/python -m calibration_lab.webapp

