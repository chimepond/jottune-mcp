<!-- Copyright (c) 2026 Nenad Micic -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# JotTune MCP

A local stdio MCP server for composing, rendering, inspecting and revising
music with JotTune Synth. It provides complete TL records, native WAV audio,
SVG/PNG visualizations, MIDI/SFZ conversion and portable session recovery.
Music remains in the workspace you select. No account, hosted model or API key
is required; the server has no song-publication operation.

## Install from npm

On macOS or Linux, use Node.js 22 or later, Python 3.10 or later, Make and a
C99 compiler. On Debian or Ubuntu, install `python3-venv` for the system Python
so that the virtual environment includes pip. The npm package includes the synth SDK.
[Package on npm](https://www.npmjs.com/package/@chimepond/jottune-mcp)

```sh
npm install -g @chimepond/jottune-mcp
```

The first public beta is `0.1.0`. Use `@chimepond/jottune-mcp@0.1.0` to pin
that release or `@chimepond/jottune-mcp@beta` to follow the beta channel.

From a directory where you keep projects, create a song workspace, install the
Python dependencies and build the renderer. Choose an npm installation location
your account can write to, since the build writes into the bundled SDK.

```sh
mkdir -p jottune-session/songs
cd jottune-session
python3 -m venv .venv
export JOTTUNE_PYTHON="$PWD/.venv/bin/python"
"$JOTTUNE_PYTHON" -m pip install -r "$(jottune-mcp requirements)"
jottune-mcp build
jottune-mcp doctor --workspace songs
jottune-mcp config --workspace songs
```

For a working core installation, `doctor` reports `"ok": true`. Copy the
`jottune` entry from the printed `mcpServers` configuration into your MCP
client’s local stdio settings and reconnect. For clients with another settings format, use the printed command,
arguments and environment. The integration tests use the Python MCP client.
Try asking your connected agent to compose a short piece and return its editable
TL file and rendered WAV.

In a new shell, set `JOTTUNE_PYTHON` to the same virtual environment before
running `doctor` or `config`. After upgrading the JotTune package, rerun
`jottune-mcp build` and `jottune-mcp doctor --workspace songs` from the session directory.

## Related repositories

- [JotTune Synth](https://github.com/chimepond/jottune-synth) is the renderer,
  the TL format and the SDK that this server runs. Its README has the project map.
- The [synth examples](https://github.com/chimepond/jottune-synth/tree/main/examples)
  include complete songs you can play locally. More are in the
  [ChimePond collection](https://github.com/chimepond/tunes).
- [ChimePond](https://chimepond.com) is the separately operated sharing platform.
  This server has no publication operation.

## Requirements

For normal setup, use Node.js 24 and Python 3.12 or later. You can select an
existing compatible native renderer with `--harness PATH`.

The package installation and core composition/delivery workflow have been checked on macOS
with Node 25.8.2/Python 3.14.7 and Linux x86-64 with Node 24.22.1/Python 3.12.3.
The Node 22/Python 3.10 families also pass with Node 22.23.4/Python 3.10.22 in
an isolated Linux x86-64 container. These checks build the bundled renderer,
discover all 31 tools and exercise composition, revision, rendering, artifact
delivery, exports and session recovery.

The Node launcher has no npm runtime dependencies. The Python MCP SDK has
transitive dependencies. NumPy and Matplotlib are optional, explicitly installed
capabilities; no install hook compiles or downloads an engine.

## Run from a source checkout

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

Copy the printed server entry into your client's local stdio settings and reconnect.

## Workspace and execution

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

To add analysis and PNG plots to the npm setup, run this in the same shell.

```sh
"$JOTTUNE_PYTHON" -m pip install -r "$(jottune-mcp requirements --plots)"
```

Use `--analysis` for NumPy alone. In a source checkout, pass
`requirements/analysis.txt` or `requirements/plots.txt` to the same Python interpreter.

Analysis enables measured audio analysis and descriptor search. Plots adds PNG
spectrograms. Core composition, revision, rendering, recipes, SVG and session
recovery do not require either extra. `doctor` distinguishes core availability
from a complete optional environment. All tool schemas remain discoverable;
operations needing an absent optional dependency return a clear refusal.

The launcher also exposes `guide`, `svg` and `interchange` commands. Run
`jottune-mcp --help` in an npm installation or `node bin/jottune-mcp.mjs --help`
in a source checkout.

## Package and test

Package committed, clean MCP and synth checkouts explicitly:

```sh
mkdir -p dist
node scripts/package.mjs --synth-root ../jottune-synth --output dist/package
npm pack ./dist/package --ignore-scripts --pack-destination dist
npm test
.venv/bin/python tests/security.py
.venv/bin/python tests/integration.py --synth-root ../jottune-synth
```

The staged package contains tracked MCP files and an exact selected synth source
revision, including the native build inputs, reusable authoring tools and CLI
decoder. Ignored local modules are excluded. It excludes song archives,
applications, sharing infrastructure, acoustic experiments and test fixtures.
`synth-release.json` records `source_revision` for the bundled SDK and
`mcp_source_revision` for the transport/launcher. Build the final archive from
the committed revisions being released so these citations resolve to their
corresponding published sources. Run installation and integration checks on
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

For help with the music tools, email [ChimePond support](mailto:support@chimepond.com).
To discuss the project with Nenad Micic, email [chimepond@nmic.app](mailto:chimepond@nmic.app).

The prepared GitHub workflow checks the launcher on Linux and macOS. Its native
integration job requires repository variable `JOTTUNE_SYNTH_REF` set to the full
public SDK commit ID. Check that the integration job passed on the release commit
before publishing the archive. Without this variable only the launcher job runs.

## Portable TL documents

The bundled SDK preserves an optional bounded metadata header through song
authoring, rendering and export. Score inputs accept an explicit `metadata`
object; no model attribution or licence is inferred. Existing headerless TL
files remain valid. The song key hashes canonical music only, while
`document_sha256` identifies complete file bytes. `doctor` reports supported
`tl_format` and engine compatibility counters. Unsupported newer requirements
are refused. Publication consent and removal credentials are never supplied by
the metadata header. See the bundled `synth/docs/TL-FORMAT.md`.
