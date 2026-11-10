#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Exercise the real stdio server and selected synth in a disposable workspace."""
import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import wave

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parents[1]
TOOLS = set("params reference gen_events render compose score_inspect score_revise "
            "measure_section measure_sections measure_voice_sections describe compare "
            "log_show log_replay artifact_read session_export session_verify midi_import "
            "interchange_inspect interchange_export interchange_verify transcribe catalog_build "
            "search suggest probe_plan seek check_patch analyze find_patch load_patch".split())


def run(*command, **kwargs):
    result = subprocess.run(list(map(str, command)), capture_output=True, text=True, **kwargs)
    assert result.returncode == 0, (command, result.returncode, result.stdout[-2000:], result.stderr[-2000:])
    return result.stdout


async def call(client, name, arguments, *, ok=True):
    reply = await client.call_tool(name, arguments)
    value = reply.structured_content
    assert isinstance(value, dict) and value.get("ok") is ok, (name, value)
    assert bool(reply.is_error) is (not ok), (name, reply.is_error)
    return value


async def retrieve(client, locator, *, text=False):
    offset, chunks = 0, []
    while True:
        page = await call(client, "artifact_read", {**locator, "offset": offset,
                          "length": 257 if text else 32768, "encoding": "utf8" if text else "base64"})
        assert page["offset"] == offset and page["sha256"] == locator["digest"]
        data = page["text"].encode("utf8") if text else base64.b64decode(page["data_base64"], validate=True)
        chunks.append(data)
        offset += len(data)
        if page["eof"]:
            assert offset == page["size"]
            break
        assert data and len(chunks) < 4096
    data = b"".join(chunks)
    assert hashlib.sha256(data).hexdigest() == locator["digest"]
    return data


