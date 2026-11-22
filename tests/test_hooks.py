"""Integration tests exercise the Git index, not a mock of the scanner."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "check_staged.py"
QUICK = ROOT / "scripts" / "check_repo.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def scan(repo: Path, maximum: int = 1024 * 1024) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--max-bytes", str(maximum)],
        cwd=repo,
        text=True,
        capture_output=True,
    )


def stage(repo: Path, filename: str, data: bytes) -> None:
    (repo / filename).write_bytes(data)
    subprocess.run(["git", "add", "--", filename], cwd=repo, check=True)


def test_index_secret_is_blocked_even_if_worktree_is_clean(repo: Path) -> None:
    token = b"gh" + b"p_" + b"x" * 36
    stage(repo, "settings.txt", b"GITHUB_TOKEN=" + token + b"\n")
    (repo / "settings.txt").write_text("clean worktree\n", encoding="utf-8")
    result = scan(repo)
    assert result.returncode == 1
    assert "BLOCK_SECRET" in result.stdout
    assert token.decode() not in result.stdout


def test_size_boundary_and_next_byte(repo: Path) -> None:
    stage(repo, "data.bin", b"\0" * 1024)
    assert scan(repo, 1024).returncode == 0
    stage(repo, "data.bin", b"\0" * 1025)
    result = scan(repo, 1024)
    assert result.returncode == 1
    assert "BLOCK_SIZE" in result.stdout


def test_normal_python_is_allowed(repo: Path) -> None:
    stage(repo, "normal.py", b"def square(x):\n    return x * x\n")
    assert scan(repo).returncode == 0


def test_quick_checks_reject_invalid_python_and_yaml(tmp_path: Path) -> None:
    python_file = tmp_path / "broken.py"
    yaml_file = tmp_path / "broken.yaml"
    python_file.write_text("def missing_colon()\n    pass\n", encoding="utf-8")
    yaml_file.write_text("items: [unclosed\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(QUICK), str(python_file), str(yaml_file)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "invalid .py" in result.stdout
    assert "invalid .yaml" in result.stdout
