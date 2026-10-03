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
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KiB", "MiB", "GiB", "TiB", "PiB"):
        value /= 1024
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}".replace(".0 ", " ")
    raise AssertionError("unreachable")


def paint(text: str, style: str, enabled: bool) -> str:
    return f"\033[{style}m{text}\033[0m" if enabled else text


def scan(root: Path) -> tuple[
    list[list[Path]], dict[Path, int], dict[Path, int], set[Path], set[Path],
]:
    sizes: dict[int, list[Path]] = defaultdict(list)
    counts: dict[Path, int] = defaultdict(int)
    file_sizes: dict[Path, int] = {}
    directories = {root}
    unsafe = set()

    def walk_error(error: OSError) -> None:
        if error.filename:
            unsafe.add(Path(error.filename))
        print(f"Warning: cannot scan directory: {error}", file=sys.stderr)

    for current, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        parent_dir = Path(current)
        for name in dirs[:]:
            path = parent_dir / name
            if path.is_symlink():
                unsafe.add(parent_dir)
                dirs.remove(name)
            else:
                directories.add(path)
        for name in files:
            path = Path(current) / name
            try:
                if path.is_symlink() or not path.is_file():
                    unsafe.add(parent_dir)
                    continue
                size = path.stat().st_size
                sizes[size].append(path)
                file_sizes[path] = size
                parent = path.parent
                while parent != root:
                    counts[parent] += 1
                    parent = parent.parent
            except OSError as error:
                unsafe.add(parent_dir)
                print(f"Warning: cannot inspect {path}: {error}", file=sys.stderr)
    hashes: dict[tuple[int, str], list[Path]] = defaultdict(list)
    for size, paths in sizes.items():
        if len(paths) < 2:
            continue
        for path in paths:
            try:
                hashes[(size, digest(path))].append(path)
            except OSError as error:
                unsafe.add(path.parent)
                print(f"Warning: cannot read {path}: {error}", file=sys.stderr)
    groups = [sorted(paths) for paths in hashes.values() if len(paths) > 1]
    return sorted(groups, key=lambda paths: str(paths[0])), counts, file_sizes, directories, unsafe


def show_groups(
    groups: list[list[Path]], root: Path, pretty: bool,
    file_sizes: dict[Path, int], color: bool,
    keepers: dict[int, Path] | None = None,
    removed_dirs: set[Path] | None = None,
    hidden_groups: set[int] | None = None,
) -> None:
    shown = 0
    for number, paths in enumerate(groups, 1):
        if hidden_groups and number in hidden_groups:
            continue
        shown += 1
        heading = f"Duplicate group {number} ({len(paths)} files, {human_size(file_sizes[paths[0]])} each):"
        print(paint(heading, "1;35", color))
        if not pretty:
            for path in paths:
                print(f"  {path}{file_action(path, number, keepers, removed_dirs, color)}")
            continue
        tree: dict = {}
        for path in paths:
            node = tree
            parts = path.relative_to(root).parts
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = path

        def render(node: dict, prefix: str = "") -> None:
            entries = sorted(node.items())
            for index, (name, child) in enumerate(entries):
                label = name
                while isinstance(child, dict) and len(child) == 1:
                    name, child = next(iter(child.items()))
                    label += "/" + name
                last = index == len(entries) - 1
                branch = paint("└── " if last else "├── ", "90", color)
                if isinstance(child, dict):
                    print(f"{prefix}{branch}{paint(label + '/', '1;34', color)}")
                    render(child, prefix + ("    " if last else "│   "))
                else:
                    filename = paint(label, "1;32", color)
                    action = file_action(child, number, keepers, removed_dirs, color)
                    print(f"{prefix}{branch}{filename}{action}")

        render(tree)
    if shown:
        print("\n" + paint(f"Found {shown} individual duplicate group(s).", "1;35", color))


def file_action(
    path: Path, group_number: int, keepers: dict[int, Path] | None,
    removed_dirs: set[Path] | None, color: bool,
) -> str:
    if keepers is None:
        return ""
    if path == keepers[group_number]:
        return " " + paint("[KEEP]", "1;32", color)
    if removed_dirs and any(directory in path.parents for directory in removed_dirs):
        return " " + paint("[REMOVE WITH DIRECTORY]", "1;31", color)
    return " " + paint("[REMOVE]", "1;31", color)


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


@dataclass(frozen=True)
class DirectoryRemoval:
    target: Path
    keep: Path
    manifest: tuple[tuple[str, ...], tuple[tuple[str, int, str], ...]]


