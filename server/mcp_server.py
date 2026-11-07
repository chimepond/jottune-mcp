#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Stdio MCP transport for the same stateless native synth operations."""
from __future__ import annotations

import argparse
import copy
import inspect
import json
from pathlib import Path
import sys
from typing import Any, Literal, get_type_hints

from sdk import operations, package_version


def interchange_reply(value, detail):
    """Keep conversion decisions and delivery paths without per-event arrays."""
    result = copy.deepcopy(value)
    result["detail"] = detail
    bundle = result.get("bundle", {})
    if result.get("published"):
        if bundle.get("container") == "zip":
            archive = Path(bundle["archive"])
            if archive.suffix.lower() == ".zip" and "\\" not in archive.name:
                result["delivery"] = {"archive": dict(directory=str(archive.parent), kind="zip",
                    digest=bundle["sha256"], filename=archive.name)}
            else:
                result["delivery_unavailable"] = "Use a .zip basename without backslashes for artifact_read delivery."
        else:
            directory = bundle["directory"]
            result["delivery"] = {"manifest": dict(directory=directory, kind="session_manifest",
                digest=bundle["manifest_sha256"])}
            for name, kind in (("source.tl", "record"), ("performance.mid", "midi"),
                               ("instrument.sfz", "sfz"), ("audio.wav", "wav")):
                if name in bundle["artifacts"]:
                    result["delivery"][kind] = dict(directory=directory, kind=kind,
                        digest=bundle["artifacts"][name]["sha256"], filename=name)
    if detail == "full":
        return result
    omitted = {}

    def omit(parent, key, prefix):
        if isinstance(parent, dict) and key in parent:
            item = parent.pop(key)
            omitted[prefix + key] = len(item) if isinstance(item, (list, dict, str)) else 1

    report = result.get("report", {})
    midi = report.get("midi", {})
    for key in ("timing", "pitch", "velocity"):
        omit(midi.get(key), "rows", "report.midi." + key + ".")
    omit(midi, "event_trace", "report.midi.")
    omit(midi, "losses", "report.midi.")  # Same complete categories are in report.losses.
    samples = report.get("samples", {})
    for key in ("cells", "source", "losses"):
        omit(samples, key, "report.samples.")
    inventory = report.get("inventory", {})
    for key in ("voices", "patches"):
        omit(inventory, key, "report.inventory.")
    for source, prefix in ((result.get("source"), "source."), (report.get("source"), "report.source.")):
        omit(source, "source_files", prefix)
        omit(source, "absent_sibling_streams", prefix)
    omit(bundle, "artifacts", "bundle.")
    result["omitted_counts"] = omitted
    result["full_report"] = ("manifest.json in the output bundle" if result.get("published")
                             else "Request detail='full'; no report file was retained.")
    return result


