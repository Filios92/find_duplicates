#!/usr/bin/env python3
"""Find files with identical contents beneath a directory."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def find_duplicates(root: Path) -> list[list[Path]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    for current, dirs, files in os.walk(root, followlinks=False):
        # Do not descend into symlinked directories.
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
        for name in files:
            path = Path(current) / name
            try:
                if path.is_file():
                    by_size[path.stat().st_size].append(path)
            except OSError as error:
                print(f"Warning: cannot inspect {path}: {error}", file=sys.stderr)

    groups: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for path in paths:
            try:
                groups[(size, digest(path))].append(path)
            except OSError as error:
                print(f"Warning: cannot read {path}: {error}", file=sys.stderr)

    return [sorted(paths) for paths in groups.values() if len(paths) > 1]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find files with identical contents, recursively. Files are only reported; none are changed."
    )
    parser.add_argument("directory", type=Path, help="directory to scan")
    args = parser.parse_args()

    root = args.directory.expanduser()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    groups = find_duplicates(root.resolve())
    if not groups:
        print("No duplicate files found.")
        return 0

    for number, paths in enumerate(sorted(groups, key=lambda group: str(group[0])), 1):
        print(f"Duplicate group {number}:")
        for path in paths:
            print(f"  {path}")
    print(f"\nFound {len(groups)} duplicate group(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
