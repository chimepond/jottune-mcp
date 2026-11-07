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
