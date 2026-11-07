# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Locate the shared synth source without importing another transport."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys

REQUIRED = ("Makefile", "tools/fm.py", "tools/interchange.py", "docs/INSTRUMENT.md", "src/host/harness.c")


def package_version():
    manifest = Path(__file__).resolve().parents[1] / "package.json"
    version = json.loads(manifest.read_text(encoding="utf8")).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("The JotTune MCP package manifest needs a version.")
    return version


def resolve_sdk(explicit=None):
    package = Path(__file__).resolve().parents[1]
    selected = explicit if explicit is not None else os.environ.get("JOTTUNE_SYNTH_ROOT")
    if selected is not None and not str(selected).strip():
        raise ValueError("A complete JotTune Synth SDK is required; the selected path is empty.")
    candidates = (package / "synth", package.parent / "jottune-synth") if selected is None else (Path(selected),)
    for candidate in candidates:
        directory = candidate.resolve()
        if all((directory / name).is_file() for name in REQUIRED):
            return directory
    raise ValueError("A complete JotTune Synth SDK is required; set JOTTUNE_SYNTH_ROOT or --synth-root PATH.")


def operations(explicit=None):
    root = resolve_sdk(explicit)
    sys.path.insert(0, str(root / "tools"))
    fm = importlib.import_module("fm")
    interchange = importlib.import_module("interchange")
    if Path(fm.ROOT).resolve() != root:
        raise ValueError("This process already imported a different JotTune Synth SDK.")
    return fm, interchange
