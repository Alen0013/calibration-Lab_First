"""Restore every local branch from the delivered bundle without overwriting files."""

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "calibration_lab1.bundle")
    parser.add_argument("--destination", type=Path, default=ROOT.parent / "calibration-lab1-git")
    args = parser.parse_args()
    bundle, target = args.bundle.resolve(), args.destination.resolve()
    if not bundle.is_file():
        parser.error(f"Не найден Git bundle: {bundle}")
    if target.exists():
        parser.error(f"Каталог уже существует, перезапись запрещена: {target}")
    subprocess.run(["git", "clone", "--branch", "develop", str(bundle), str(target)], check=True)
    refs = subprocess.check_output(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"],
        cwd=target,
        text=True,
    ).splitlines()
    for ref in refs:
        name = ref.removeprefix("origin/")
        if name not in {"HEAD", "develop", "origin"}:
            subprocess.run(["git", "branch", name, ref], cwd=target, check=True)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=target, check=True)
    print(f"История и ветки восстановлены: {target}")
    print("Добавьте адрес GitHub в качестве remote origin по docs/git_workflow.md")


if __name__ == "__main__":
    main()
