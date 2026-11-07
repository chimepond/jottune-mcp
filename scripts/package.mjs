#!/usr/bin/env node
// Copyright (c) 2026 Nenad Micic
// SPDX-License-Identifier: Apache-2.0
// Explicitly stage a self-contained npm payload from committed synth source.
import fs from 'node:fs/promises';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {packageRoot, resolveSDK} from '../lib/sdk.mjs';

const sdkFiles = new Set([
  'LICENSE', 'NOTICE', 'Makefile', 'docs/INSTRUMENT.md', 'docs/INTERCHANGE.md',
  'tools/fm.py', 'tools/score.py', 'tools/generator.py', 'tools/measure.py',
  'tools/midi_export.py', 'tools/sample_export.py', 'tools/interchange.py',
  'tools/export_schema.py',
  'tools/catalog.py', 'tools/receipts.py', 'tools/session.py', 'tools/recover.py',
  'tools/tl_svg.mjs', 'web/media.mjs', 'web/runtime.mjs',
]);

export function isSDKFile(name) {
  if (name === 'data/catalog.tsv') return false;
  return sdkFiles.has(name) || name.startsWith('src/') || name.startsWith('vendor/') ||
    /^data\/[^/]+\.(txt|tsv)$/.test(name) || /^tools\/music\/[^/]+\.py$/.test(name) ||
    name.startsWith('web/vendor/browser_wasi_shim/');
}

async function copyFiles(source, destination, names) {
  for (const name of names) {
    if (!name || name.startsWith('/') || name.split('/').some(part => !part || part === '.' || part === '..'))
      throw Error('Invalid source member.');
    const input = path.join(source,name), info = await fs.lstat(input);
    if (!info.isFile() || info.isSymbolicLink()) throw Error('Package members must be regular files.');
    const output = path.join(destination,name);
    await fs.mkdir(path.dirname(output),{recursive:true});
    await fs.copyFile(input,output);
    await fs.chmod(output,info.mode & 0o777);
  }
}

export async function stagePackage({synthRoot, output}) {
  const sdk = resolveSDK(synthRoot), target = path.resolve(output);
  if (target === packageRoot || target.startsWith(sdk + path.sep) || target === sdk)
    throw Error('Choose a new output directory outside the synth SDK.');
  try {await fs.lstat(target); throw Error('Package output already exists.');}
  catch(error) {if(error.code !== 'ENOENT')throw error;}
  const git = args => execFileSync('git',args,{cwd:sdk,encoding:'utf8'}).trim();
  if (await fs.realpath(git(['rev-parse','--show-toplevel'])) !== sdk)
    throw Error('The synth SDK must be its own Git checkout.');
  if (git(['status','--porcelain','--untracked-files=normal']))
    throw Error('Commit the synth SDK before packaging.');
  const revision = git(['rev-parse','HEAD']);
  const files = git(['ls-files','-z']).split('\0').filter(name => name && isSDKFile(name));
  await fs.mkdir(target,{recursive:false});
  try {
    for (const member of ['LICENSE','SECURITY.md','README.md','CHANGELOG.md','bin','lib','server','requirements']) {
      const input = path.join(packageRoot,member), info = await fs.lstat(input);
      if (info.isSymbolicLink())throw Error('Package source must not contain links.');
      await fs.cp(input,path.join(target,member),{recursive:true,filter:file=>
        !file.split(path.sep).some(part=>part==='__pycache__') && !file.endsWith('.pyc')});
    }
    const manifest = JSON.parse(await fs.readFile(path.join(packageRoot,'package.json'),'utf8'));
    delete manifest.scripts;
    await copyFiles(sdk,path.join(target,'synth'),files);
    await fs.writeFile(path.join(target,'synth-release.json'),JSON.stringify({
      format:1, source_revision:revision, engine_source:'chimepond/jottune-synth',
    },null,2)+'\n');
    // The source checkout stays private. Expose a publishable manifest only
    // after its SDK and source-revision record have been assembled successfully.
    manifest.private = false;
    await fs.writeFile(path.join(target,'package.json'),JSON.stringify(manifest,null,2)+'\n');
    return {output:target,synth_revision:revision,synth_files:files.length};
  } catch(error) {
    await fs.rm(target,{recursive:true,force:true});
    throw error;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args=process.argv.slice(2);let synthRoot,output;
    while(args.length) {
      const option=args.shift(),value=args.shift();
      if(!value || value.startsWith('--'))throw Error('Usage: node scripts/package.mjs --output NEW_DIRECTORY [--synth-root PATH]');
      if(option==='--synth-root' && synthRoot===undefined)synthRoot=value;
      else if(option==='--output' && output===undefined)output=value;
      else throw Error('Unknown or repeated package argument.');
    }
    if(!output)throw Error('Supply --output NEW_DIRECTORY.');
    console.log(JSON.stringify(await stagePackage({synthRoot,output})));
  } catch(error) {console.error(error.message);process.exitCode=2;}
}