def create_server(harness=None, *, synth_root=None):
    fm, interchange = operations(synth_root)
    from mcp.server import MCPServer
    from mcp.types import CallToolResult, TextContent
    from pydantic import TypeAdapter, ValidationError

    argument_specs = {}

    class StrictServer(MCPServer):
        async def call_tool(self, name, arguments, context=None):
            # This public dispatch hook runs after protocol/handshake validation
            # but before SDK argument coercion. Missing required bindings keep
            # their SDK tool-error envelope; complete calls get usage refusals.
            if name in argument_specs:
                adapters, required = argument_specs[name]
                if required <= arguments.keys():
                    unknown = sorted(arguments.keys() - adapters.keys())
                    message = "unknown argument(s): " + ", ".join(unknown) if unknown else None
                    if message is None:
                        for key, value in arguments.items():
                            try:
                                adapters[key].validate_python(value, strict=True)
                            except ValidationError:
                                message = "argument " + key + " must match its declared JSON type and choices"
                                break
                    if message is not None:
                        reply = dict(version=1, ok=False, exit=2, message=message)
                        return CallToolResult(content=[TextContent(type="text", text=json.dumps(reply))],
                                              structured_content=reply)
            return await super().call_tool(name, arguments, context)

        async def list_tools(self):
            # Copy public definitions so discovery matches the rejection rule.
            result = [tool.model_copy(deep=True) for tool in await super().list_tools()]
            for tool in result:
                tool.input_schema["additionalProperties"] = False
                if tool.name == "interchange_export":
                    tool.input_schema["properties"]["midi_format"]["enum"] = [0, 1]
                if tool.name == "compose":
                    tool.input_schema["properties"]["score"] = {"anyOf": [fm.score_module().score_schema(), {"type": "null"}]}
                if tool.name in ("measure_sections", "measure_voice_sections"):
                    endpoint = {"type": "integer", "minimum": 0, "maximum": 2 ** 64 - 1}
                    tool.input_schema["properties"]["sections"] = {"type": "array", "minItems": 1,
                        "maxItems": fm.measure_module().MAX_SECTIONS, "items": {"type": "object",
                            "properties": {"start_ns": endpoint,
                                "end_ns": {"anyOf": [endpoint, {"type": "null"}]}},
                            "additionalProperties": False}}
                    if tool.name == "measure_voice_sections":
                        tool.input_schema["properties"]["voices"] = {"type": "array", "minItems": 1,
                            "maxItems": 64, "uniqueItems": True,
                            "items": {"type": "integer", "minimum": 0, "maximum": 63}}
                if tool.name in ("compose", "score_inspect", "score_revise"):
                    tool.input_schema["properties"]["source_ref"] = {"anyOf": [{"type": "object",
                        "properties": {"directory": {"type": "string"}, "kind": {"const": "score"},
                            "digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                            "filename": {"type": "string", "pattern": "^[0-9a-f]{64}\\.json$"}},
                        "required": ["directory", "kind", "digest", "filename"], "additionalProperties": False},
                        {"type": "null"}]}
                    tool.input_schema["oneOf"] = [
                        {"required": ["score"], "properties": {"score": {"type": "object"}, "source_ref": {"type": "null"}}},
                        {"required": ["source_ref"], "properties": {"score": {"type": "null"}, "source_ref": {"type": "object"}}}]
            return result

    server = StrictServer(
        "jottune-mcp", title="JotTune MCP", version=package_version(), subscriptions=False, log_level="WARNING",
        instructions="Start with reference() for the instrument in bounded text pages. "
        "reference(source='reference-options') describes each source's selectors, copyable requests and declared reference costs without native work. "
        "Use reference(section='index') to discover guide topics and reference(source='params', section='send') for native fields. "
        "Follow next_offset and keep source/selection hashes equal across pages. Bulk resources may exceed client limits. "
        "Native validation is authoritative. "
        "compose accepts explicit rational musical-time scores, requires log and receipt_dir, "
        "and retains lowered text and native outcomes without WAV; its default reply is compact. "
        "score_inspect shows authored sections/voices and gate advisories; score_revise previews one identity-checked replacement. "
        "Use their retained source_ref in compose to validate and publish the revision. "
        "measure_section measures final output after a complete render including prior effects history; it uses a full temporary WAV. "
        "measure_sections batches up to32 ordered windows with the same full history and two render passes through a pipe, without an audio file. "
        "gen_events inspects the native generated-note schedule without PCM; optional receipts retain its complete output for retrieval. "
        "Operations have no current patch or session; supply timeline text or input paths and explicit output paths. "
        "Check exit and measured checks separately. Use explicit detail='compact' on render/check_patch for long scores; "
        "legacy full remains their default. Compact render supplies a verified record reader; "
        "receipt_dir retains complete validation outcomes without rerendering. describe/compare default to compact; "
        "page detail retrieves omitted full-result data with a stable result SHA256; windows selects existing spatial windows. "
        "Opt into receipt_dir to retain requests, outcomes and complete measured/search results without recomputing. "
        "Resume from log_show(kind='operations') and artifact_read(kind='receipt'), never inferred conversation state. "
        "Use probe_plan for structural render costs and session_export/session_verify for explicit portable snapshots. "
        "MIDI import defaults to a dry run; discover converter flags through reference(source='midi-options'). "
        "interchange_inspect inventories export options; interchange_export renders WAV, MIDI or SFZ with dry-run by default. "
        "Use explicit new out paths for complete directory/ZIP bundles and accept_loss for MIDI or sampled patches. "
        "interchange_verify checks a relocated bundle without rendering; artifact_read accepts export delivery locators for complete .zip transfer. Conversion reports replace operation receipts here. "
        "transcribe approximates separated mono notes from WAV; discover transcribe-options and opt into receipts before costly work. "
        "Retain composed songs as canonical timelines. Created files, verified byte retrieval, "
        "and actual listening are separate evidence.",
    )

    def tool(fn):
        signature, hints = inspect.signature(fn), get_type_hints(fn)
        argument_specs[fn.__name__] = (
            {name: TypeAdapter(hints[name]) for name in signature.parameters},
            {name for name, param in signature.parameters.items() if param.default is inspect.Parameter.empty},
        )
        return server.tool()(fn)

    @tool
    def params() -> dict[str, Any]:
        """Return the full native inventory; restricted clients should use bounded reference(source='params') instead."""
        return fm.params(harness=harness)

    @tool
    def reference(source: Literal["instrument", "params", "gen-params", "recipes", "midi-options", "transcribe-options", "reference-options"] = "instrument",
                  section: str | None = None, offset: int = 0, length: int = 4096) -> dict[str, Any]:
        """Read bounded source text. reference-options supplies JSON selector examples and declared reference costs without native work. section=index lists guide/native topics; all reads the whole source. Recipes support index/all only; use load_patch(name) for one recipe. midi-options/transcribe-options/reference-options support all only. Follow next_offset; length1..8192 UTF-8 bytes. Compare source/selection hashes across pages; they are not input guards."""
        return fm.reference(source=source, section=section, offset=offset, length=length, harness=harness)

    @tool
    def gen_events(files: list[str] | None = None, text: str | None = None,
                   offset: int = 0, length: int = 4096,
                   expected_manifest_sha256: str | None = None,
                   detail: Literal["page", "summary"] = "page",
                   start_ns: int | None = None, end_ns: int | None = None,
                   receipt_dir: str | None = None, receipt_parent: str | None = None,
                   receipt_purpose: str = "") -> dict[str, Any]:
        """Inspect actual native generated events from exactly one of ordered files or complete text. No PCM/WAV/take publication. At most17 regular-file occurrences/4MiB inputs; no recipe expansion. Every call normally runs two discovery children and one manifest child; raw pages re-execute. Follow next_offset with expected_manifest_sha256, assemble UTF8 to EOF and verify the manifest hash. Prefer receipt_dir for long output: full_result_sha256 identifies complete JSON retrievable with artifact_read(kind='receipt') without native work, then decode its manifest stdout. Public15360-byte bound differs from the duplicated SDK envelope. summary reports ON/OFF counts and ON-only pitch/velocity ranges for observed lanes, optionally in a half-open nanosecond interval; summary requires offset0/length4096, page requires bounds absent. It still computes the whole schedule. Empty selections do not establish generator inactivity or audibility. Read reference(section='Session generators and live lanes') for timing and capture limits."""
        selection = {}
        if detail != "page":
            selection["detail"] = detail
        if start_ns is not None:
            selection["start_ns"] = start_ns
        if end_ns is not None:
            selection["end_ns"] = end_ns
        return fm.gen_events(files, text=text, harness=harness, offset=offset, length=length,
                             expected_manifest_sha256=expected_manifest_sha256, **selection,
                             receipt_dir=receipt_dir, receipt_parent=receipt_parent,
                             receipt_purpose=receipt_purpose)

    @tool
    def render(files: list[str] | None = None, out: str | None = None, wav: bool = False,
               log: str | None = None, parent_record: str | None = None,
               note_text: str = "", target: list[str] | None = None,
               text: str | None = None, receipt_dir: str | None = None,
               receipt_parent: str | None = None, receipt_purpose: str = "",
               detail: Literal["compact", "full"] = "full") -> dict[str, Any]:
        """Render exactly one of files or complete text. out/log are directories; log appends a take. parent_record is a record SHA256 already in that same log, not a path; omitted means no parent. Targets use KEY=V or KEY=LO..HI. Explicit compact bounds the outcome and supplies a record artifact reader; optional receipts retain its exact full result. Full remains the legacy default. wav=false synthesizes and validates without creating a WAV."""
        return fm.render(files, harness=harness, out=out, wav=wav, log=log,
                         parent_record=parent_record, note_text=note_text, target=target, text=text,
                         receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose,
                         detail=detail)

    @tool
    def compose(log: str, receipt_dir: str, score: dict[str, Any] | None = None,
                source_ref: dict[str, Any] | None = None,
                parent_record: str | None = None, note_text: str = "",
                target: list[str] | None = None, detail: Literal["compact", "full"] = "compact",
                receipt_parent: str | None = None, receipt_purpose: str = "") -> dict[str, Any]:
        """Compose exactly one inline score or retained source_ref with rational quarter times, explicit streams/voices and native declaration text. Version1 has literal events; version2 adds flat phrases and finite repeats. Version3 adds explicit phrase transforms, literal chords, sampled controllers and named regions. No build, WAV, levelling or allocation. Requires explicit take-log and receipt directories (may be the same). Compact preserves actual discovery/candidate/parent calls and publication state; complete frozen source, lowered TL and maps are retained in either detail mode. Read full_result_sha256 with artifact_read(kind='receipt'); add receipt_dir as directory to receipt.source_artifact to recover source or reuse it directly as source_ref. Native owns declaration/coupled validation. First expected hash is unchecked; failed requested targets still retain a valid take. See reference(section='Musical-time scores') and reference(section='Editing and recovering scores')."""
        options = {"source_ref": source_ref} if source_ref is not None else {}
        return fm.compose(score, log=log, harness=harness, parent_record=parent_record,
                          note_text=note_text, target=target, detail=detail, receipt_dir=receipt_dir,
                          receipt_parent=receipt_parent, receipt_purpose=receipt_purpose, **options)

    @tool
    def score_inspect(receipt_dir: str, score: dict[str, Any] | None = None,
                      source_ref: dict[str, Any] | None = None, start: int | str = 0, end: int | str | None = None,
                      detail: Literal["compact", "full"] = "compact",
                      receipt_parent: str | None = None, receipt_purpose: str = "") -> dict[str, Any]:
        """Inspect exactly one inline score or retained source_ref. start/end are rational quarter coordinates. Report sections, voice uses and advisory gate conflicts from complete authored history; native bindings/generator activity/audibility remain unknown. Zero native work. Compact bounds delivery; full is unbounded. Required receipts retain complete tables and frozen source. Read full_result_sha256 with artifact_read(kind='receipt'); receipt.source_artifact gives the exact source reader relative to receipt_dir. See reference(section='Editing and recovering scores')."""
        return fm.score_inspect(score, source_ref=source_ref, start=start, end=end, detail=detail,
                                receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def score_revise(receipt_dir: str, expected_source_sha256: str, pointer: str, expected: Any, value: Any,
                     score: dict[str, Any] | None = None, source_ref: dict[str, Any] | None = None,
                     detail: Literal["compact", "full"] = "compact",
                     receipt_parent: str | None = None, receipt_purpose: str = "") -> dict[str, Any]:
        """Replace one existing RFC6901 source pointer guarded by exact source SHA and expected old value. Arrays/root can be replaced explicitly. Definition edits fan out to every phrase use; one occurrence needs explicit source restructuring. Reports exact differences and clock/order effects, without guessing note identity or acoustic equivalence. Zero native work; no take is published. Use receipt.source_artifact (plus receipt_dir) as source_ref in compose for native validation and explicit parent_record lineage. Compact retains full source/diff through required receipts; full is unbounded."""
        return fm.score_revise(score, source_ref=source_ref, expected_source_sha256=expected_source_sha256,
                               pointer=pointer, expected=expected, value=value, detail=detail,
                               receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def measure_section(files: list[str] | None = None, text: str | None = None,
                        start_ns: int = 0, end_ns: int | None = None,
                        receipt_dir: str | None = None, receipt_parent: str | None = None,
                        receipt_purpose: str = "") -> dict[str, Any]:
        """Measure whole-mix final signed16 output in a half-open nanosecond section. Renders the complete session from frame0 to preserve effect history; creates/reads/deletes a full temporary WAV (no persistent WAV). Normally3 native children/2 render passes; section size does not cap CPU/RAM/disk. Ceil sample-time bounds intersect physical EOF explicitly; empty/out-of-record sections refuse. Exact peak/energy/floor milli-RMS and host dBFS per output component plus combined; no downmix, loudness, per-voice isolation or listening claim. Native checks apply to the full record; expected hash mismatch remains exit3. Opt into receipts to recover full evidence without rerendering."""
        return fm.measure_section(files, text=text, harness=harness, start_ns=start_ns, end_ns=end_ns,
                                  receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def measure_sections(sections: list[dict[str, Any]], files: list[str] | None = None,
                         text: str | None = None, detail: Literal["compact", "full"] = "compact",
                         receipt_dir: str | None = None, receipt_parent: str | None = None,
                         receipt_purpose: str = "") -> dict[str, Any]:
        """Measure1..32 ordered whole-mix final-PCM sections from one complete checked render pair. Each section contains only optional start_ns (default0) and end_ns (defaultnull=record end); uint64 bounds, overlaps/repeats allowed, order preserved. Exact peak, energy, integer RMS and selected PCM hash with full prior history. No regular audio file or full Python PCM buffer; native retains two full buffers. Admission512MiB finalPCM,120s aggregate child/capture budget plus bounded cleanup effort; no global RSS/disk/filesystem deadline guarantee. AmbiX reports component energy, not decoded ears/localization. Native checks remain full-record and mismatchexit3. Compact omits bulk detail explicitly; receipt_dir retains complete evidence for verified recovery without rerendering."""
        return fm.measure_sections(files, text=text, harness=harness, sections=sections, detail=detail,
                                   receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def measure_voice_sections(voices: list[int], sections: list[dict[str, Any]],
                               files: list[str] | None = None, text: str | None = None,
                               detail: Literal["compact", "full"] = "compact",
                               receipt_dir: str | None = None, receipt_parent: str | None = None,
                               receipt_purpose: str = "") -> dict[str, Any]:
        """Measure1..64 distinct native dry voices in1..32 ordered sections with complete prior history. Raw signed32 Q2.30 is after voice synthesis/filter/amp but before crusher, sends, pan, pinna or master; unity is2^30, not conventional PCM32 amplitude. Normally3 native children and3 synthesis passes: two unchanged final-mix checks plus one dry rerender, whose runtime twin is unchecked. All declared voices are transported even when selecting one. No audio file/full Python PCM; two full native final16 buffers remain. Each representation capped512MiB, cooperative120s child/capture deadline plus bounded cleanup effort; no total RSS/lifetime guarantee. Exact integer peak/energy/RMS and per-voice hashes; no group, image, loudness or listening claim. Separate completion flags preserve earlier evidence on failure. Receipts recover full text/statistics after restart, not unretained dry audio."""
        return fm.measure_voice_sections(files, text=text, harness=harness, voices=voices,
            sections=sections, detail=detail, receipt_dir=receipt_dir,
            receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def describe(source: str, measure: bool = False, note: int | None = None,
                 out: str | None = None, detail: Literal["compact", "summary", "full", "page", "windows"] = "compact",
                 offset: int = 0, length: int = 4096,
                 expected_result_sha256: str | None = None, start_ns: int = 0,
                 end_ns: int | None = None, window_offset: int = 0, window_limit: int = 8,
                 receipt_dir: str | None = None, receipt_parent: str | None = None,
                 receipt_purpose: str = "") -> dict[str, Any]:
        """Describe a record, recipe or piece directory; note is mHz. Compact omits bulk data explicitly. Page recomputes full data; follow next_offset with expected_result_sha256. out is an artifact directory, not a JSON filename, and requires explicit summary/full."""
        return fm.describe(source, harness=harness, measure=measure, note=note, out=out, detail=detail,
                           offset=offset, length=length, expected_result_sha256=expected_result_sha256,
                           start_ns=start_ns, end_ns=end_ns, window_offset=window_offset, window_limit=window_limit,
                           receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def compare(a: str, b: str, measure: bool = False, note: int | None = None,
                target: list[str] | None = None,
                detail: Literal["compact", "summary", "full", "page", "windows"] = "compact",
                offset: int = 0, length: int = 4096,
                expected_result_sha256: str | None = None, start_ns: int = 0,
                end_ns: int | None = None, window_offset: int = 0, window_limit: int = 8,
                receipt_dir: str | None = None, receipt_parent: str | None = None,
                receipt_purpose: str = "") -> dict[str, Any]:
        """Compare timelines/WAVs; deltas are [a,b,b-a]. Targets address numeric dictionary paths, e.g. a.clipped=0 and b.clipped=0 for rendered timelines. WAV operands do not expose native clipping counts; those and bare clipped stay unchecked. Compact preserves computed verdicts/counts and marks omissions. Page recomputes full data; follow next_offset with expected_result_sha256. Legacy summary/full are unbounded."""
        return fm.compare(a, b, harness=harness, measure=measure, note=note, target=target, detail=detail,
                          offset=offset, length=length, expected_result_sha256=expected_result_sha256,
                          start_ns=start_ns, end_ns=end_ns, window_offset=window_offset, window_limit=window_limit,
                          receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def log_show(directory: str, kind: Literal["takes", "operations"] = "takes",
                 offset: int = 0, limit: int = 20,
                 expected_index_sha256: str | None = None,
                 detail: Literal["compact", "full"] = "compact") -> dict[str, Any]:
        """Verify the entire log before paging takes or operations with an index SHA guard. Compact takes omit notes/diffs and inline TSV; artifact_read(kind='take_log') retrieves exact TSV bytes. Full takes are unbounded. Operations remain compact; pending means outcome unknown."""
        return fm.log_show(directory, kind=kind, offset=offset, limit=limit,
                           expected_index_sha256=expected_index_sha256, detail=detail)

    @tool
    def log_replay(directory: str, receipt_dir: str | None = None,
                   receipt_parent: str | None = None, receipt_purpose: str = "",
                   detail: Literal["compact", "full"] = "full") -> dict[str, Any]:
        """Verify stored artifacts and replay every accepted take separately. Full remains the unbounded default. Explicit compact preserves actual status and returned-row counts; aborted-prefix counts can be unknown. Opt into receipt_dir for exact full evidence through artifact_read, without replaying again. No new takes or WAVs are published."""
        options = dict(detail="compact") if detail == "compact" else {}
        return fm.log_replay(directory, harness=harness, receipt_dir=receipt_dir,
                             receipt_parent=receipt_parent, receipt_purpose=receipt_purpose, **options)

    @tool
    def artifact_read(directory: str, kind: Literal["record", "wav", "png", "midi", "sfz", "zip", "receipt", "take_log", "session_manifest", "score", "authored_tl", "generator_manifest"], digest: str,
                      offset: int = 0, length: int = 8192,
                      filename: str | None = None,
                      encoding: Literal["base64", "utf8"] = "base64") -> dict[str, Any]:
        """Verify the entire artifact before returning bytes. encoding='utf8' reads record/sfz/receipt/take_log/session_manifest/score/authored_tl/generator_manifest text; follow next_offset to EOF with the same digest and verify assembled bytes. Pages may split JSON tokens. UTF8 length is1..8192 for every supported text kind. Base64 remains default and allows1..1048576 bytes except authored_tl/generator_manifest, whose maximum is8192. score requires filename=<parent receipt SHA>.json and digest=<canonical source SHA>; verifies parent and projected source, without native work. authored_tl and generator_manifest similarly select exact text from a recognized full receipt result; both encodings allow at most8192 bytes/page, parent64MiB and selected16MiB, with a15360-byte public reply bound. Add directory to source_artifact or text_artifact locators (omit their size when calling this reader). A retained authored TL may follow a native refusal; a manifest view requires complete capture. Neither authenticates journal membership. PNG/MIDI/SFZ/ZIP require one .png/.mid/.sfz/.zip filename; record/WAV optionally accept one .tl/.wav basename, otherwise use digest-addressed log paths. Interchange delivery locators are ready reader arguments; ZIP transfers the complete bundle, while reading a directory SFZ alone omits its WAV dependencies. Every page re-verifies the complete parent/file. Manifest retrieval does not verify its bundle; use session_verify or interchange_verify for its respective format."""
        return fm.artifact_read(directory, kind=kind, digest=digest, offset=offset, length=length,
                                filename=filename, encoding=encoding)

    @tool
    def session_export(sources: list[str], out: str) -> dict[str, Any]:
        """Snapshot1..32 explicit receipt/take roots into a new verified bundle. Preserve retained indexes, JSON, TL and referenced WAV bytes; external inputs and native binaries are excluded. No rendering/replay. Output must not exist; discover original-root relocation through the manifest artifact."""
        return fm.session_export(sources, out)

    @tool
    def session_verify(directory: str, expected_manifest_sha256: str | None = None) -> dict[str, Any]:
        """Verify the manifest, all declared artifacts and their journal/take links after moving a bundle. No writes, native execution or replay. Use manifest root mappings with existing explicit log_replay and a selected native runtime."""
        return fm.session_verify(directory, expected_manifest_sha256=expected_manifest_sha256)

    @tool
    def midi_import(source: str, options: list[str] | None = None, out: str | None = None,
                    dry_run: bool = True, receipt_dir: str | None = None,
                    receipt_parent: str | None = None, receipt_purpose: str = "") -> dict[str, Any]:
        """Inspect Standard MIDI File0/1 with existing conversion policies. options is argv tokens, not a shell command; discover them via reference(source='midi-options'). Default dry_run requires out omitted and performs no writes/build/render. Explicit dry_run=false requires a new out directory and uses the configured built harness. Compact output identifies canonical TL/WAV; optional receipts retain full diagnostics/dependencies without repeating conversion."""
        return fm.midi_import(source, options=options, out=out, dry_run=dry_run,
                              harness=harness if not dry_run else None, receipt_dir=receipt_dir,
                              receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def interchange_inspect(source: str | list[str]) -> dict[str, Any]:
        """Native-validate a complete local TL path, recipe or ordered input paths and inventory WAV/MIDI/SFZ candidates. This renders; candidate=true does not guarantee export. Keep one complete TL as the original. No retained files, downloads, build or device sending. Use interchange_export without out for the actual conversion/loss report."""
        return interchange.inspect(source, harness=harness)

    @tool
    @interchange.public
    def interchange_export(source: str | list[str], profile: Literal["wav", "midi", "sfz", "sfz-bank"],
                           out: str | None = None, container: Literal["directory", "zip"] = "directory",
                           channels: list[str] | None = None, ppqn: int = 960, tempo_us: int = 500000,
                           midi_format: int = 1, bend_range: int = 2, accept_loss: bool = False,
                           key: int | None = None, patch: str | None = None, notes: list[int] | None = None,
                           velocities: list[int] | None = None, hold_ms: int | None = None,
                           tail_ms: int | None = None, max_samples: int | None = None,
                           receiver_profile: Literal["sfizz-ram"] | None = None,
                           detail: Literal["compact", "full"] = "compact") -> dict[str, Any]:
        """Export existing local TL/recipe/input paths through the native renderer. Omit out for an actual dry-run conversion report; explicit out creates a NEW directory or ZIP, never overwrites, and means local files only. WAV retains native mono/stereo PCM. MIDI0/1 needs unique channels=['0=1','1=2'] (VOICE=CHANNEL1..16), transfers notes/timing, loses synth/FX/space, and refuses generators. sfz is a whole-song one-shot with required key0..127. sfz-bank requires patch, notes, velocities, hold_ms, tail_ms and max_samples; it samples fixed mono notes and drops song/FX/space. Materializing MIDI/sfz-bank requires accept_loss=true after reviewing losses. Default compact keeps loss categories/counts/max errors/output paths but omits per-event and per-cell arrays with counts; full returns the complete report. Published manifest.json always retains it. Delivery locators work directly with artifact_read; choose a .zip output for complete bundle transfer through MCP. No downloads/build/device sending or receipt arguments; use interchange_verify for bundled bytes."""
        result = interchange.export(source, profile=profile, harness=harness, out=out, container=container,
            channels=None if channels is None else interchange.channel_map(channels), ppqn=ppqn,
            tempo_us=tempo_us, midi_format=midi_format, bend_range=bend_range, accept_loss=accept_loss,
            key=key, patch=patch, notes=notes, velocities=velocities, hold_ms=hold_ms, tail_ms=tail_ms,
            max_samples=max_samples, receiver_profile=receiver_profile)
        return interchange_reply(result, detail)

    @tool
    def interchange_verify(bundle: str, expected_manifest_sha256: str | None = None) -> dict[str, Any]:
        """Verify a complete directory or ZIP export after copying it, optionally pinning its manifest SHA256. Checks required members, bytes and format dependencies without native rendering or extraction. No writes, downloads or operation receipts; success does not certify a DAW, sampler or hardware receiver."""
        return interchange.verify(bundle, expected_manifest_sha256=expected_manifest_sha256)

    @tool
    def transcribe(source: str, out: str, options: list[str] | None = None,
                   native_budget: int | None = None, receipt_dir: str | None = None, receipt_parent: str | None = None,
                   receipt_purpose: str = "") -> dict[str, Any]:
        """Approximate separated mono PCM16 WAV at44.1/48kHz using the accepted transcriber. options contains existing argv tokens; discover reference(source='transcribe-options'). Requires a new out directory and uses the configured built harness in an isolated child. No build, dry run or arbitrary-song recovery guarantee. Optional native_budget caps attempted CONFIG/play calls. Check complete: a normal budget stop has exit0 but complete=false and publishes no partial score. Completed output includes a bounded note preview, editable/canonical TL, reconstruction, exact replay and unaligned source-error scores. Opt into receipt_dir before work to retrieve the complete report without repeating searches; compact output includes direct canonical TL/WAV artifact arguments. Actual native calls and template requests are distinct."""
        return fm.transcribe(source, out, options=options, native_budget=native_budget, harness=harness,
                             receipt_dir=receipt_dir, receipt_parent=receipt_parent,
                             receipt_purpose=receipt_purpose)

    @tool
    def catalog_build(out: str, records: list[str] | None = None,
                      batch_size: int = 16) -> dict[str, Any]:
        """Measure patches from explicit local TL records into a descriptor table. Installed packages require records; songs are not bundled or downloaded. Omitting records selects the source checkout's full corpus on Linux."""
        return fm.catalog_build(out=out, records=records, batch_size=batch_size, harness=harness)

    @tool
    def search(ranges: list[str] | None = None, tags: list[str] | None = None,
               tsv: list[str] | None = None, offset: int = 0, limit: int = 20,
               receipt_dir: str | None = None, receipt_parent: str | None = None,
               receipt_purpose: str = "") -> dict[str, Any]:
        """Search existing descriptor tables using KEY=LO..HI ranges and optional recipe tags."""
        return fm.search(ranges or [], tags=tags or [], tsv=tsv, offset=offset, limit=limit,
                         receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def suggest(target: list[str], source: str | None = None, patch: str | None = None,
                probe: bool = False, among: str | None = None, note: int | None = None,
                tsv: list[str] | None = None, offset: int = 0, limit: int = 20,
                detail: Literal["compact", "full"] = "compact",
                receipt_dir: str | None = None, receipt_parent: str | None = None,
                receipt_purpose: str = "") -> dict[str, Any]:
        """Rank measured patches; probe only when requested. Compact omits bulk candidates; receipt_dir retains the complete result for artifact_read without repeating renders. Full is unbounded and executes a new operation."""
        return fm.suggest(target, source=source, patch=patch, probe=probe, among=among,
                          note=note, tsv=tsv, harness=harness, offset=offset, limit=limit, detail=detail,
                          receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def probe_plan(source: str, patch: str | None = None, note: int | None = None,
                   parent_record: str | None = None, receipt_dir: str | None = None,
                   receipt_parent: str | None = None, receipt_purpose: str = "") -> dict[str, Any]:
        """Count eligible probe candidates and upper render bounds before suggest/seek. Source validation renders; planning reports its actual cost. No candidates, measurements, publication, reuse or convergence promise. parent_record is unverified cost context only."""
        return fm.probe_plan(source, patch=patch, note=note, parent_record=parent_record,
                             harness=harness, receipt_dir=receipt_dir,
                             receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    @tool
    def seek(target: list[str], source: str, log: str, budget: int,
             tol: list[str] | None = None, patch: str | None = None,
             note: int | None = None, tsv: list[str] | None = None,
             parent_record: str | None = None, detail: Literal["compact", "full"] = "compact",
             receipt_dir: str | None = None, receipt_parent: str | None = None,
             receipt_purpose: str = "") -> dict[str, Any]:
        """Search within an explicit render budget; retain independently verified improvements. Compact keeps outcomes; receipt_dir retains full history without repeating search. A resumed call starts a new search, never a hidden checkpoint."""
        return fm.seek(target, source=source, log=log, budget=budget, tol=tol,
                       patch=patch, note=note, tsv=tsv, harness=harness,
                       parent_record=parent_record, detail=detail,
                       receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    # Expose optional operations only when supported by the selected synth SDK.
    if hasattr(fm, "check_patch"):
        @tool
        def check_patch(files: list[str] | None = None, patch_name: str | None = None,
                        fs: int = 48000, text: str | None = None,
                        receipt_dir: str | None = None, receipt_parent: str | None = None,
                        receipt_purpose: str = "", detail: Literal["compact", "full"] = "full") -> dict[str, Any]:
            """Check exactly one of files or timeline text; patch_name wraps bare patch lines in a silent context. No WAV or retained record is created. Explicit compact bounds the outcome; receipt_dir on this call retains the exact full result for later verified reads without rerendering. Full remains the legacy default."""
            return fm.check_patch(files, harness=harness, patch_name=patch_name, fs=fs, text=text,
                                  receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose,
                                  detail=detail)

    if hasattr(fm, "analyze"):
        @tool
        def analyze(wav: str, mode: Literal["overview", "loudness", "spectro"] = "overview",
                    out: str | None = None,
                    detail: Literal["compact", "full"] = "compact",
                    receipt_dir: str | None = None, receipt_parent: str | None = None,
                    receipt_purpose: str = "") -> dict[str, Any]:
            """Return analyzer metrics or spectrogram metadata. Spectro requires out=.png; creates its parent. It previews inferred bars1-4, not necessarily the whole WAV; stdout states the span. Compact supplies artifact_read arguments; full includes unbounded PNG base64. Neither proves listening/viewing."""
            return fm.analyze(wav, mode=mode, out=out, detail=detail,
                              receipt_dir=receipt_dir, receipt_parent=receipt_parent, receipt_purpose=receipt_purpose)

    if hasattr(fm, "find_patch"):
        @tool
        def find_patch(query: list[str], tags: list[str] | None = None,
                       tsv: list[str] | None = None,
                       mode: Literal["search", "describe"] = "search") -> dict[str, Any]:
            """Search existing descriptors, or describe exactly one recipe in query. Use separate describe-mode calls for multiple recipes; reference discovers the guide."""
            return fm.find_patch(query, tags=tags, tsv=tsv, mode=mode, harness=harness)

    if hasattr(fm, "load_patch"):
        @tool
        def load_patch(name: str, patch: str = "patch", detail: Literal["full", "page"] = "full",
                       offset: int = 0, length: int = 4096,
                       expected_sha256: str | None = None) -> dict[str, Any]:
            """Expand one named recipe; reference(source='recipes') only accepts index/all. Full is unchanged and unbounded. Explicit page returns UTF8 bytes (default4096,max8192); follow next_offset with expected_sha256, assemble to EOF and verify sha256. Pages re-expand without native work/WAV/storage. The15360-byte escaped public JSON bound excludes the duplicated SDK envelope; request smaller pages if needed. Text is not native-validated."""
            options = {} if (detail == "full" and offset == 0 and length == 4096
                             and expected_sha256 is None) else dict(
                detail=detail, offset=offset, length=length, expected_sha256=expected_sha256)
            return fm.load_patch(name, patch=patch, **options)

    def file_resource(uri, relative, mime):
        def read() -> str:
            return (fm.ROOT / relative).read_bytes().decode("utf-8")
        server.resource(uri, name=relative, mime_type=mime)(read)

    file_resource("fm://instrument", "docs/INSTRUMENT.md", "text/markdown")
    file_resource("fm://recipes", "data/recipes.txt", "text/plain")
    file_resource("fm://descriptors", "data/descriptors.tsv", "text/tab-separated-values")
    if (fm.ROOT / "data/catalog.tsv").is_file():
        file_resource("fm://catalog", "data/catalog.tsv", "text/tab-separated-values")

    def native_resource(uri, option):
        def read() -> str:
            native = fm.Native(harness)
            result = native.run([option])
            fm.require(result.returncode == 0, result.stderr, result.returncode)
            return result.stdout
        server.resource(uri, name=option, mime_type="text/plain")(read)

    native_resource("fm://params", "--params")
    native_resource("fm://gen-params", "--gen-params")
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--synth-root", type=Path)
    args = parser.parse_args(argv)
    try:
        create_server(args.harness, synth_root=args.synth_root).run(transport="stdio")
    except ImportError as error:
        print("Install requirements/core.txt in a host virtual environment: " + str(error), file=sys.stderr)
        return 5
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