@dataclass(frozen=True)
class DirectoryGroup:
    directories: tuple[Path, ...]
    manifest: tuple[tuple[str, ...], tuple[tuple[str, int, str], ...]]
    file_groups: frozenset[int]
    hideable_groups: frozenset[int]


def directory_manifest(root: Path) -> tuple[tuple[str, ...], tuple[tuple[str, int, str], ...]]:
    """Include names, empty directories, sizes, and content in an exact tree comparison."""
    if root.is_symlink() or not root.is_dir():
        raise OSError(f"directory disappeared or became a symlink: {root}")
    directory_names = []
    files = []

    def walk_error(error: OSError) -> None:
        raise error

    for current, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        parent = Path(current)
        directory_names.append(str(parent.relative_to(root)))
        for name in dirs:
            if (parent / name).is_symlink():
                raise OSError(f"symlink in directory: {parent / name}")
        for name in names:
            path = parent / name
            if path.is_symlink() or not path.is_file():
                raise OSError(f"unsupported entry in directory: {path}")
            files.append((str(path.relative_to(root)), path.stat().st_size, digest(path)))
    return tuple(sorted(directory_names)), tuple(sorted(files))


def exact_directory_groups(
    root: Path, groups: list[list[Path]], file_sizes: dict[Path, int],
    directories: set[Path], unsafe: set[Path],
) -> list[DirectoryGroup]:
    group_of = {path: number for number, paths in enumerate(groups, 1) for path in paths}
    child_dirs: dict[Path, list[Path]] = defaultdict(list)
    child_files: dict[Path, list[Path]] = defaultdict(list)
    for directory in directories - {root}:
        child_dirs[directory.parent].append(directory)
    for path in file_sizes:
        child_files[path.parent].append(path)

    signatures: dict[Path, tuple] = {}
    subtree_files: dict[Path, list[Path]] = {}
    for directory in sorted(directories, key=lambda path: (-len(path.parts), str(path))):
        files = child_files[directory][:]
        for child in child_dirs[directory]:
            files.extend(subtree_files[child])
        subtree_files[directory] = files
        if directory in unsafe:
            signatures[directory] = ("unsafe", str(directory))
        else:
            file_entries = tuple(sorted(
                (path.name, ("group", group_of[path]) if path in group_of else ("unique", str(path)))
                for path in child_files[directory]
            ))
            dir_entries = tuple(sorted((child.name, signatures[child]) for child in child_dirs[directory]))
            signatures[directory] = (file_entries, dir_entries)

    matching: dict[tuple, list[Path]] = defaultdict(list)
    for directory in directories - {root}:
        if subtree_files[directory]:
            matching[signatures[directory]].append(directory)

    candidates: list[tuple[Path, ...]] = []
    for siblings in matching.values():
        if len(siblings) >= 2:
            candidates.append(tuple(sorted(siblings)))
    candidates.sort(key=lambda siblings: (min(len(path.parts) for path in siblings), str(siblings[0])))

    result: list[DirectoryGroup] = []
    for siblings in candidates:
        if any(
            left == right or left in right.parents or right in left.parents
            for previous in result for left in siblings for right in previous.directories
        ):
            continue
        manifests: dict[tuple, list[Path]] = defaultdict(list)
        for directory in siblings:
            try:
                manifests[directory_manifest(directory)].append(directory)
            except OSError as error:
                print(f"Warning: cannot compare directory {directory}: {error}", file=sys.stderr)
        for manifest, matched in manifests.items():
            if len(matched) < 2:
                continue
            all_paths = {path for directory in matched for path in subtree_files[directory]}
            if any(path not in group_of for path in all_paths):
                continue
            file_groups = frozenset(group_of[path] for path in all_paths)
            if any(any(sum(directory in path.parents for path in groups[number - 1]) != 1
                       for directory in matched) for number in file_groups):
                continue
            hideable = frozenset(number for number in file_groups
                                 if set(groups[number - 1]) <= all_paths)
            result.append(DirectoryGroup(tuple(sorted(matched)), manifest, file_groups, hideable))
    return sorted(result, key=lambda group: str(group.directories[0]))


def plan_directories(
    directory_groups: list[DirectoryGroup], keepers: dict[int, Path],
) -> list[DirectoryRemoval]:
    removals = []
    for directory_group in directory_groups:
        retained = [directory for directory in directory_group.directories if all(
            directory in keepers[number].parents for number in directory_group.file_groups
        )]
        if not retained:
            continue
        keep = retained[0]
        for target in directory_group.directories:
            if target != keep and all(
                target not in keepers[number].parents for number in directory_group.file_groups
            ):
                removals.append(DirectoryRemoval(target, keep, directory_group.manifest))
    return sorted(removals, key=lambda item: str(item.target))


