# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Constrain caller-selected paths before any tool reads or creates files."""
from pathlib import Path
import os


class WorkspaceError(ValueError):
    pass


class Workspace:
    PATHS = {"files", "out", "log", "receipt_dir", "directory", "sources", "records",
             "tsv", "among", "wav", "bundle", "a", "b"}

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
        return resolved

    def validate(self, name, arguments):
        for key, value in arguments.items():
            if value is None:
                continue
            if (key in self.PATHS and (key != "wav" or name == "analyze")) or key == "source" and name != "reference":
                for item in value if isinstance(value, list) else [value]:
                    self.path(item)
            elif key == "source_ref" and isinstance(value, dict):
                self.path(value.get("directory"))
        if name == "midi_import":
            options = arguments.get("options") or []
            for index, option in enumerate(options):
                key, separator, inline = option.partition("=")
                if key in ("--tuning", "--scala"):
                    selected = inline if separator else options[index + 1] if index + 1 < len(options) else ""
                    for item in selected.split(":") if key == "--scala" else [selected]:
                        self.path(item)
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
