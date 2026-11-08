<!-- Copyright (c) 2026 Nenad Micic -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# JotTune MCP

A local stdio MCP server for composing, rendering, inspecting and revising
music with JotTune Synth. It provides complete TL records, native WAV audio,
SVG/PNG visualizations, MIDI/SFZ conversion and portable session recovery.
Music remains in the workspace you select. No account, hosted model or API key
is required; the server has no song-publication operation.

Version 0.1.0 is an unpublished public beta candidate for
`@chimepond/jottune-mcp`. Preparing or installing a local archive does not
publish it to npm.

## Requirements

For normal setup, use Node.js 24 and Python 3.12 or later. The compatible minimum
families are Node.js 22 and Python 3.10. A JotTune Synth SDK checkout or a package
containing one is required. The native renderer needs Make and a C99 compiler.
An existing compatible renderer can instead be supplied with `--harness`.

The package installation and core composition/delivery workflow have been checked on macOS
with Node 25.8.2/Python 3.14.7 and Linux x86-64 with Node 24.22.1/Python 3.12.3.
The Node 22/Python 3.10 families also pass with Node 22.23.4/Python 3.10.22 in
an isolated Linux x86-64 container. These checks build the bundled renderer,
discover all 31 tools and exercise composition, revision, rendering, artifact
delivery, exports and session recovery.

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
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Use Python 3.10 or later"'
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/core.txt
export JOTTUNE_PYTHON="$PWD/.venv/bin/python"
node bin/jottune-mcp.mjs build
mkdir -p session
node bin/jottune-mcp.mjs doctor --workspace session
node bin/jottune-mcp.mjs config --workspace session
```

Copy the printed configuration into your MCP client's local stdio-server settings.
Configuration layouts differ by client; this prints the conventional mcpServers
entry, not a claim that every client accepts that file format unchanged. The
protocol workflow is tested through the Python MCP client; individual desktop
clients require their own configuration and acceptance check.

Copy the printed configuration into your MCP client. It contains absolute
launcher, SDK, Python and workspace paths. Configuration generation does not
edit client files. Reconnect the client after changing its configuration.

For an installed npm package, use the `jottune-mcp` command from that
installation instead of `node bin/jottune-mcp.mjs`. Keep the Python virtual
environment outside the installed package and locate its requirements through
the command, rather than assuming a source-checkout layout:

```sh
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Use Python 3.10 or later"'
python3 -m venv ~/.local/share/jottune-mcp/venv
export JOTTUNE_PYTHON="$HOME/.local/share/jottune-mcp/venv/bin/python"
"$JOTTUNE_PYTHON" -m pip install -r "$(jottune-mcp requirements)"
mkdir -p session
jottune-mcp build
jottune-mcp doctor --workspace session
jottune-mcp config --workspace session
```

The explicit build writes into the selected SDK, so that directory must be
writable. A separately built renderer can instead be selected with `--harness`.
Reinstalling or updating the npm package replaces its bundled SDK and removes
that local build; run `jottune-mcp build` and `doctor` again before reconnecting.
A separately managed SDK/harness stays under your control.

`--workspace` is the boundary for tool-selected inputs, outputs and artifact
reads, including resolved symlink targets. Copy files into that directory before
importing or exporting them. Use workspace-relative paths or explicit contained
paths; tilde/home aliases are refused. A directory argument is checked entry by
entry, following symlinks, and is refused when it holds more than 65536 entries,
a special file, or anything that resolves outside the workspace. Files with more
than one hard link are refused, because another name for the same file can lie
outside the workspace; copy such a file first. Analysis and catalog outputs
require new paths and never replace existing files. The configured SDK and
native executable remain explicit trusted startup inputs outside that directory.

This is a path boundary, not an operating-system sandbox. Paths are checked when
a call starts and again in its worker, but files are opened by path afterwards.
Another process running with the same account permissions can replace a checked
file or directory with a symlink after those checks and redirect a read or write
outside the workspace. Use a workspace that no untrusted process can modify.

Tools run in separate children with a 120-second execution limit and bounded
output capture. At most `JOTTUNE_MCP_CONCURRENCY` calls (1 to 8, default 2) run
at once; an invalid value stops the server at startup. Further calls wait
without using their execution limit and can be cancelled while waiting.
Cancellation, input EOF and normal termination stop their native descendants,
and a worker stops itself and its native descendants shortly after its server
is killed. MCP native rendering, including section measurements, first inspects
the TL against the same 192 MiB render/256 MiB memory profile as the browser;
`midi_import` and `transcribe` render inside their converters without that
inspection. A timeout or a killed server can leave an incomplete receipt or
already completed output; inspect retained logs before retrying.

Each call works in a `.jottune-operation-*` directory in the workspace, which is
removed when the call ends, including after a timeout or cancellation. If the
server itself is killed, the next server start removes directories with that
generated name whose contents have been unchanged for more than an hour. Do not
keep your own files under that name.

Default replies are compact, parameter reads are paged, and binary artifact
reads accept up to 32768 bytes per page. A tool reply larger than 48 KiB of JSON
is replaced by an envelope that keeps `version`, `ok`, `exit` and small fields,
sets `truncated: true`, lists up to 256 omitted fields with their sizes in
`reply_omitted` (counting any others in `reply_omitted_unlisted`) and names a
paged or receipt reader in `next`; `analyze` keeps its PNG locator in
`artifact`. As the MCP specification recommends, each reply is sent both as
structured content and as the same JSON in a text block, so a message carries
it twice. Clients with smaller reply limits should request compact or paged
detail and smaller artifact pages.

## Discover and compose

Discover the tools, then read `reference(section="index")`. The instrument guide
is also available as `fm://instrument`; bounded `reference` pages avoid large
responses. Each `fm://` resource also has an `fm://<name>/page/{page}` template
that returns the same bytes in numbered JSON pages of at most 32 KiB of text;
follow `next` and check the assembled bytes against `sha256`.
`reference(source="reference-options")` describes selectors and paging.
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
.venv/bin/python tests/security.py
.venv/bin/python tests/integration.py --synth-root ../jottune-synth
```

The staged package contains an exact selected synth source revision, including
the native build inputs and reusable authoring tools. It excludes song archives,
applications, sharing infrastructure, acoustic experiments and test fixtures.
`synth-release.json` records the bundled source revision. Build the final archive
from the committed source revision being released so that this citation resolves
to the corresponding published SDK. Run installation and integration checks on
the exact archive being published. The source checkout is `private: true`, so
publishing directly from it is refused: it does not contain the bundled SDK.
The explicit packaging script produces a publishable manifest only after the SDK
and `synth-release.json` have been assembled. No build, test or installation
command publishes anything. Publish the exact tested `.tgz`, rather than the
source directory. The npm publication configuration selects public access and the
`beta` distribution tag; publication remains an explicit final operation.

Code is licensed under Apache-2.0. Bundled synth dependencies retain their
upstream licences and notices, including the synth NOTICE for adapted tables.
For security reports, rights questions or takedown requests, see SECURITY.md.

The prepared GitHub workflow checks the launcher on Linux and macOS. Its native
integration job requires repository variable `JOTTUNE_SYNTH_REF` set to the full
public SDK commit ID. That job is skipped until configured; launcher checks alone
do not establish SDK integration. Hosted CI has not been run for this candidate.