def show_directory_groups(
    displayed: list[tuple[int, DirectoryGroup, Path]], root: Path,
    color: bool, planned: bool,
) -> None:
    for number, group, reference in displayed:
        files = group.manifest[1]
        total_size = sum(size for _, size, _ in files)
        heading = (f"Duplicate directory group {number} "
                   f"({len(group.directories)} dirs, {len(files)} files each, "
                   f"{human_size(total_size)} per dir):")
        print(paint(heading, "1;35", color))
        relatives = [str(directory.relative_to(root)) for directory in group.directories]
        common = Path(os.path.commonpath(relatives))
        base = ""
        if common != Path("."):
            print(paint(f"└── {common}/", "1;34", color))
            base = "    "
        ordered = [reference] + [directory for directory in group.directories if directory != reference]
        for index, directory in enumerate(ordered):
            last = index == len(ordered) - 1
            name = str(directory.relative_to(root)) if not base else str(directory.relative_to(root).relative_to(common))
            branch = paint("└── " if last else "├── ", "90", color)
            suffix = ""
            if planned:
                marker = "[KEEP]" if directory == reference else "[REMOVE DIRECTORY; same files as above]"
                suffix = " " + paint(marker, "1;32" if directory == reference else "1;31", color)
            elif directory != reference:
                suffix = " " + paint("[same files as above]", "90", color)
            print(f"{base}{branch}{paint(name + '/', '1;34', color)}{suffix}")
            if directory == reference:
                child_prefix = base + ("    " if last else "│   ")
                for file_index, (path, size, _) in enumerate(files):
                    file_branch = paint("└── " if file_index == len(files) - 1 else "├── ", "90", color)
                    filename = paint(path, "1;32", color)
                    print(f"{child_prefix}{file_branch}{filename} "
                          f"{paint('(' + human_size(size) + ')', '33', color)}")
    if displayed:
        print("\n" + paint(f"Found {len(displayed)} duplicate directory group(s).", "1;35", color))


def delete_copy(path: Path, keep: Path) -> bool:
    try:
        if path.is_symlink() or keep.is_symlink() or not path.is_file() or not keep.is_file():
            raise OSError("file disappeared or became a symlink")
        size = path.stat().st_size
        if size != keep.stat().st_size or digest(path) != digest(keep):
            raise OSError("files no longer have identical contents")
        path.unlink()
    except OSError as error:
        print(f"Cannot delete {path}: {error}", file=sys.stderr)
        return False
    print(f"Deleted: {path}\nKept:    {keep}\nSize:    {human_size(size)} each")
    return True


def modify_keepers(
    groups: list[list[Path]], directory_groups: list[DirectoryGroup],
    keepers: dict[int, Path], root: Path,
) -> bool:
    while True:
        try:
            answer = input(f"Change file group 1-{len(groups)} or directory group d1-d{len(directory_groups)} "
                           "[Enter to review, q to cancel]: ").strip()
        except EOFError:
            return False
        if answer == "":
            return True
        if answer.lower() == "q":
            return False
        if answer.lower().startswith("d") and answer[1:].isdecimal():
            directory_number = int(answer[1:])
            if not 1 <= directory_number <= len(directory_groups):
                print("Invalid directory group number.")
                continue
            directory_group = directory_groups[directory_number - 1]
            print(f"Directory group d{directory_number}: choose the directory to keep")
            for index, directory in enumerate(directory_group.directories, 1):
                print(f"  {index}. {directory.relative_to(root)}/")
            try:
                choice = input(f"Keep directory [1-{len(directory_group.directories)}]: ").strip()
            except EOFError:
                return False
            if not choice.isdecimal() or not 1 <= int(choice) <= len(directory_group.directories):
                print("Invalid directory number; choices unchanged.")
                continue
            selected_dir = directory_group.directories[int(choice) - 1]
            for number in directory_group.file_groups:
                keepers[number] = next(path for path in groups[number - 1] if selected_dir in path.parents)
            continue
        if not answer.isdecimal() or not 1 <= int(answer) <= len(groups):
            print("Enter a file or directory group number, Enter, or q.")
            continue
        number = int(answer)
        paths = groups[number - 1]
        print(f"Group {number}: choose the file to keep")
        for index, path in enumerate(paths, 1):
            marker = " [current]" if path == keepers[number] else ""
            print(f"  {index}. {path.relative_to(root)}{marker}")
        try:
            choice = input(f"Keep file [1-{len(paths)}, Enter for current]: ").strip()
        except EOFError:
            return False
        if choice == "":
            continue
        if not choice.isdecimal() or not 1 <= int(choice) <= len(paths):
            print("Invalid file number; choice unchanged.")
            continue
        keepers[number] = paths[int(choice) - 1]


