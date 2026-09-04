#!/usr/bin/env python3
"""Fail if generated, backup, or secret-like artifacts are tracked by git."""

import subprocess
import sys
from pathlib import Path
from pathlib import PurePosixPath


BLOCKED_SUFFIXES = (
    ".env",
    ".log",
    ".pyc",
    ".quickstock-deploy.zip",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".subscription-backup",
)

BLOCKED_PARTS = {
    "__pycache__",
    ".venv",
    "build",
    "dist",
    "media",
    "staticfiles",
}


def git_ls_files():
    result = subprocess.run(
        ["git", "ls-files"],
        text=True,
        capture_output=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_blocked(path):
    posix_path = PurePosixPath(path)
    parts = set(posix_path.parts)
    lowered = path.lower()
    return bool(parts & BLOCKED_PARTS) or lowered.endswith(BLOCKED_SUFFIXES)


def main():
    blocked = sorted(
        path
        for path in git_ls_files()
        if Path(path).exists() and is_blocked(path)
    )
    if not blocked:
        print("No generated or secret-like artifacts are tracked.")
        return 0

    print("Tracked generated/secret-like artifacts found:")
    for path in blocked:
        print(f"  - {path}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
