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

`--pretty` displays each duplicate group as a compact path tree. `--dirs` lists
directory pairs ranked by the number of duplicate content groups found beneath
both directories. The file counts also include files without duplicates; use
them to judge how similar the directories are. This mode does not delete
directories.

Each listed file includes its size in B, KiB, MiB, and larger units as needed.

`--select` requires `fzf`. Select files with Tab, press Enter, then type
`DELETE` at the confirmation prompt. The script refuses a selection that would
remove every copy in a duplicate group.

`--delete-one` removes exactly one file per run: the lexicographically last
path in the first duplicate group, keeping the first path. Both files are
hashed again immediately before deletion. Symlinked files and directories are
skipped by the scan.
