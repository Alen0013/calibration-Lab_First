"""Fast, offline validation of Python, YAML, JSON and merge-conflict markers."""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

import yaml

CONFLICT = re.compile(r"^(?:<{7} |={7}$|>{7} )", re.MULTILINE)


def inspect(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (UnicodeDecodeError, OSError):
        return []
    errors = []
    if CONFLICT.search(text):
        errors.append(f"{path}: unresolved merge-conflict marker")
    try:
        if path.suffix == ".py":
            ast.parse(text, filename=str(path))
        elif path.suffix == ".json":
            json.loads(text)
        elif path.suffix in {".yaml", ".yml"}:
            yaml.safe_load(text)
    except (SyntaxError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"{path}: invalid {path.suffix} ({type(exc).__name__})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    errors = [message for name in args.paths for message in inspect(Path(name))]
    if errors:
        print("\n".join(errors))
        return 1
    print(f"OK: quick checks passed for {len(args.paths)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
