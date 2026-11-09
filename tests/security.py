#!/usr/bin/env python3
# Copyright (c) 2026 Nenad Micic
# SPDX-License-Identifier: Apache-2.0
"""Workspace confinement, safe output publication and operation cancellation."""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT.parent / "jottune-synth"
sys.path.insert(0, str(ROOT / "server"))
from guard import Workspace, WorkspaceError


class WorkspaceTests(unittest.TestCase):
    def test_paths_and_link_targets_are_confined_before_reading(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            guard = Workspace(root)
            private = Path(outside) / "private.tl"
            private.write_text("PRIVATE_SENTINEL=value")
            (root / "link.tl").symlink_to(private)
            for name in (str(private), "link.tl", "../private.tl"):
                with self.assertRaises(WorkspaceError):
                    guard.validate("render", {"files": [name]})
            guard.validate("render", {"text": "a song", "wav": True, "out": "new"})
            guard.validate("describe", {"source": "bell"})
            for arguments in ({"a": str(private), "b": "bell"}, {"a": "bell", "b": str(private)}):
                with self.assertRaises(WorkspaceError):
                    guard.validate("compare", arguments)
            self.assertEqual(private.read_text(), "PRIVATE_SENTINEL=value")

    def test_home_aliases_are_refused_before_adapter_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = Workspace(Path(directory))
            with patch.object(Path, "expanduser", side_effect=AssertionError("must not expand a home alias")) as expansion:
                for value in ("~/new.zip", "~root/new.zip", "~", "~/../new.zip"):
                    for name, field in (("interchange_export", "out"), ("interchange_verify", "bundle")):
                        with self.assertRaises(WorkspaceError):
                            guard.validate(name, {field: value})
                expansion.assert_not_called()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_analysis_and_catalog_outputs_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "keep.png"
            target.write_bytes(b"preserve")
            for name in ("analyze", "catalog_build"):
                with self.assertRaises(WorkspaceError):
                    Workspace(root).validate(name, {"out": "keep.png"})
            self.assertEqual(target.read_bytes(), b"preserve")

    def test_analyzer_failure_does_not_publish_or_overwrite(self):
        sys.path.insert(0, str(SDK / "tools"))
        import fm
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source.wav", root / "new.png"
            source.write_bytes(b"keep-source")
            def invalid(args):
                Path(args[-1]).write_bytes(b"invalid PNG")
                return {"ok": True, "exit": 0}
            with patch.object(fm, "existing_command", invalid):
                self.assertFalse(fm.analyze(source, mode="spectro", out=target)["ok"])
            self.assertFalse(target.exists())
            self.assertEqual(source.read_bytes(), b"keep-source")
            target.symlink_to(source)
            self.assertFalse(fm.analyze(source, mode="spectro", out=target)["ok"])
            self.assertEqual(source.read_bytes(), b"keep-source")

    def test_catalog_refuses_an_existing_input_as_output(self):
        sys.path.insert(0, str(SDK / "tools"))
        import fm
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "song.tl"
            source.write_text("keep-source")
            result = fm.catalog_build(out=source, records=[source])
            self.assertFalse(result["ok"])
            self.assertEqual(source.read_text(), "keep-source")

    def test_directory_inputs_refuse_linked_children_outside_the_workspace(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root, outside = Path(directory), Path(outside)
            guard = Workspace(root)
            (outside / "records").mkdir()
            for name in ("log.tsv", "manifest.json", "song.tl"):
                (outside / name).write_text("PRIVATE_SENTINEL\n")
            def calls(selected):
                return (("log_show", {"directory": selected}), ("session_verify", {"directory": selected}),
                        ("session_export", {"sources": [selected], "out": "bundle"}),
                        ("catalog_build", {"records": [selected], "out": "new.tsv"}))
            for index, child in enumerate(("log.tsv", "records", "manifest.json", "song.tl")):
                selected = root / ("linked" + str(index))
                selected.mkdir()
                (selected / child).symlink_to(outside / child)
                for name, arguments in calls(selected.name):
                    with self.assertRaises(WorkspaceError, msg=(child, name)):
                        guard.validate(name, arguments)
            # Links that stay inside, including a cycle, remain usable; their targets are walked too.
            real, alias = root / "real", root / "alias"
            (real / "records").mkdir(parents=True)
            (real / "log.tsv").write_text("kept")
            (real / "loop").symlink_to(real, target_is_directory=True)
            alias.mkdir()
            (alias / "records").symlink_to(real / "records", target_is_directory=True)
            for name, arguments in calls("alias"):
                guard.validate(name, arguments)
            (real / "records" / "escape.tl").symlink_to(outside / "song.tl")
            for name, arguments in calls("alias"):
                with self.assertRaises(WorkspaceError, msg=name):
                    guard.validate(name, arguments)

    def test_multiply_linked_files_are_refused(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            guard = Workspace(root)
            private = Path(outside) / "private.tl"
            private.write_text("PRIVATE_SENTINEL=value")
            (root / "logs").mkdir()
            try:
                os.link(private, root / "linked.tl")
                os.link(private, root / "logs" / "log.tsv")
            except OSError:
                self.skipTest("hard links need one filesystem")
            for name, arguments in (("render", {"files": ["linked.tl"]}), ("log_show", {"directory": "logs"}),
                                    ("artifact_read", {"directory": "logs", "kind": "take_log", "digest": "0" * 64})):
                with self.assertRaises(WorkspaceError, msg=name):
                    guard.validate(name, arguments)
            (root / "single.tl").write_text("kept")
            guard.validate("render", {"files": ["single.tl"]})

    def test_midi_option_prefixes_and_inline_values_are_confined(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory)
            guard = Workspace(root)
            private = str(Path(outside) / "private.txt")
            Path(private).write_text("1\n")
            (root / "tune.txt").write_text("1\n")
            for options in (["--tun", private], ["--tunin=" + private], ["--tuning", private],
                            ["--sca", private], ["--scal=" + private], ["--scala=tune.txt:" + private],
                            ["--t", private]):
                with self.assertRaises(WorkspaceError, msg=options):
                    guard.validate("midi_import", {"source": "song.mid", "options": options})
            guard.validate("midi_import", {"source": "song.mid",
                                           "options": ["--tuning", "tune.txt", "--scale", "1=major", "--", "x"]})

    def test_synth_take_log_reader_refuses_linked_children_outside_the_workspace(self):
        sys.path.insert(0, str(SDK / "tools"))
        import fm
        import hashlib
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root, outside = Path(directory).resolve(), Path(outside).resolve()
            record = b"PRIVATE_RECORD_SENTINEL\n"
            digest = hashlib.sha256(record).hexdigest()
            values = dict(version="1", take="1", hash="0123456789abcdef", record_sha256=digest, fs="48000",
                          utc="PRIVATE_UTC_SENTINEL", note="PRIVATE_NOTE_SENTINEL")
            log = "\t".join(fm.LOG_COLUMNS) + "\n" + "\t".join(values.get(key, "-") for key in fm.LOG_COLUMNS) + "\n"
            for base in (outside, root / "real"):
                (base / "records").mkdir(parents=True)
                (base / "records" / (digest + ".tl")).write_bytes(record)
                (base / "log.tsv").write_text(log)
            linked = root / "linked"
            linked.mkdir()
            (linked / "log.tsv").symlink_to(outside / "log.tsv")
            (linked / "records").symlink_to(outside / "records", target_is_directory=True)
            with patch.object(fm, "WORKSPACE_ROOT", root):
                self.assertTrue(fm.log_show(str(root / "real"), detail="full")["ok"])
                for detail in ("full", "compact"):
                    reply = fm.log_show(str(linked), detail=detail)
                    self.assertEqual((reply["ok"], reply["exit"]), (False, 2), reply)
                    self.assertNotIn("PRIVATE", json.dumps(reply))

    def test_midi_child_refuses_option_files_outside_the_workspace(self):
        sys.path.insert(0, str(SDK / "tools"))
        import fm
        track = bytes.fromhex("00903c648360803c0000ff2f00")
        midi = (b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big")
                + (480).to_bytes(2, "big") + b"MTrk" + len(track).to_bytes(4, "big") + track)
        table = "".join(str(round(440000 * 2 ** ((key - 69) / 12))) + "\n" for key in range(128))
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory).resolve()
            (root / "song.mid").write_bytes(midi)
            (root / "tuning.txt").write_text(table)
            private = Path(outside).resolve() / "private-tuning.txt"
            private.write_text(table)
            with patch.dict(os.environ, {"JOTTUNE_MCP_WORKSPACE": str(root)}):
                accepted = fm.midi_import(str(root / "song.mid"),
                                          options=["--map", "1=epiano", "--tuning", str(root / "tuning.txt")])
                self.assertTrue(accepted["ok"], accepted)
                for options in (["--tun=" + str(private)], ["--tuning", str(private)], ["--scala", str(private)]):
                    reply = fm.midi_import(str(root / "song.mid"), options=["--map", "1=epiano", *options])
                    self.assertEqual((reply["ok"], reply["exit"]), (False, 2), (options, reply))
                    self.assertNotIn("private-tuning", json.dumps(reply))

    def test_section_measurements_apply_the_render_profile_before_synthesis(self):
        sys.path.insert(0, str(SDK / "tools"))
        import fm
        import time
        native = SDK / "build/s12e10p1v1/harness"
        if not native.is_file():
            self.skipTest("Build the native harness first")
        golden = (SDK / "tests/golden/48000/sine.tl").read_text().splitlines()
        hour = "".join(line.replace("length_ns 2000000000", "length_ns 3600000000000") + "\n"
                       for line in golden if not line.startswith("hash "))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "short.tl").write_text("\n".join(golden) + "\n")
            (root / "hour.tl").write_text(hour)
            sections = [dict(start_ns=0, end_ns=1000000000)]
            with patch.object(fm, "WORKSPACE_ROOT", root):
                for name, extra in (("measure_sections", {}), ("measure_voice_sections", dict(voices=[0]))):
                    accepted = getattr(fm, name)([str(root / "short.tl")], harness=str(native),
                                                 sections=sections, **extra)
                    self.assertTrue(accepted["ok"], (name, str(accepted)[:600]))
                    started = time.monotonic()
                    reply = getattr(fm, name)([str(root / "hour.tl")], harness=str(native), sections=sections,
                                              detail="full", **extra)
                    self.assertLess(time.monotonic() - started, 15, name)
                    self.assertEqual((reply["ok"], reply["exit"]), (False, 2), (name, str(reply)[:600]))
                    self.assertIn("192MiB render/256MiB memory profile", reply["message"])
                    self.assertEqual([row["operation"] for row in reply["processes"]],
                                     ["--params", "--gen-params", "--inspect"])

    def test_cancel_reaps_native_and_its_separate_process_group(self):
        from mcp_server import isolated_tool
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                native = SDK / "build/s12e10p1v1/harness"
                if not native.is_file():
                    self.skipTest("Build the native harness first")
                fake = root / "slow-harness"
                fake.write_text("#!/usr/bin/env python3\n" +
                    "import os,sys,time,subprocess,json\n" +
                    "from pathlib import Path\n" +
                    "if sys.argv[1] == '--play':\n" +
                    " child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)\n" +
                    " Path('running.json').write_text(json.dumps([os.getpid(),child.pid]))\n" +
                    " time.sleep(60)\n" +
                    "else: os.execv(" + repr(str(native)) + ", [" + repr(str(native)) + "]+sys.argv[1:])\n")
                fake.chmod(0o700)
                text = next((SDK / "examples").glob("*.tl")).read_text()
                task = asyncio.create_task(isolated_tool("render", {"text": text, "out": "result"}, fake, SDK, root))
                marker = root / "running.json"
                for _ in range(300):
                    if marker.exists():
                        break
                    if task.done():
                        self.fail(str(task.result()))
                    await asyncio.sleep(0.02)
                self.assertTrue(marker.exists())
                pids = json.loads(marker.read_text())
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                for pid in pids:
                    state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                    self.assertTrue(not state or state.startswith("Z"), (pid, state))
                self.assertEqual(list(root.glob(".jottune-operation-*")), [])
        asyncio.run(exercise())

    def slow_harness(self, root, seconds=60, then_render=False):
        native = SDK / "build/s12e10p1v1/harness"
        if not native.is_file():
            self.skipTest("Build the native harness first")
        fake = root / "slow-harness"
        fake.write_text("#!/usr/bin/env python3\nimport json,os,sys,time\nfrom pathlib import Path\n" +
            "if sys.argv[1]=='--play':\n" +
            " Path('running-'+str(os.getpid())).write_text(json.dumps([os.getpid(),os.getppid()]))\n" +
            " time.sleep(" + str(seconds) + ")\n" + ("" if then_render else " sys.exit(5)\n") +
            "os.execv(" + repr(str(native)) + ", [" + repr(str(native)) + "]+sys.argv[1:])\n")
        fake.chmod(0o700)
        return fake

    def test_anyio_cancellation_reaps_worker_before_releasing_its_slot(self):
        import anyio
        import mcp_server

        async def exercise():
            before = set(mcp_server.ACTIVE_CHILDREN)
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                fake = self.slow_harness(root)
                text = next((SDK / "examples").glob("*.tl")).read_text()
                scopes = []

                async def call():
                    # The MCP SDK cancels tool calls through an AnyIO scope,
                    # which delivers cancellation again at every checkpoint.
                    with anyio.CancelScope() as scope:
                        scopes.append(scope)
                        await mcp_server.isolated_tool("render", {"text": text, "out": "new"},
                                                       fake, SDK, root)

                try:
                    async with anyio.create_task_group() as group:
                        group.start_soon(call)
                        with anyio.fail_after(15):
                            while not list(root.glob("running-*")):
                                await anyio.sleep(0.02)
                        worker_pids = set(mcp_server.ACTIVE_CHILDREN) - before
                        self.assertEqual(len(worker_pids), 1)
                        self.assertEqual(len(list(root.glob(".jottune-operation-*"))), 1)
                        scopes[0].cancel()
                    # Check immediately after the cancelled call joins, rather
                    # than letting a server exit or a startup sweep hide leaks.
                    self.assertEqual(mcp_server.ACTIVE_CHILDREN, before)
                    self.assertEqual(list(root.glob(".jottune-operation-*")), [])
                    for pid in worker_pids:
                        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                               capture_output=True, text=True).stdout.strip()
                        self.assertFalse(state, (pid, state))
                    with anyio.fail_after(30):
                        reply = await mcp_server.isolated_tool("reference", {"section": "index", "length": 64},
                                                               fake, SDK, root)
                    self.assertTrue(reply["ok"], reply)
                    self.assertEqual(mcp_server.ACTIVE_CHILDREN, before)
                finally:
                    for pid in set(mcp_server.ACTIVE_CHILDREN) - before:
                        mcp_server.kill_operation(pid)
                        mcp_server.ACTIVE_CHILDREN.discard(pid)

        with patch.dict(os.environ, {"JOTTUNE_MCP_CONCURRENCY": "1"}):
            anyio.run(exercise, backend="asyncio")

    def test_worker_failures_report_their_actual_cause(self):
        import mcp_server
        from mcp_server import isolated_tool
        async def exercise():
            with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
                root = Path(directory).resolve()
                fake = self.slow_harness(root)
                private = Path(outside) / "private.tl"
                private.write_text("PRIVATE_SENTINEL\n")
                large = await isolated_tool("render", {"text": "x" * mcp_server.CAPTURE_BYTES, "out": "new"},
                                            fake, SDK, root)
                self.assertEqual((large["ok"], large["exit"]), (False, 2), large)
                self.assertIn("request", large["message"])
                changed = await isolated_tool("describe", {"source": str(private)}, fake, SDK, root)
                self.assertEqual((changed["ok"], changed["exit"]), (False, 2), changed)
                self.assertIn("changed", changed["message"])
                self.assertNotIn("PRIVATE", json.dumps(changed))
                with patch.object(mcp_server, "CAPTURE_BYTES", 64):
                    captured = await isolated_tool("reference", {"section": "index"}, fake, SDK, root)
                self.assertEqual((captured["ok"], captured["exit"]), (False, 5), captured)
                self.assertIn("capture limit", captured["message"])
                dying = root / "dying-harness"
                dying.write_text(fake.read_text().replace("time.sleep(60)", "os.kill(os.getppid(),9)"))
                dying.chmod(0o700)
                text = next((SDK / "examples").glob("*.tl")).read_text()
                died = await isolated_tool("render", {"text": text, "out": "new"}, dying, SDK, root)
                self.assertEqual((died["ok"], died["exit"]), (False, 5), died)
                self.assertIn("without a structured result", died["message"])
                self.assertEqual(list(root.glob(".jottune-operation-*")), [])
        asyncio.run(exercise())

    def test_timeouts_leave_no_operation_directories(self):
        import mcp_server
        from mcp_server import isolated_tool
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                fake = self.slow_harness(root)
                text = next((SDK / "examples").glob("*.tl")).read_text()
                with patch.object(mcp_server, "TOOL_SECONDS", 3):
                    reply = await isolated_tool("render", {"text": text, "out": "new"}, fake, SDK, root)
                self.assertEqual((reply["ok"], reply["exit"]), (False, 5), reply)
                self.assertIn("execution limit", reply["message"])
                for marker in root.glob("running-*"):
                    for pid in json.loads(marker.read_text()):
                        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                        self.assertTrue(not state or state.startswith("Z"), (pid, state))
                self.assertEqual(list(root.glob(".jottune-operation-*")), [])
        asyncio.run(exercise())

    def test_startup_sweep_removes_only_stale_operation_directories(self):
        import mcp_server
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root, outside = Path(directory).resolve(), Path(outside).resolve()
            (outside / "keep.txt").write_text("keep")
            old = os.path.getmtime(root) - 7200
            stale, nested, fresh = (root / ".jottune-operation-abcd1234", root / ".jottune-operation-nest_123",
                                    root / ".jottune-operation-fresh123")
            for path in (stale, nested, fresh):
                path.mkdir()
            (nested / "fm-sections-x").mkdir()
            (nested / "fm-sections-x" / "input.tl").write_text("left over")
            (root / ".jottune-operation-shortname").mkdir()
            (root / ".jottune-operation-file1234").write_text("user file")
            (root / ".jottune-operation-link1234").symlink_to(outside, target_is_directory=True)
            (root / "user").mkdir()
            recent = root / ".jottune-operation-recent12"
            (recent / "work").mkdir(parents=True)
            (recent / "work" / "current.tl").write_text("in use")
            for path in (stale, nested / "fm-sections-x" / "input.tl", nested / "fm-sections-x", nested,
                         recent / "work", recent, root / ".jottune-operation-shortname",
                         root / ".jottune-operation-file1234", root / "user"):
                os.utime(path, (old, old))
            mcp_server.sweep_operations(root)
            self.assertEqual(sorted(path.name for path in root.iterdir()),
                             [".jottune-operation-file1234", ".jottune-operation-fresh123",
                              ".jottune-operation-link1234", ".jottune-operation-recent12",
                              ".jottune-operation-shortname", "user"])
            self.assertEqual((outside / "keep.txt").read_text(), "keep")

    def test_concurrent_workers_are_bounded_and_waiting_calls_cancel(self):
        import mcp_server
        from mcp_server import isolated_tool
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                fake = self.slow_harness(root)
                text = next((SDK / "examples").glob("*.tl")).read_text()
                first = asyncio.create_task(isolated_tool("render", {"text": text, "out": "one"}, fake, SDK, root))
                for _ in range(300):
                    if list(root.glob("running-*")):
                        break
                    await asyncio.sleep(0.02)
                second = asyncio.create_task(isolated_tool("render", {"text": text, "out": "two"}, fake, SDK, root))
                await asyncio.sleep(1.5)
                self.assertEqual(len(list(root.glob("running-*"))), 1)
                self.assertEqual(len(list(root.glob(".jottune-operation-*"))), 1)
                second.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await second
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first
                reply = await asyncio.wait_for(isolated_tool("reference", {"section": "index", "length": 64},
                                                             fake, SDK, root), 30)
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(len(list(root.glob("running-*"))), 1)
                self.assertEqual(list(root.glob(".jottune-operation-*")), [])
        with patch.dict(os.environ, {"JOTTUNE_MCP_CONCURRENCY": "1"}):
            asyncio.run(exercise())
        import contextlib
        import io
        for value in ("0", "9", "two"):
            message = io.StringIO()
            with patch.dict(os.environ, {"JOTTUNE_MCP_CONCURRENCY": value}), contextlib.redirect_stderr(message):
                self.assertEqual(mcp_server.main(["--synth-root", str(SDK)]), 2)
            self.assertIn("JOTTUNE_MCP_CONCURRENCY", message.getvalue())

    def test_worker_stops_when_its_server_is_killed(self):
        async def exercise():
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                fake = self.slow_harness(root, seconds=2, then_render=True)
                server = await asyncio.create_subprocess_exec(sys.executable, "-B", str(ROOT / "server/mcp_server.py"),
                    "--synth-root", str(SDK), "--harness", str(fake), cwd=root,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                async def send(value):
                    server.stdin.write((json.dumps(value) + "\n").encode())
                    await server.stdin.drain()
                try:
                    await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "kill-test", "version": "1"}}})
                    self.assertIn("result", json.loads(await asyncio.wait_for(server.stdout.readline(), 10)))
                    await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                    await send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "render",
                        "arguments": {"text": next((SDK / "examples").glob("*.tl")).read_text(), "out": "new"}}})
                    for _ in range(500):
                        markers = list(root.glob("running-*"))
                        if markers:
                            break
                        await asyncio.sleep(0.02)
                    self.assertTrue(markers)
                    pids = json.loads(markers[0].read_text())
                    server.kill()
                    await server.wait()
                    await asyncio.sleep(5)
                    for pid in pids:
                        state = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                        self.assertTrue(not state or state.startswith("Z"), (pid, state))
                    self.assertFalse((root / "new" / "records").exists())
                finally:
                    if server.returncode is None:
                        server.kill()
                        await server.wait()
        asyncio.run(exercise())

    def test_large_replies_become_explicit_bounded_envelopes(self):
        from mcp_server import REPLY_BYTES, bounded_reply
        small = dict(version=1, ok=True, exit=0, detail="full", sections=[dict(index=0, rms=0.5)])
        self.assertIs(bounded_reply("measure_sections", small), small)
        rows = [dict(index=index, rms=0.5, peak=0.75, label="section-%04d" % index) for index in range(2000)]
        large = dict(version=1, ok=True, exit=0, detail="full", sections=rows, processes=rows, cost=dict(seconds=1))
        reply = bounded_reply("measure_sections", large)
        self.assertLessEqual(len(json.dumps(reply)), REPLY_BYTES)
        self.assertEqual((reply["ok"], reply["exit"], reply["truncated"], reply["detail"], reply["cost"]),
                         (True, 0, True, "full", dict(seconds=1)))
        self.assertEqual((reply["reply_bytes"], reply["reply_limit"]), (len(json.dumps(large)), REPLY_BYTES))
        self.assertEqual(sorted(reply["reply_omitted"]), ["processes", "sections"])
        self.assertIn("artifact_read(kind='receipt')", reply["next"])
        failed = bounded_reply("render", dict(version=1, exit=2, message="refused", processes=rows))
        self.assertEqual((failed["ok"], failed["exit"], failed["message"], failed["truncated"]), (False, 2, "refused", True))
        image = dict(version=1, ok=True, exit=0, stdout="", stderr="", png="/work/plots/a.png", png_sha256="0" * 64,
                     png_base64="A" * REPLY_BYTES, mime_type="image/png")
        located = bounded_reply("analyze", image)
        self.assertEqual(located["artifact"], dict(directory="/work/plots", kind="png", digest="0" * 64, filename="a.png"))
        self.assertIn("png_base64", located["reply_omitted"])
        self.assertLessEqual(len(json.dumps(located)), REPLY_BYTES)
        many = dict(version=1, ok=True, exit=0, **{"k" * 55 + "%05d" % index: "v" * 900 for index in range(400)})
        many["x" * 5000] = "y"
        bounded = bounded_reply("suggest", many)
        self.assertLessEqual(len(json.dumps(bounded)), REPLY_BYTES)
        kept = [key for key in bounded if key in many and key not in ("version", "ok", "exit")]
        self.assertEqual((len(bounded["reply_omitted"]), len(kept) + 256 + bounded["reply_omitted_unlisted"]),
                         (256, len(many) - 3))

    def test_stdio_replies_and_resource_pages_are_bounded(self):
        import base64
        import hashlib
        import mcp_server
        native = SDK / "build/s12e10p1v1/harness"
        if not native.is_file():
            self.skipTest("Build the native harness first")
        async def exercise():
            with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as log:
                root = Path(directory).resolve()
                (root / "song.tl").write_text(sorted((SDK / "examples").glob("*.tl"))[0].read_text())
                server = await asyncio.create_subprocess_exec(sys.executable, "-B", str(ROOT / "server/mcp_server.py"),
                    "--synth-root", str(SDK), "--harness", str(native), cwd=root, limit=1 << 22,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log)
                sent = 0
                async def request(method, params):
                    nonlocal sent
                    sent += 1
                    server.stdin.write((json.dumps({"jsonrpc": "2.0", "id": sent, "method": method, "params": params}) + "\n").encode())
                    await server.stdin.drain()
                    while True:
                        line = await asyncio.wait_for(server.stdout.readline(), 120)
                        message = json.loads(line)
                        if message.get("id") == sent:
                            return line, message
                async def tool(name, arguments):
                    line, message = await request("tools/call", {"name": name, "arguments": arguments})
                    result = message["result"]
                    self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
                    self.assertLessEqual(len(json.dumps(result["structuredContent"])), mcp_server.REPLY_BYTES)
                    self.assertLess(len(line), 3 * mcp_server.REPLY_BYTES)
                    return result["structuredContent"]
                try:
                    await request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                 "clientInfo": {"name": "budget-test", "version": "1"}})
                    server.stdin.write(b'{"jsonrpc": "2.0", "method": "notifications/initialized"}\n')
                    sections = [dict(start_ns=index * 1000000, end_ns=(index + 1) * 1000000) for index in range(32)]
                    measured = await tool("measure_sections", {"files": ["song.tl"], "sections": sections, "detail": "full"})
                    self.assertEqual((measured["ok"], measured["exit"], measured["truncated"]), (True, 0, True), measured)
                    self.assertGreater(measured["reply_bytes"], mcp_server.REPLY_BYTES)
                    self.assertIn("sections", measured["reply_omitted"])
                    rendered = await tool("render", {"files": ["song.tl"], "out": "r", "wav": True, "detail": "compact"})
                    self.assertTrue(rendered["ok"], rendered)
                    locator = {"directory": "r", "kind": "wav", "digest": rendered["wav_sha256"]}
                    page = await tool("artifact_read", {**locator, "length": 32768})
                    self.assertEqual((page["ok"], len(base64.b64decode(page["data_base64"]))), (True, 32768))
                    self.assertNotIn("truncated", page)
                    refused = await tool("artifact_read", {**locator, "length": 32769})
                    self.assertEqual((refused["ok"], refused["exit"]), (False, 2), refused)
                    self.assertIn("32768", refused["message"])
                    _, listed = await request("resources/templates/list", {})
                    templates = [item["uriTemplate"] for item in listed["result"]["resourceTemplates"]]
                    for uri in ("fm://instrument", "fm://params"):
                        self.assertIn(uri + "/page/{page}", templates)
                        whole = (await request("resources/read", {"uri": uri}))[1]["result"]["contents"][0]["text"].encode()
                        chunks, number = [], 1
                        while True:
                            content = (await request("resources/read", {"uri": uri + "/page/" + str(number)}))[1]["result"]["contents"][0]
                            value = json.loads(content["text"])
                            self.assertEqual((content["mimeType"], value["uri"], value["page"], value["size"], value["sha256"],
                                              value["offset"]), ("application/json", uri, number, len(whole),
                                              hashlib.sha256(whole).hexdigest(), sum(map(len, chunks))))
                            chunks.append(value["text"].encode())
                            self.assertLessEqual(len(chunks[-1]), mcp_server.RESOURCE_PAGE_BYTES)
                            if value["next"] is None:
                                break
                            number += 1
                            self.assertEqual(value["next"], uri + "/page/" + str(number))
                        self.assertEqual((b"".join(chunks), number > 1), (whole, True))
                    for bad in ("0", "999", "x"):
                        _, missing = await request("resources/read", {"uri": "fm://instrument/page/" + bad})
                        self.assertEqual(missing["error"]["code"], -32602, missing)
                        self.assertIn("numbered from 1", missing["error"]["message"])
                finally:
                    if server.returncode is None:
                        server.kill()
                        await server.wait()
                log.seek(0)
                self.assertNotIn(b"Traceback", log.read())
        asyncio.run(exercise())


    def test_cancellation_eof_and_sigterm_stop_inflight_native_processes(self):
        from mcp_server import package_version
        async def exercise(mode):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                native = SDK / "build/s12e10p1v1/harness"
                fake = root / "slow-harness"
                fake.write_text("#!/usr/bin/env python3\nimport os,sys,time\nfrom pathlib import Path\n" +
                    "if sys.argv[1]=='--play':\n Path('running.pid').write_text(str(os.getpid()))\n time.sleep(60)\n" +
                    "else: os.execv(" + repr(str(native)) + ", [" + repr(str(native)) + "]+sys.argv[1:])\n")
                fake.chmod(0o700)
                child = await asyncio.create_subprocess_exec("node", str(ROOT / "bin/jottune-mcp.mjs"),
                    "serve", "--synth-root", str(SDK), "--harness", str(fake), "--workspace", str(root),
                    env={**os.environ, "JOTTUNE_PYTHON": sys.executable},
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                async def send(value):
                    child.stdin.write((json.dumps(value)+"\n").encode())
                    await child.stdin.drain()
                try:
                    await send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
                        "protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"shutdown-test","version":"1"}}})
                    answer = json.loads(await asyncio.wait_for(child.stdout.readline(),5))
                    self.assertIn("result",answer)
                    await send({"jsonrpc":"2.0","method":"notifications/initialized"})
                    await send({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"render",
                        "arguments":{"text":next((SDK/'examples').glob('*.tl')).read_text(),"out":"new"}}})
                    marker=root/'running.pid'
                    for _ in range(300):
                        if marker.exists():break
                        await asyncio.sleep(0.02)
                    self.assertTrue(marker.exists())
                    pid=int(marker.read_text())
                    if mode=='cancelled':
                        await send({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":2,"reason":"stop render"}})
                        for _ in range(100):
                            state=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
                            if not state or state.startswith('Z'):break
                            await asyncio.sleep(0.02)
                        self.assertTrue(not state or state.startswith('Z'),(mode,pid,state))
                        await send({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"reference","arguments":{"section":"index","length":256}}})
                        response=json.loads(await asyncio.wait_for(child.stdout.readline(),5))
                        self.assertEqual(response['id'],3)
                        self.assertFalse(response['result'].get('isError',False))
                        child.stdin.close()
                    elif mode=='eof':child.stdin.close()
                    else:child.terminate()
                    try:
                        await asyncio.wait_for(child.wait(),5)
                    except asyncio.TimeoutError:
                        self.fail("Server did not finish after " + mode + "; launcher exit=" + str(child.returncode))
                    for _ in range(100):
                        state=subprocess.run(['ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
                        if not state or state.startswith('Z'):break
                        await asyncio.sleep(0.02)
                    self.assertTrue(not state or state.startswith('Z'),(mode,pid,state))
                    self.assertEqual(list(root.glob(".jottune-operation-*")),[],mode)
                finally:
                    if child.returncode is None:
                        child.terminate()
                        try: await asyncio.wait_for(child.wait(),2)
                        except asyncio.TimeoutError:
                            child.kill();await child.wait()
        for mode in ('cancelled','sigterm','eof'):
            asyncio.run(exercise(mode))


if __name__ == "__main__":
    unittest.main()
