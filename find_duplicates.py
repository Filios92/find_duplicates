#!/usr/bin/env python3
"""Find duplicate files and optionally remove selected copies."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def scan(root: Path) -> tuple[list[list[Path]], dict[Path, int]]:
    sizes: dict[int, list[Path]] = defaultdict(list)
    counts: dict[Path, int] = defaultdict(int)
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(current) / name).is_symlink()]
        for name in files:
            path = Path(current) / name
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                sizes[path.stat().st_size].append(path)
                parent = path.parent
                while parent != root:
                    counts[parent] += 1
                    parent = parent.parent
            except OSError as error:
                print(f"Warning: cannot inspect {path}: {error}", file=sys.stderr)
    hashes: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for size, paths in sizes.items():
        if len(paths) < 2:
            continue
        for path in paths:
            try:
                hashes[(size, digest(path))].append(path)
            except OSError as error:
                print(f"Warning: cannot read {path}: {error}", file=sys.stderr)
    groups = [sorted(paths) for paths in hashes.values() if len(paths) > 1]
    return sorted(groups, key=lambda paths: str(paths[0])), counts


def show_groups(groups: list[list[Path]], root: Path, pretty: bool) -> None:
    for number, paths in enumerate(groups, 1):
        print(f"Duplicate group {number} ({len(paths)} files):")
        if not pretty:
            for path in paths:
                print(f"  {path}")
            continue
        tree: dict = {}
        for path in paths:
            node = tree
            for part in path.relative_to(root).parts:
                node = node.setdefault(part, {})

        def render(node: dict, indent: str = "  ") -> None:
            for name, child in sorted(node.items()):
                label = name
                while len(child) == 1:
                    name, child = next(iter(child.items()))
                    label += "/" + name
                print(f"{indent}{label}{'/' if child else ''}")
                render(child, indent + "  ")

        render(tree)
    print(f"\nFound {len(groups)} duplicate group(s).")


def show_directories(groups: list[list[Path]], counts: dict[Path, int], root: Path) -> None:
    shared: dict[tuple[Path, Path], int] = defaultdict(int)
    for paths in groups:
        directories: set[Path] = set()
        for path in paths:
            parent = path.parent
            while parent != root:
                directories.add(parent)
                parent = parent.parent
        for left, right in combinations(sorted(directories), 2):
            if left not in right.parents and right not in left.parents:
                shared[(left, right)] += 1
    if not shared:
        print("No directory pairs share duplicate files.")
        return
    print("Directory pairs (shared duplicate content groups / total files below each):")
    ranked = sorted(shared.items(), key=lambda item: (-item[1], str(item[0])))
    for (left, right), common in ranked[:20]:
        print(f"  {left.relative_to(root)}/  <->  {right.relative_to(root)}/")
        left_count, right_count = counts[left], counts[right]
        print(f"    {common} shared; {left_count} file(s) vs {right_count} file(s)")
    if len(ranked) > 20:
        print(f"  ... {len(ranked) - 20} more pair(s)")


def delete_copy(path: Path, keep: Path) -> bool:
    try:
        if path.is_symlink() or keep.is_symlink() or not path.is_file() or not keep.is_file():
            raise OSError("file disappeared or became a symlink")
        if path.stat().st_size != keep.stat().st_size or digest(path) != digest(keep):
            raise OSError("files no longer have identical contents")
        path.unlink()
    except OSError as error:
        print(f"Cannot delete {path}: {error}", file=sys.stderr)
        return False
    print(f"Deleted: {path}\nKept:    {keep}")
    return True


def select_copies(groups: list[list[Path]], root: Path) -> int:
    if shutil.which("fzf") is None:
        print("fzf is required for --select.", file=sys.stderr)
        return 2
    choices: dict[int, tuple[int, Path]] = {}
    lines = []
    for group_number, paths in enumerate(groups, 1):
        for path in paths:
            index = len(choices) + 1
            choices[index] = (group_number, path)
            label = str(path.relative_to(root)).replace("\\", "\\\\").replace("\n", "\\n").replace("\t", "\\t")
            lines.append(f"{index}\tgroup {group_number}\t{label}")
    result = subprocess.run(
        ["fzf", "--multi", "--read0", "--print0", "--delimiter=\t", "--with-nth=2..",
         "--header=Tab: select files; Enter: review deletion"],
        input=("\0".join(lines) + "\0").encode(), stdout=subprocess.PIPE, check=False,
    )
    if result.returncode == 1 or not result.stdout:
        return 0
    if result.returncode != 0:
        print("fzf failed.", file=sys.stderr)
        return 2
    selected = {choices[int(line.split(b"\t", 1)[0])] for line in result.stdout.split(b"\0") if line}
    for group_number, paths in enumerate(groups, 1):
        if all((group_number, path) in selected for path in paths):
            print(f"Cannot delete every file in group {group_number}; selection cancelled.", file=sys.stderr)
            return 2
    print("Selected for deletion:")
    for _, path in sorted(selected):
        print(f"  {path}")
    try:
        if input("Type DELETE to confirm: ") != "DELETE":
            print("Cancelled.")
            return 0
    except EOFError:
        return 2
    failures = 0
    for group_number, path in sorted(selected):
        keep = next(other for other in groups[group_number - 1] if (group_number, other) not in selected)
        failures += not delete_copy(path, keep)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="directory to scan recursively")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--pretty", action="store_true", help="show duplicate paths as compact trees")
    modes.add_argument("--dirs", action="store_true", help="show directory pairs sharing duplicate files")
    modes.add_argument("--select", action="store_true", help="select files to delete with fzf")
    modes.add_argument("--delete-one", action="store_true", help="delete one duplicate file and exit")
    args = parser.parse_args()
    root = args.directory.expanduser()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    root = root.resolve()
    groups, counts = scan(root)
    if not groups:
        print("No duplicate files found.")
        return 0
    if args.dirs:
        show_directories(groups, counts, root)
    elif args.select:
        return select_copies(groups, root)
    elif args.delete_one:
        return 0 if delete_copy(groups[0][-1], groups[0][0]) else 1
    else:
        show_groups(groups, root, args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
