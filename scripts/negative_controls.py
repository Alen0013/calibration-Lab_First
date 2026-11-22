"""Prove three real Git commits are blocked in a disposable repository."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )


def execute(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    configuration = ROOT / ".pre-commit-config.yaml"
    shutil.copyfile(configuration, output / "precommit_config.yaml")
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PRE_COMMIT_COLOR"] = "never"
    env.pop("SKIP", None)
    cases = [
        ("style", "bad_style.py", b"import os\nx=  1\n", "Ruff lint"),
        (
            "secret",
            "fake_credential.txt",
            b"GITHUB_TOKEN=" + b"gh" + b"p_" + b"A1b2C3d4E5f6G7h8J9k0L1m2N3o4P5q6R7s8" + b"\n",
            "BLOCK_SECRET",
        ),
        ("oversize", "oversized_fixture.bin", b"\0" * (1024 * 1024 + 1), "BLOCK_SIZE"),
    ]
    results = []
    log = [
        "LAB1 variant 25: actual negative Git commit controls",
        f"UTC: {datetime.now(timezone.utc).isoformat()}",
        "Method: disposable repository, real installed pre-commit hook, real git commit.",
        "Credentials in this test are synthetic. No credential values are printed.",
        "Successful control = nonzero commit exit + unchanged HEAD + expected blocker.",
    ]
    with tempfile.TemporaryDirectory(prefix="lab1_git_controls_") as directory:
        repo = Path(directory)
        (repo / "scripts").mkdir()
        shutil.copyfile(configuration, repo / ".pre-commit-config.yaml")
        for name in ["check_staged.py", "check_repo.py"]:
            shutil.copyfile(ROOT / "scripts" / name, repo / "scripts" / name)
        # Preserve lint settings without installing or copying the project package.
        if (ROOT / "pyproject.toml").exists():
            shutil.copyfile(ROOT / "pyproject.toml", repo / "pyproject.toml")
        commands = [
            ["git", "init", "-b", "main"],
            ["git", "config", "user.name", "LAB1 isolated controls"],
            ["git", "config", "user.email", "lab1-controls@example.invalid"],
            ["git", "config", "commit.gpgsign", "false"],
            ["git", "add", "."],
            ["git", "commit", "-m", "chore: initialize isolated control fixture"],
            [sys.executable, "-m", "pre_commit", "install", "--install-hooks"],
        ]
        for args in commands:
            completed = command(args, repo, env)
            if completed.returncode:
                raise RuntimeError(f"Setup command failed: {args[:3]}\n{completed.stdout}")
        baseline = command(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()
        for name, filename, blob, marker in cases:
            (repo / filename).write_bytes(blob)
            added = command(["git", "add", "--", filename], repo, env)
            if added.returncode:
                raise RuntimeError(added.stdout)
            attempted = command(["git", "commit", "-m", f"test: reject {name} fixture"], repo, env)
            after = command(["git", "rev-parse", "HEAD"], repo, env).stdout.strip()
            blocked = attempted.returncode != 0 and after == baseline and marker in attempted.stdout
            item = {
                "case": name,
                "commit_exit_code": attempted.returncode,
                "head_unchanged": after == baseline,
                "expected_marker": marker,
                "marker_observed": marker in attempted.stdout,
                "blocked": blocked,
            }
            results.append(item)
            log.extend(
                [
                    "",
                    f"=== {name.upper()} ===",
                    f"$ git add -- {filename}",
                    f"$ git commit -m 'test: reject {name} fixture'",
                    attempted.stdout.replace(str(repo), "<temporary_repository>").rstrip(),
                    f"commit_exit_code={attempted.returncode}",
                    f"head_unchanged={after == baseline}",
                    f"control_passed={blocked}",
                ]
            )
            reset = command(["git", "reset", "--hard", baseline], repo, env)
            if reset.returncode:
                raise RuntimeError(reset.stdout)
    summary = {"all_blocked": all(item["blocked"] for item in results), "cases": results}
    log.extend(["", "RESULT: " + ("PASS" if summary["all_blocked"] else "FAIL")])
    (output / "precommit_block.log").write_text("\n".join(log) + "\n", encoding="utf-8")
    (output / "precommit_controls.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "LAB1")
    args = parser.parse_args()
    try:
        result = execute(args.output)
    except (OSError, RuntimeError) as exc:
        print(f"NEGATIVE_CONTROLS_FAILED: {exc}")
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["all_blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
