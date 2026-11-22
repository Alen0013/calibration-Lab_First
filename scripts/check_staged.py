"""Reject credential patterns and oversized blobs in the Git index.

The index is inspected instead of the worktree so an unstaged cleanup cannot
hide a secret that would still be included in a commit. No secret value is ever
printed. This small teaching scanner is intentionally not an exhaustive DLP
system; it covers explicit credentials, common token formats and private keys.
"""

from __future__ import annotations

import argparse
import re
import subprocess

MAX_BYTES = 1024 * 1024
PATTERNS = (
    ("GitHub token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("GitHub fine-grained token", re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    (
        "private key",
        re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    ),
    (
        "credential assignment",
        re.compile(
            rb"(?im)^\s*(?:export\s+)?(?:[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|"
            rb"AUTH_TOKEN|SECRET_KEY|PASSWORD|GITHUB_TOKEN))\s*[:=]\s*"
            rb"[\"']?([A-Za-z0-9_+/=.-]{20,})[\"']?\s*$"
        ),
    ),
)
PLACEHOLDER_WORDS = (b"example", b"placeholder", b"your_", b"replace_me")


def run_git(*args: str) -> bytes:
    """Read Git output without shell parsing or displaying file contents."""
    return subprocess.check_output(["git", *args], stderr=subprocess.PIPE)


def indexed_paths() -> list[str]:
    """Inspect all indexed files, including an unchanged tracked credential."""
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in run_git("ls-files", "--cached", "-z").split(b"\0")
        if item
    ]


def secret_reasons(blob: bytes) -> list[str]:
    reasons = []
    for label, pattern in PATTERNS:
        for match in pattern.finditer(blob):
            if label == "credential assignment" and any(
                word in match.group(1).lower() for word in PLACEHOLDER_WORDS
            ):
                continue
            reasons.append(label)
            break
    return reasons


def check_index(max_bytes: int = MAX_BYTES) -> list[str]:
    errors: list[str] = []
    for filename in indexed_paths():
        # Asking Git for the blob avoids following symlinks outside the repo.
        blob = run_git("show", f":{filename}")
        if len(blob) > max_bytes:
            errors.append(
                f"BLOCK_SIZE {filename}: {len(blob)} bytes exceeds {max_bytes} bytes. "
                "Store large artifacts outside Git."
            )
        for reason in secret_reasons(blob):
            errors.append(
                f"BLOCK_SECRET {filename}: {reason} detected; value redacted. "
                "Remove credentials from the staged file."
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-bytes", type=int, default=MAX_BYTES)
    args = parser.parse_args()
    if args.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    try:
        errors = check_index(args.max_bytes)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"CHECK_FAILED: cannot read the Git index ({type(exc).__name__}).")
        return 2
    if errors:
        print("\n".join(errors))
        return 1
    print(f"OK: indexed files have no matched credentials and are <= {args.max_bytes} bytes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
