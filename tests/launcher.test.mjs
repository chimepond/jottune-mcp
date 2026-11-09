// Copyright (c) 2026 Nenad Micic
// SPDX-License-Identifier: Apache-2.0
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {execFileSync,spawnSync} from 'node:child_process';
import {fileURLToPath,pathToFileURL} from 'node:url';
import test from 'node:test';
import {resolveSDK,packageRoot} from '../lib/sdk.mjs';
import {isSDKFile} from '../scripts/package.mjs';

async function fixture(t) {
  const root = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(),'jottune-mcp-test-')));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const sdk=path.join(root,'jottune-synth');await fs.mkdir(sdk);
  for(const file of ['LICENSE','Makefile','tools/fm.py','tools/interchange.py','tools/export_schema.py','tools/decode.py',
    'docs/INSTRUMENT.md','docs/DECODING.md','src/host/harness.c']) {
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
test('the direct Python server also refuses empty explicit SDK selections',async t=>{
  const {sdk}=await fixture(t);
  execFileSync('python3',['-B','-c',`
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sdk import resolve_sdk
expected = Path(sys.argv[2])
assert resolve_sdk(expected) == expected
for value in ('', '   ', '/unavailable-jottune-sdk'):
    os.environ['JOTTUNE_SYNTH_ROOT'] = value
    for explicit in (None, value):
        try:
            resolve_sdk(explicit)
        except ValueError:
            pass
        else:
            raise AssertionError('Invalid explicit SDK was accepted')
`,path.join(packageRoot,'server'),sdk],{cwd:sdk,stdio:['ignore','pipe','pipe']});
});
test('the protocol version follows the package manifest and refuses a missing version',async t=>{
  const {root}=await fixture(t),server=path.join(root,'server');await fs.mkdir(server);
  await fs.copyFile(path.join(packageRoot,'server/sdk.py'),path.join(server,'sdk.py'));
  await fs.writeFile(path.join(root,'package.json'),JSON.stringify({version:'1.2.3'}));
  execFileSync('python3',['-B','-c',`
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from sdk import package_version
manifest = Path(sys.argv[1]).parent / 'package.json'
assert package_version() == '1.2.3'
manifest.write_text(json.dumps({'version': '2.0.0-beta.1'}))
assert package_version() == '2.0.0-beta.1'
manifest.write_text('{}')
try:
    package_version()
except ValueError:
    pass
else:
    raise AssertionError('A missing package version was accepted')
`,server],{stdio:['ignore','pipe','pipe']});
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
// A stand-in interpreter that answers the launcher's Python probe with a fixed report.
async function stubPython(root,name,report) {
  const file=path.join(root,name);
  // Quote for sh: a ' inside the report (as in No module named 'mcp') must survive.
  await fs.writeFile(file,'#!/bin/sh\necho \''+JSON.stringify(report).replace(/'/g,"'\\''")+'\'\n');await fs.chmod(file,0o755);
  return file;
}
const usablePython={version:'3.12.3',supported:true,modules:{'mcp.server':{ok:true}}};
test('client configuration freezes the selected SDK and workspace paths',async t=>{
  const {root,sdk}=await fixture(t),workspace=path.join(root,'session');await fs.mkdir(workspace);
  const python=await stubPython(root,'python',usablePython);
  const value=JSON.parse(run(['config','--synth-root',sdk,'--workspace',workspace],{JOTTUNE_PYTHON:python}));
  const entry=value.mcpServers.jottune;
  assert.equal(await fs.realpath(entry.command),await fs.realpath(process.execPath));
  assert.deepEqual(entry.args,[launcher,'serve','--synth-root',sdk,'--harness',path.join(sdk,'build/s12e10p1v1/harness'),'--workspace',workspace]);
  assert.deepEqual(entry.env,{JOTTUNE_PYTHON:python});
});
test('client configuration refuses a Python that cannot run the server unless forced',async t=>{
  const {root,sdk}=await fixture(t),workspace=path.join(root,'session');await fs.mkdir(workspace);
  const cases=[[await stubPython(root,'old',{version:'3.9.6',supported:false,modules:{'mcp.server':{ok:false,error:"No module named 'mcp'"}}}),/3\.9\.6.*3\.10/],
    [await stubPython(root,'bare',{version:'3.12.3',supported:true,modules:{'mcp.server':{ok:false,error:"No module named 'mcp'"}}}),/No module named 'mcp'/],
    [process.execPath,/could not be started|did not report/]];
  for(const [python,reason] of cases) {
    const refused=spawnSync(process.execPath,[launcher,'config','--synth-root',sdk,'--workspace',workspace],
      {env:{...process.env,JOTTUNE_PYTHON:python},encoding:'utf8'});
    assert.equal(refused.status,2,python);assert.equal(refused.stdout,'');
    assert.match(refused.stderr,reason);assert.match(refused.stderr,/requirements\/core\.txt/);assert.match(refused.stderr,/--force/);
    const forced=spawnSync(process.execPath,[launcher,'config','--force','--synth-root',sdk,'--workspace',workspace],
      {env:{...process.env,JOTTUNE_PYTHON:python},encoding:'utf8'});
    assert.equal(forced.status,0,python);assert.match(forced.stderr,/warning/i);
    assert.equal(JSON.parse(forced.stdout).mcpServers.jottune.env.JOTTUNE_PYTHON,python);
  }
});
test('a signal death of the server is reported as 128 plus the signal number',async t=>{
  const {root,sdk}=await fixture(t),workspace=path.join(root,'session');await fs.mkdir(workspace);
  const harness=path.join(root,'harness');await fs.writeFile(harness,'fixture\n');
  for(const [name,status] of [['KILL',137],['SEGV',139],['TERM',143]]) {
    const python=path.join(root,'python-'+name);
    await fs.writeFile(python,'#!/bin/sh\nkill -'+name+' $$\n');await fs.chmod(python,0o755);
    const result=spawnSync(process.execPath,[launcher,'serve','--synth-root',sdk,'--harness',harness,'--workspace',workspace],
      {env:{...process.env,JOTTUNE_PYTHON:python},input:'',encoding:'utf8'});
    assert.equal(result.status,status,name);
  }
});
test('build without a C compiler explains how to install one and leaves no build directory',async t=>{
  const {root,sdk}=await fixture(t),bin=path.join(root,'bin');await fs.mkdir(bin);
  await fs.writeFile(path.join(sdk,'Makefile'),'CC ?= cc\nB := build/s12e10p1v1\nall: $(B)/harness\n$(B):\n\tmkdir -p $@\n'+
    '$(B)/harness: | $(B)\n\t$(CC) -o $@ src/host/harness.c\n');
  for(const tool of ['make','mkdir'])
    await fs.symlink(execFileSync('/bin/sh',['-c','command -v '+tool],{encoding:'utf8'}).trim(),path.join(bin,tool));
  const env={...process.env,PATH:bin};delete env.CC;delete env.HOSTCC;
  const result=spawnSync(process.execPath,[launcher,'build','--synth-root',sdk],{env,encoding:'utf8'});
  assert.equal(result.status,2);
  assert.match(result.stderr,/C99 compiler/);assert.match(result.stderr,/xcode-select --install/);assert.match(result.stderr,/build-essential/);
  assert.doesNotMatch(result.stderr,/No such file or directory/);
  await assert.rejects(fs.lstat(path.join(sdk,'build')),{code:'ENOENT'});
});
test('package selection excludes corpora, apps, infrastructure and experiments',()=>{
  for(const file of ['src/host/harness.c','tools/fm.py','tools/export_schema.py','tools/decode.py','docs/DECODING.md',
    'tools/music/compose.py','data/recipes.txt','web/media.mjs'])
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
  // Exercise packaging from its own committed checkout, including ignored files
  // and dirty source, rather than depending on the caller's working-tree state.
  const mcp=path.join(root,'jottune-mcp');await fs.mkdir(mcp);
  const sourceMembers=['LICENSE','SECURITY.md','README.md','CHANGELOG.md','package.json','bin','lib','server','requirements','scripts'];
  for(const member of sourceMembers)await fs.cp(path.join(packageRoot,member),path.join(mcp,member),
    {recursive:true,filter:file=>!file.split(path.sep).includes('__pycache__')&&!file.endsWith('.pyc')});
  const mcpGit=args=>execFileSync('git',args,{cwd:mcp,encoding:'utf8',stdio:['ignore','pipe','pipe']}).trim();
  mcpGit(['init','--initial-branch=main']);mcpGit(['add','--',...sourceMembers]);
  mcpGit(['-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','Create package fixture']);
  const {stagePackage}=await import(pathToFileURL(path.join(mcp,'scripts/package.mjs')));
  await fs.writeFile(path.join(mcp,'.git/info/exclude'),'server/ignored-local.py\n');
  await fs.writeFile(path.join(mcp,'server/ignored-local.py'),'must not ship\n');
  const output=path.join(root,'package'),result=await stagePackage({synthRoot:sdk,output});
  const revision=JSON.parse(await fs.readFile(path.join(output,'synth-release.json'),'utf8'));
  assert.equal(result.synth_revision,git(['rev-parse','HEAD']));
  assert.equal(revision.source_revision,result.synth_revision);
  assert.equal(result.mcp_revision,mcpGit(['rev-parse','HEAD']));
  assert.equal(revision.mcp_source_revision,result.mcp_revision);
  const manifest=JSON.parse(await fs.readFile(path.join(output,'package.json'),'utf8'));
  const sourceManifest=JSON.parse(await fs.readFile(path.join(packageRoot,'package.json'),'utf8'));
  assert.equal(sourceManifest.private,true,'The unassembled source checkout must refuse npm publication');
  assert.equal(manifest.name,'@chimepond/jottune-mcp');assert.equal(manifest.private,false);
  assert.equal(manifest.version,sourceManifest.version);
  assert.deepEqual(manifest.publishConfig,{access:'public',tag:'beta'});
  assert.equal(manifest.scripts,undefined);
  assert.equal(manifest.dependencies,undefined);
  for(const name of ['server/__pycache__/private.pyc','synth/build/harness','synth/music/song.tl','synth/data/catalog.tsv']) {
    const file=path.join(output,name);await fs.mkdir(path.dirname(file),{recursive:true});
    await fs.writeFile(file,'excluded package member\n');
  }
  const packed=JSON.parse(execFileSync('npm',['pack','--json','--offline','--ignore-scripts','--pack-destination',root],{
    cwd:output,encoding:'utf8',stdio:['ignore','pipe','pipe'],
    env:{...process.env,npm_config_cache:path.join(root,'npm-cache')},
  }))[0];
  const names=packed.files.map(file=>file.path);
  assert.ok(names.includes('server/mcp_server.py')&&names.includes('synth/src/host/harness.c'));
  assert.ok(names.includes('synth/tools/export_schema.py'));
  assert.ok(names.includes('synth/tools/decode.py')&&names.includes('synth/docs/DECODING.md'));
  assert.ok(!names.includes('server/ignored-local.py'));
  assert.ok(!names.some(name=>name.includes('__pycache__')||name.endsWith('.pyc')||name.endsWith('.tl')||
    name.startsWith('synth/build/')||name==='synth/data/catalog.tsv'));
  await assert.rejects(stagePackage({synthRoot:sdk,output}),/already exists/);
  const readme=await fs.readFile(path.join(mcp,'README.md'));
  await fs.appendFile(path.join(mcp,'README.md'),'uncommitted change\n');
  await assert.rejects(stagePackage({synthRoot:sdk,output:path.join(root,'dirty-mcp')}),/Commit the MCP source/);
  await assert.rejects(fs.access(path.join(root,'dirty-mcp')),{code:'ENOENT'});
  await fs.writeFile(path.join(mcp,'README.md'),readme);
  await fs.writeFile(path.join(sdk,'tools/fm.py'),'changed\n');
  await assert.rejects(stagePackage({synthRoot:sdk,output:path.join(root,'dirty')}),/Commit the synth SDK/);
});
test('doctor reports the synth API and the native renderer separately',async t=>{
  const {root,sdk}=await fixture(t),workspace=path.join(root,'session');await fs.mkdir(workspace);
  const doctor=async api=>{
    await fs.writeFile(path.join(sdk,'tools/fm.py'),api+'class Native:\n    def __init__(self, harness):\n'+
      "        self.metadata = type('Metadata', (), {'engine': 'fixture engine'})()\n");
    const result=spawnSync(process.execPath,[launcher,'doctor','--synth-root',sdk,'--workspace',workspace],{encoding:'utf8'});
    return JSON.parse(result.stdout);
  };
  const current=await doctor('API_VERSION = 1\n');
  assert.equal(current.capabilities.render,true);
  assert.equal(current.engine,'ENGINE fixture engine');
  assert.deepEqual(current.synth_api,{version:1,supported:[1],compatible:true});
  // An unknown or missing API version makes doctor fail, but it does not mark a working renderer as failed.
  for(const [api,version] of [['API_VERSION = 99\n',99],['',null]]) {
    const report=await doctor(api);
    assert.equal(report.ok,false);assert.equal(report.complete,false);
    assert.equal(report.capabilities.render,true);
    assert.equal(report.engine,'ENGINE fixture engine');
    assert.equal(report.synth_api.version,version);
    assert.equal(report.synth_api.compatible,false);
    assert.match(report.synth_api.error,/this server supports API version 1\./);
  }
});
test('config and doctor look up Python modules as the server does, not in the caller\'s directory',async t=>{
  // python -c puts the current directory first on sys.path. The server runs as a script and does not.
  const {root,sdk}=await fixture(t),workspace=path.join(root,'session'),project=path.join(root,'project');
  await fs.mkdir(workspace);await fs.mkdir(path.join(project,'mcp'),{recursive:true});
  const plant=name=>'open('+JSON.stringify(path.join(root,name+'-was-imported'))+", 'w').close()\n";
  await fs.writeFile(path.join(project,'json.py'),plant('json'));
  await fs.writeFile(path.join(project,'mcp/__init__.py'),plant('mcp'));
  await fs.writeFile(path.join(project,'mcp/server.py'),'class MCPServer: pass\n');
  await fs.writeFile(path.join(project,'mcp/client.py'),'class Client: pass\n');
  const launch=args=>spawnSync(process.execPath,[launcher,...args,'--synth-root',sdk,'--workspace',workspace],
    {cwd:project,encoding:'utf8'});
  launch(['config','--force']);
  const doctor=JSON.parse(launch(['doctor']).stdout);
  assert.equal(typeof doctor.python_version,'string');
  for(const name of ['json','mcp']) await assert.rejects(fs.access(path.join(root,name+'-was-imported')),name+' was imported from the caller\'s directory');
});
