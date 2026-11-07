<!-- Copyright (c) 2026 Nenad Micic -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# JotTune MCP

A local stdio MCP server for composing, rendering, inspecting and revising
music with JotTune Synth. It provides complete TL records, native WAV audio,
SVG/PNG visualizations, MIDI/SFZ conversion and portable session recovery.
Music remains in the workspace you select. No account, hosted model or API key
is required; the server has no song-publication operation.

The package is development software and has not been released to npm.
Its intended npm name is `@chimepond/jottune-mcp`.

## Requirements

Use Node.js 22+, Python 3.10+, and a JotTune Synth SDK checkout or a package
containing one. The native renderer requires Make and a C99 compiler to build.
An existing compatible renderer can instead be supplied with `--harness`.

The Node launcher has no npm runtime dependencies. The Python MCP SDK has
transitive dependencies. NumPy and Matplotlib are optional, explicitly installed
capabilities; no install hook compiles or downloads an engine.

For source development, place `jottune-mcp` and `jottune-synth` beside one another,
or select the SDK with `JOTTUNE_SYNTH_ROOT` or `--synth-root PATH`. An explicit
selection is authoritative: an invalid path fails rather than falling back.
Without an explicit selection, a package's bundled `synth/` takes priority over
a sibling checkout. The engine and authoring helpers are maintained in
`jottune-synth`; this repository maintains only the MCP transport and launcher.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/core.txt
export JOTTUNE_PYTHON="$PWD/.venv/bin/python"
node bin/jottune-mcp.mjs build
mkdir -p session
node bin/jottune-mcp.mjs doctor --workspace session
node bin/jottune-mcp.mjs config --workspace session
```

Copy the printed configuration into your MCP client. It contains absolute
launcher, SDK, Python and workspace paths. Configuration generation does not
edit client files. Reconnect the client after changing its configuration.

`--workspace` resolves relative tool paths and does not restrict the account's
filesystem permissions. The server needs access to files involved in the session.

## Discover and compose

Discover the tools, then read `reference(section="index")`. The instrument guide
is also available as `fm://instrument`; bounded `reference` pages avoid large
responses. `reference(source="reference-options")` describes selectors and paging.
Read the musical-time score section to construct an editable score, use
`compose`, revise it with `score_revise`, and render the canonical TL with
`render(wav=true)`. Retrieve returned artifact locators through `artifact_read`
until EOF. Check the returned status and checksum; a server path alone is not
a delivered artifact.

Native validation is authoritative. Creating a file, verifying replay and
listening to audio are distinct outcomes. The tools retain explicit identity,
conversion-loss and optional operation-receipt information so work can resume
without relying on conversation history. Songs and source prompts are not
bundled in the npm package.

## Optional analysis and plots

```sh
.venv/bin/python -m pip install -r requirements/analysis.txt
.venv/bin/python -m pip install -r requirements/plots.txt
```

Analysis enables measured audio analysis and descriptor search. Plots adds PNG
spectrograms. Core composition, revision, rendering, recipes, SVG and session
recovery do not require either extra. `doctor` distinguishes core availability
from a complete optional environment. All tool schemas remain discoverable;
operations needing an absent optional dependency return a clear refusal.

The launcher also exposes `guide`, `svg` and `interchange` commands. Run
`node bin/jottune-mcp.mjs --help` for usage.

## Package and test

Package a committed, clean synth checkout explicitly:

```sh
mkdir -p dist
node scripts/package.mjs --synth-root ../jottune-synth --output dist/package
npm pack ./dist/package --ignore-scripts --pack-destination dist
npm test
.venv/bin/python tests/integration.py --synth-root ../jottune-synth
```

The staged package contains an exact selected synth source revision, including
the native build inputs and reusable authoring tools. It excludes song archives,
applications, sharing infrastructure, acoustic experiments and test fixtures.
`synth-release.json` records the bundled source revision. Source and staged
packages retain `private: true`; releasing a version requires an explicit change.

Code is licensed under Apache-2.0. Bundled synth dependencies retain their
upstream licences and notices.
