#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3.12 -m venv .venv
fi
if [ ! -f .venv/lab1_installed.ok ]; then
  .venv/bin/python -m pip install -e '.[dev]'
  touch .venv/lab1_installed.ok
fi
export PATH="$PWD/.venv/bin:$PATH"
exec .venv/bin/python -m calibration_lab.webapp

