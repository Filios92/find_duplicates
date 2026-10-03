# Duplicate file finder

Scan a directory recursively using file size and SHA-256 content hashes:

```sh
./find_duplicates.py /path/to/directory
```

Available modes:

```sh
./find_duplicates.py /path/to/directory --pretty
./find_duplicates.py /path/to/directory --dirs
./find_duplicates.py /path/to/directory --select
./find_duplicates.py /path/to/directory --delete-one
```

`--pretty` displays each duplicate group as a compact, colored path tree.
Colors appear automatically in a terminal. Use `--color always` to retain
colors when piping output, or `--color never` to disable them. Setting
`NO_COLOR` also disables automatic colors. `--dirs` lists
directory pairs ranked by the number of duplicate content groups found beneath
both directories. The file counts also include files without duplicates; use
them to judge how similar the directories are. This mode does not delete
directories.

Each duplicate group shows the size of one file in B, KiB, MiB, or a larger
unit, for example `Duplicate group 1 (2 files, 374 KiB each)`.

`--select` requires `fzf`. Select files with Tab, press Enter, then type
`DELETE` at the confirmation prompt. The script refuses a selection that would
remove every copy in a duplicate group.

`--delete-one` removes exactly one file per run: the lexicographically last
path in the first duplicate group, keeping the first path. Both files are
hashed again immediately before deletion. Symlinked files and directories are
skipped by the scan.
