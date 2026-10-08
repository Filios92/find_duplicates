# Duplicate file finder

Scan one or more directories recursively using file size and SHA-256 content
hashes. All supplied directories are searched together, so duplicates across
them appear in the same group:

```sh
./find_duplicates.py /path/to/directory
./find_duplicates.py /path/to/first /path/to/second --pretty
```

Paths in compact views are shown relative to the directories' common parent.
Repeated or nested inputs are scanned once.
With `--delete`, an entire supplied directory can be proposed for removal when
another supplied directory is an exact retained copy; the confirmation preview
shows that operation explicitly.

Available modes:

```sh
./find_duplicates.py /path/to/directory --pretty
./find_duplicates.py /path/to/directory --dirs
./find_duplicates.py /path/to/directory --select
./find_duplicates.py /path/to/directory --delete
```

`--pretty` displays each duplicate group as a compact, colored path tree. Exact
duplicate directory trees appear as one directory group: the files and their
sizes are listed under one directory, and the other directories say
`[same files as above]`.
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

`--delete` plans to keep one file in every duplicate group and remove all other
copies. It prints the compact tree with `[KEEP]`, `[REMOVE]`, and
`[REMOVE WITH DIRECTORY]` marks before asking `Proceed? [y/N/m to modify]`.
The operation summary lists directories first, then every file to remove
individually, before the confirmation prompt. It includes the combined size of
all files that would be removed, including files inside removed directories.
The default answer is no. Choose `m` to change which file is kept in any group,
then review the updated plan before confirming. `--delete-one` remains an alias
for `--delete`.

When a whole directory tree has the same relative file paths, contents, and
empty subdirectories as a retained tree, the plan removes that directory in
one operation. The preview shows the files under the kept directory and marks
the other one `[REMOVE DIRECTORY; same files as above]`. File permissions,
owners, and timestamps do not affect the match. Choose `m`, then a directory
group such as `d1`, to switch which directory is kept. Both trees are checked
again before removal. Ctrl-C cancels cleanly with exit status 130. Symlinked
files and directories are skipped by the scan.
