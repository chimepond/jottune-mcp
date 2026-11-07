// Copyright (c) 2026 Nenad Micic
// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import test from 'node:test';
import {resolveSDK,packageRoot} from '../lib/sdk.mjs';
import {isSDKFile,stagePackage} from '../scripts/package.mjs';

async function fixture(t) {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),'jottune-mcp-test-')));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const sdk=path.join(root,'jottune-synth');await fs.mkdir(sdk);
  for(const file of ['LICENSE','Makefile','tools/fm.py','tools/interchange.py','docs/INSTRUMENT.md','src/host/harness.c']) {
    await fs.mkdir(path.dirname(path.join(sdk,file)),{recursive:true});
    await fs.writeFile(path.join(sdk,file),'fixture\n');
  }
  return {root,sdk};
}
const launcher=path.join(packageRoot,'bin/jottune-mcp.mjs');
const run=(args,env={})=>execFileSync(process.execPath,[launcher,...args],{
  encoding:'utf8',env:{...process.env,...env},stdio:['ignore','pipe','pipe'],
});

test('explicit SDK and environment select the exact complete source',async t=>{
  const {sdk}=await fixture(t);
  assert.equal(resolveSDK(sdk,{env:{}}),sdk);
  assert.equal(resolveSDK(undefined,{env:{JOTTUNE_SYNTH_ROOT:sdk}}),sdk);
});
test('an explicit invalid or empty SDK cannot fall back to a valid sibling',async t=>{
  const {root,sdk}=await fixture(t),packageDirectory=path.join(root,'jottune-mcp');
  await fs.mkdir(packageDirectory);
  assert.equal(resolveSDK(undefined,{root:packageDirectory,env:{}}),sdk);
  assert.throws(()=>resolveSDK(path.join(root,'missing'),{root:packageDirectory,env:{}}),/complete JotTune/);
  assert.throws(()=>resolveSDK(undefined,{root:packageDirectory,env:{JOTTUNE_SYNTH_ROOT:''}}),/complete JotTune/);
});
test('a bundled SDK takes priority over a sibling checkout',async t=>{
  const {root,sdk}=await fixture(t),packageDirectory=path.join(root,'jottune-mcp');
  await fs.mkdir(packageDirectory);
  await fs.cp(sdk,path.join(packageDirectory,'synth'),{recursive:true});
  assert.equal(resolveSDK(undefined,{root:packageDirectory,env:{}}),path.join(packageDirectory,'synth'));
});
test('help and requirement discovery work before an SDK is available',()=>{
  const env={JOTTUNE_SYNTH_ROOT:'/unavailable-jottune-sdk'};
  assert.match(run(['--help'],env),/--synth-root/);
  assert.equal(run(['requirements'],env).trim(),path.join(packageRoot,'requirements/core.txt'));
  assert.equal(run(['requirements','--plots'],env).trim(),path.join(packageRoot,'requirements/plots.txt'));
});
test('client configuration freezes the selected SDK and workspace paths',async t=>{
  const {root,sdk}=await fixture(t),workspace=path.join(root,'session');await fs.mkdir(workspace);
  const value=JSON.parse(run(['config','--synth-root',sdk,'--workspace',workspace],{JOTTUNE_PYTHON:process.execPath}));
  const entry=value.mcpServers.jottune;
  assert.equal(entry.command,process.execPath);
  assert.deepEqual(entry.args,[launcher,'serve','--synth-root',sdk,'--harness',path.join(sdk,'build/s12e10p1v1/harness'),'--workspace',workspace]);
});
test('package selection excludes corpora, apps, infrastructure and experiments',()=>{
  for(const file of ['src/host/harness.c','tools/fm.py','tools/music/compose.py','data/recipes.txt','web/media.mjs'])
    assert.equal(isSDKFile(file),true,file);
  for(const file of ['music/song.tl','tests/golden/sine.tl','data/catalog.tsv','apps/macos/App.swift',
    'site/exchange.mjs','audio-text-lab/cli.mjs','tools/music/songbook/song.py','swarm/notes.md'])
    assert.equal(isSDKFile(file),false,file);
});
test('package staging pins committed source and rejects dirty or existing output',async t=>{
  const {root,sdk}=await fixture(t);
  const git=args=>execFileSync('git',args,{cwd:sdk,encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
  git(['init','--initial-branch=main']);git(['add','--','LICENSE','Makefile','tools','docs','src']);
  git(['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Create fixture']);
  const output=path.join(root,'package'),result=await stagePackage({synthRoot:sdk,output});
  const revision=JSON.parse(await fs.readFile(path.join(output,'synth-release.json'),'utf8'));
  assert.equal(result.synth_revision,git(['rev-parse','HEAD']));
  assert.equal(revision.source_revision,result.synth_revision);
  const manifest=JSON.parse(await fs.readFile(path.join(output,'package.json'),'utf8'));
  assert.equal(manifest.name,'@chimepond/jottune-mcp');assert.equal(manifest.private,true);
  assert.equal(manifest.scripts,undefined);
  await assert.rejects(stagePackage({synthRoot:sdk,output}),/already exists/);
  await fs.writeFile(path.join(sdk,'tools/fm.py'),'changed\n');
  await assert.rejects(stagePackage({synthRoot:sdk,output:path.join(root,'dirty')}),/Commit the synth SDK/);
});