def remove_duplicates(
    root: Path, groups: list[list[Path]], file_sizes: dict[Path, int],
    directory_groups: list[DirectoryGroup], color: bool,
) -> int:
    keepers = {number: paths[0] for number, paths in enumerate(groups, 1)}
    while True:
        removals = plan_directories(directory_groups, keepers)
        removed_dirs = {item.target for item in removals}
        displayed = []
        hidden_groups = set()
        for number, group in enumerate(directory_groups, 1):
            group_removals = [item for item in removals if item.target in group.directories]
            if len(group_removals) != len(group.directories) - 1:
                continue
            reference = group_removals[0].keep
            displayed.append((number, group, reference))
            hidden_groups.update(group.hideable_groups)
        print("Planned removals:")
        show_directory_groups(displayed, root, color, planned=True)
        show_groups(groups, root, True, file_sizes, color, keepers, removed_dirs, hidden_groups)
        if removals:
            print("Exact directory trees to remove (their files are marked above):")
            rows = [(f"{item.target.relative_to(root)}/", f"{item.keep.relative_to(root)}/")
                    for item in removals]
            width = max(len(target) for target, _ in rows)
            arrow = paint("->", "1;36", color)
            for target, retained in rows:
                print(f"  {target:<{width}}  {arrow}  keep {retained}")
        count = sum(len(paths) - 1 for paths in groups)
        print(f"Will remove {count} file(s) from {len(groups)} group(s), "
              f"using {len(removals)} whole-directory removal(s).")
        try:
            answer = input("Proceed? [y/N/m to modify]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer == "m":
            if not modify_keepers(groups, directory_groups, keepers, root):
                print("Cancelled.")
                return 0
            continue
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 0
        break

    failures = 0
    for item in removals:
        try:
            if directory_manifest(item.target) != item.manifest or directory_manifest(item.keep) != item.manifest:
                raise OSError("directory contents changed after confirmation")
            shutil.rmtree(item.target)
            print(f"Removed directory: {item.target} (kept {item.keep})")
        except OSError as error:
            failures += 1
            print(f"Cannot remove directory {item.target}: {error}", file=sys.stderr)

    for number, paths in enumerate(groups, 1):
        for path in paths:
            if path == keepers[number] or any(directory in path.parents for directory in removed_dirs):
                continue
            failures += not delete_copy(path, keepers[number])
    return 1 if failures else 0


def select_copies(groups: list[list[Path]], root: Path, file_sizes: dict[Path, int]) -> int:
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
    for group_number in sorted({group_number for group_number, _ in selected}):
        size = human_size(file_sizes[groups[group_number - 1][0]])
        print(f"  Group {group_number} ({size} each):")
        for _, path in sorted(item for item in selected if item[0] == group_number):
            print(f"    {path}")
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
    modes.add_argument("--delete", "--delete-one", dest="delete", action="store_true",
                       help="preview and confirm removal of all duplicate copies")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="color for --pretty (default: auto)")
    args = parser.parse_args()
    root = args.directory.expanduser()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")
    root = root.resolve()
    groups, counts, file_sizes, directories, unsafe = scan(root)
    if not groups:
        print("No duplicate files found.")
        return 0
    if args.dirs:
        directory_groups = exact_directory_groups(root, groups, file_sizes, directories, unsafe)
        displayed = [(number, group, group.directories[0])
                     for number, group in enumerate(directory_groups, 1)]
        show_directory_groups(displayed, root, color=False, planned=False)
        show_directories(groups, counts, root)
    elif args.select:
        return select_copies(groups, root, file_sizes)
    elif args.delete:
        color = args.color == "always" or (
            args.color == "auto" and sys.stdout.isatty()
            and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        )
        directory_groups = exact_directory_groups(root, groups, file_sizes, directories, unsafe)
        return remove_duplicates(root, groups, file_sizes, directory_groups, color)
    else:
        color = args.pretty and (args.color == "always" or (
            args.color == "auto" and sys.stdout.isatty()
            and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        ))
        directory_groups = exact_directory_groups(root, groups, file_sizes, directories, unsafe) if args.pretty else []
        displayed = [(number, group, group.directories[0])
                     for number, group in enumerate(directory_groups, 1)]
        show_directory_groups(displayed, root, color, planned=False)
        hidden_groups = {number for group in directory_groups for number in group.hideable_groups}
        show_groups(groups, root, args.pretty, file_sizes, color, hidden_groups=hidden_groups)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)
