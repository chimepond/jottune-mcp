# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Constrain caller-selected paths before any tool reads or creates files."""
from pathlib import Path
import os
import stat


class WorkspaceError(ValueError):
    pass


LINKED = ("Workspace files must have exactly one hard link; copy the file, or remove a leftover "
          ".*-pending-* staging directory, before using it.")


class Workspace:
    PATHS = {"files", "out", "log", "receipt_dir", "directory", "sources", "records",
             "tsv", "among", "wav", "bundle", "a", "b"}
    # Directory inputs are walked completely before use; this bounds that walk.
    TREE_ENTRIES = 65536

    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspaceError("Choose an existing workspace directory.")

    def path(self, value):
        if not isinstance(value, str) or not value or "\0" in value:
            raise WorkspaceError("A tool path must be a nonempty string.")
        # Adapters differ in whether they expand '~'. Reject that alias before
        # validation so no tool can validate a literal workspace path and then
        # open or publish an expanded home-directory target instead.
        if value.startswith("~"):
            raise WorkspaceError("Use workspace-relative or explicit workspace-contained paths, not '~' aliases.")
        selected = Path(value)
        selected = selected if selected.is_absolute() else self.root / selected
        resolved = selected.resolve()
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError("Tool paths must remain inside the configured workspace, including symlink targets.")
        # Refuse special files before an adapter can block while opening one.
        if resolved.exists() and not (resolved.is_file() or resolved.is_dir()):
            raise WorkspaceError("Tool paths must select regular files or directories.")
        # A second hard link can name a file outside the workspace; the path
        # alone cannot show which name is the original.
        if resolved.is_file() and resolved.stat().st_nlink > 1:
            raise WorkspaceError(LINKED)
        return resolved

    def tree(self, directory):
        """Apply the path rules to every entry an adapter could read below a directory."""
        pending, seen, count = [directory], set(), 0
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            with os.scandir(current) as entries:
                for entry in entries:
                    count += 1
                    if count > self.TREE_ENTRIES:
                        raise WorkspaceError("Directory inputs are limited to " + str(self.TREE_ENTRIES)
                                             + " entries; select a smaller directory.")
                    if entry.is_symlink():
                        target = self.path(entry.path)
                        if target.is_dir():
                            pending.append(target)
                        continue
                    status = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(status.st_mode):
                        pending.append(Path(entry.path))
                    elif not stat.S_ISREG(status.st_mode):
                        raise WorkspaceError("Tool paths must select regular files or directories.")
                    elif status.st_nlink > 1:
                        raise WorkspaceError(LINKED)

    def checked(self, value):
        resolved = self.path(value)
        if resolved.is_dir():
            self.tree(resolved)
        return resolved

    def validate(self, name, arguments):
        for key, value in arguments.items():
            if value is None:
                continue
            if (key in self.PATHS and (key != "wav" or name == "analyze")) or key == "source" and name != "reference":
                for item in value if isinstance(value, list) else [value]:
                    self.checked(item)
            elif key == "source_ref" and isinstance(value, dict):
                self.checked(value.get("directory"))
        if name == "midi_import":
            options = arguments.get("options") or []
            for index, option in enumerate(options):
                key, separator, inline = option.partition("=")
                # Match every abbreviation a parser could expand, not only exact names.
                files = [full for full in ("--tuning", "--scala") if len(key) > 2 and full.startswith(key)]
                if files:
                    selected = inline if separator else options[index + 1] if index + 1 < len(options) else ""
                    scala = "--scala" in files
                    for item in ({selected, selected.rpartition(":")[0] or selected, *selected.split(":")}
                                 if scala else [selected]):
                        self.checked(item)
        # These two historical adapters overwrite files. MCP always requires a
        # new file; the native/public adapters also publish exclusively.
        if name in ("analyze", "catalog_build") and arguments.get("out") is not None:
            destination = self.path(arguments["out"])
            if os.path.lexists(destination):
                raise WorkspaceError("Output already exists; choose a new output path.")
        # Indirect retained-artifact paths can contain a linked child directory.
        if name == "artifact_read":
            directory = self.path(arguments["directory"])
            filename = arguments.get("filename")
            if filename is not None:
                self.path(str(directory / filename))
            for child in ("receipts", "records", "sounds", "log.tsv", "manifest.json"):
                self.path(str(directory / child))