async def check(spec, workspace, harness, launcher, env):
    async with Client(spec, mode="legacy", cache=None, read_timeout_seconds=120) as client:
        assert client.server_info.name == "jottune-mcp"
        expected_version = json.loads(launcher.resolve().parents[1].joinpath("package.json").read_text())["version"]
        assert client.server_info.version == expected_version
        inventory = await client.list_tools()
        assert {tool.name for tool in inventory.tools} == TOOLS
        assert all(tool.input_schema.get("additionalProperties") is False for tool in inventory.tools)
        assert all("oneOf" not in tool.input_schema for tool in inventory.tools)
        assert len(json.dumps([tool.model_dump() for tool in inventory.tools])) < 65536
        assert all(tool.title for tool in inventory.tools)
        with tempfile.TemporaryDirectory(prefix="jottune-outside-") as outside:
            private = Path(outside) / "private.tl"
            private.write_text("PRIVATE_SENTINEL=not-a-song\n")
            (workspace / "linked.tl").symlink_to(private)
            (workspace / "linked-dir").symlink_to(outside, target_is_directory=True)
            refusal_entries = set(workspace.iterdir())
            for tool, arguments in [
                ("interchange_export", {"source": "bell", "profile": "wav", "out": "~/jottune-refused-export.zip", "container": "zip"}),
                ("interchange_verify", {"bundle": "~/jottune-refused-export.zip"}),
                ("interchange_verify", {"bundle": "~root/jottune-refused-export.zip"}),
                ("describe", {"source": str(private)}),
                ("describe", {"source": "linked.tl"}),
                ("compare", {"a": str(private), "b": "bell"}),
                ("gen_events", {"files": [str(private)]}),
                ("artifact_read", {"directory": outside, "kind": "record", "filename": "private.tl", "digest": hashlib.sha256(private.read_bytes()).hexdigest()}),
                ("artifact_read", {"directory": ".", "kind": "record", "filename": "linked.tl", "digest": hashlib.sha256(private.read_bytes()).hexdigest()}),
                ("catalog_build", {"out": str(private), "records": []}),
                ("analyze", {"wav": str(private), "mode": "spectro", "out": "plot.png"}),
                ("midi_import", {"source": "song.mid", "options": ["--tuning", str(private)]}),
            ]:
                refused = await call(client, tool, arguments, ok=False)
                assert "PRIVATE_SENTINEL" not in json.dumps(refused)
                if any(isinstance(value, str) and value.startswith("~") for value in arguments.values()):
                    assert set(workspace.iterdir()) == refusal_entries, "Refused home alias created a lock or output"
            assert private.read_text() == "PRIVATE_SENTINEL=not-a-song\n"
            (workspace / "linked.tl").unlink()
            (workspace / "linked-dir").unlink()
        parameters = await call(client, "params", {})
        assert parameters["next_offset"] is not None and len(json.dumps(parameters)) < 16384
        await call(client, "artifact_read", {"directory": ".", "kind": "wav", "digest": "a" * 64, "length": 32769}, ok=False)
        guide = await call(client, "reference", {"section": "Musical-time scores", "length": 8192})
        score = json.loads(re.search(r"```json\s*(.*?)\s*```", guide["text"], re.S).group(1))
        score["name"] = "MCP integration"
        score["metadata"] = {"title":"Portable MCP integration","made_by":"agent","model":{"id":"fixture/v1","source":"client-reported"}}
        first = await call(client, "compose", {"score": score, "log": "takes", "receipt_dir": "operations"})
        assert first["published"] and first["receipt"]["state"] == "complete"
        source = {"directory": "operations", **first["receipt"]["source_artifact"]}
        assert json.loads(await retrieve(client, source, text=True)) == score
        edit = {"source_ref": source, "expected_source_sha256": first["source_sha256"],
                "pointer": "/events/0/velocity_q15", "expected": score["events"][0]["velocity_q15"],
                "value": 10000, "receipt_dir": "operations"}
        await call(client, "score_revise", {**edit, "expected": 1}, ok=False)
        revised = await call(client, "score_revise", edit)
        revised_source = {"directory": "operations", **revised["receipt"]["source_artifact"]}
        second = await call(client, "compose", {"source_ref": revised_source, "log": "takes",
                            "receipt_dir": "operations", "parent_record": first["record_sha256"]})
        assert second["record_sha256"] != first["record_sha256"]
        canonical = await retrieve(client, second["artifact"], text=True)
        assert canonical.startswith(b"# jottune-tl 1 1\n") and b"Portable MCP integration" in canonical
        assert second["document_sha256"] == hashlib.sha256(canonical).hexdigest()
        assert second["record_sha256"] != second["document_sha256"]
        rendered = await call(client, "render", {"text": canonical.decode("utf8"), "log": "audio",
                              "wav": True, "detail": "compact"})
        assert rendered["checks_pass"] and rendered["record_sha256"] == second["record_sha256"]
        delivered = workspace / "delivered"
        delivered.mkdir()
        (delivered / "song.tl").write_bytes(await retrieve(client, rendered["artifact"], text=True))
        wav = await retrieve(client, {"directory": "audio", "kind": "wav", "digest": rendered["wav_sha256"]})
        (delivered / "song.wav").write_bytes(wav)
        with wave.open(io.BytesIO(wav)) as audio:
            assert audio.getframerate() == score["fs"] and audio.getnframes() > 0
            assert any(audio.readframes(audio.getnframes()))
        assert "match=yes" in run(harness, "--play", delivered / "song.tl", env=env)
        run("node", launcher, "svg", "delivered/song.tl", "delivered/song.svg", "--workspace", workspace,
            "--harness", harness, env=env)
        assert "<svg" in (delivered / "song.svg").read_text()
        exported_audio = await call(client, "interchange_export", {"source": "delivered/song.tl",
                                    "profile": "wav", "container": "zip", "out": "delivered/audio.zip"})
        assert exported_audio["published"]
        await call(client, "interchange_verify", {"bundle": "delivered/audio.zip",
                   "expected_manifest_sha256": exported_audio["bundle"]["manifest_sha256"]})
        assert await retrieve(client, exported_audio["delivery"]["archive"]) == (delivered / "audio.zip").read_bytes()
        bundle = await call(client, "session_export", {"sources": ["operations", "takes", "audio"], "out": "bundle"})
        manifest = json.loads(await retrieve(client, bundle["artifact"], text=True))
        relocated = workspace / "relocated"
        (workspace / "bundle").rename(relocated)
        for name in ("operations", "takes", "audio"):
            shutil.rmtree(workspace / name)
    async with Client(spec, mode="legacy", cache=None, read_timeout_seconds=120) as client:
        await call(client, "session_verify", {"directory": "relocated", "expected_manifest_sha256": bundle["manifest_sha256"]})
        member = next(item for item in manifest["files"] if item["path"].endswith(".tl"))
        (relocated / member["path"]).write_text("corrupt\n")
        await call(client, "session_verify", {"directory": "relocated", "expected_manifest_sha256": bundle["manifest_sha256"]}, ok=False)
    return len(inventory.tools)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synth-root", type=Path, required=True)
    parser.add_argument("--harness", type=Path)
    parser.add_argument("--launcher", type=Path, default=ROOT / "bin/jottune-mcp.mjs")
    args = parser.parse_args()
    sdk = args.synth_root.resolve()
    harness = (args.harness or sdk / "build/s12e10p1v1/harness").resolve()
    with tempfile.TemporaryDirectory(prefix="jottune-mcp-integration-") as directory:
        workspace = Path(directory).resolve()
        env = {**os.environ, "JOTTUNE_SYNTH_ROOT": str(sdk), "JOTTUNE_PYTHON": sys.executable,
               "PYTHONDONTWRITEBYTECODE": "1", "MPLCONFIGDIR": str(workspace / "matplotlib")}
        config = json.loads(run("node", args.launcher, "config", "--synth-root", sdk,
                                "--harness", harness, "--workspace", workspace, env=env))["mcpServers"]["jottune"]
        spec = StdioServerParameters(command=config["command"], args=config["args"], env=env, cwd=str(workspace))
        count = asyncio.run(check(spec, workspace, harness, args.launcher, env))
        print(f"MCP_INTEGRATION {count} tools; compose/revise/render/TL/WAV/SVG/ZIP/recovery passed")


if __name__ == "__main__":
    main()
