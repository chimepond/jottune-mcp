#!/usr/bin/env node
// Copyright (c) 2026 Nenad Micic
// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn, spawnSync} from 'node:child_process';
import {packageRoot, resolveSDK} from '../lib/sdk.mjs';

const launcher = fileURLToPath(import.meta.url);
const args = process.argv.slice(2);
const command = args.length && !args[0].startsWith('-') ? args.shift() : 'serve';
function fail(message) { console.error('JotTune MCP: ' + message); process.exit(2); }
function option(name, fallback) {
  const index = args.indexOf(name);
  if (index === -1) return fallback;
  if (!args[index + 1] || args[index + 1].startsWith('--')) fail(name + ' needs a path.');
  const value = args[index + 1];
  args.splice(index, 2);
  return value;
}
const hasWorkspace = args.includes('--workspace');
const hasHarness = args.includes('--harness');
const synthRoot = option('--synth-root', undefined);
const help = command !== 'interchange' && (args.includes('--help') || args.includes('-h') || command === 'help');
let root = null;
if (!help && command !== 'requirements') {
  try { root = resolveSDK(synthRoot); } catch (error) { fail(error.message); }
}
const workspace = path.resolve(option('--workspace', process.cwd()));
const harness = path.resolve(option('--harness', process.env.JOTTUNE_HARNESS || path.join(root || packageRoot, 'build/s12e10p1v1/harness')));
const python = process.env.JOTTUNE_PYTHON || 'python3';
const requirementProfiles = Object.fromEntries(['core', 'analysis', 'plots'].map(name =>
  [name, path.join(packageRoot, 'requirements', name + '.txt')]));
const requirements = requirementProfiles.core;
const installFor = file => [python, '-m', 'pip', 'install', '-r', file];
function run(executable, arguments_, options = {}, cleanup = () => {}) {
  const result = spawnSync(executable, arguments_, {stdio: 'inherit',
    env: {...process.env, JOTTUNE_SYNTH_ROOT: root}, ...options});
  if (result.error || result.status !== 0) cleanup();
  if (result.error) fail(executable === 'make' && result.error.code === 'ENOENT'
    ? 'Make is unavailable. Install Make and a C99 compiler, then run jottune-mcp build again.' : result.error.message);
  process.exit(result.status ?? 1);
}
function checkWorkspace() {
  try {
    if (!fs.statSync(workspace).isDirectory()) throw Error('not a directory');
    fs.accessSync(workspace, fs.constants.R_OK | fs.constants.W_OK | fs.constants.X_OK);
  } catch (error) { fail('Choose an existing readable/writable --workspace directory: ' + error.message); }
}
function nodePath() {
  // Prefer a persistent PATH alias over a resolved version-specific executable.
  const executable = fs.realpathSync(process.execPath);
  for (const directory of (process.env.PATH || '').split(path.delimiter)) {
    const candidate = path.resolve(directory, 'node');
    try { if (candidate !== executable && fs.realpathSync(candidate) === executable) return candidate; } catch {}
  }
  return process.execPath;
}
function pythonPath() {
  const candidates = python.includes('/') ? [path.resolve(python)] :
    (process.env.PATH || '').split(path.delimiter).map(directory => path.resolve(directory, python));
  for (const candidate of candidates) {
    try { fs.accessSync(candidate, fs.constants.X_OK); if (fs.statSync(candidate).isFile()) return candidate; } catch {}
  }
  fail('Python executable is unavailable; set JOTTUNE_PYTHON to your Python/venv executable.');
}
// One probe serves config (interpreter and MCP SDK only) and doctor (everything).
const probeScript = `import sys
# python -c puts the caller's directory first on sys.path. The server runs as a script, which puts its
# own directory there instead, so the probe finds modules the way the server will.
if sys.path and sys.path[0] == '':
    sys.path[0] = sys.argv[4]
import importlib, importlib.util, json, os, re, subprocess
mode, fm_path, harness, server, root = sys.argv[1:6]
modules = {}
for name in ('mcp.server',) if mode == 'config' else ('mcp.server', 'mcp.client', 'numpy', 'matplotlib'):
    try:
        module = importlib.import_module(name)
        if name == 'mcp.server':
            getattr(module, 'MCPServer')
        if name == 'mcp.client':
            getattr(module, 'Client')
        modules[name] = {'ok': True}
        if name == 'matplotlib':
            # Build the font cache now; otherwise the first plot waits for it.
            importlib.import_module('matplotlib.font_manager')
            modules[name]['font_cache'] = module.get_cachedir()
    except Exception as error:
        modules[name] = {'ok': False, 'error': str(error)}
report = {'version': sys.version.split()[0], 'supported': sys.version_info >= (3, 10), 'modules': modules}
if mode == 'doctor':
    sys.path.insert(0, server)
    fm = None
    try:
        spec = importlib.util.spec_from_file_location('jottune_doctor_fm', fm_path)
        fm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fm)
    except Exception as error:
        fm = None
        report['native'] = {'ok': False, 'error': str(error)}
    if fm is not None:
        # The API report has its own try, so that it cannot mark a working renderer as failed.
        try:
            import sdk
            report['api'] = {'version': getattr(fm, 'API_VERSION', None), 'supported': list(sdk.SYNTH_API_VERSIONS)}
            try:
                sdk.check_api(fm)
                report['api']['compatible'] = True
            except ValueError as error:
                report['api'].update(compatible=False, error=str(error))
        except Exception as error:
            report['api'] = {'compatible': False, 'error': str(error)}
        try:
            renderer = fm.Native(harness)
            report['native'] = {'ok': True, 'engine': renderer.metadata.engine}
        except Exception as error:
            report['native'] = {'ok': False, 'error': str(error)}
    try:
        document_spec = importlib.util.spec_from_file_location('jottune_document_format', os.path.join(root, 'tools', 'tl_document.py'))
        document_module = importlib.util.module_from_spec(document_spec)
        document_spec.loader.exec_module(document_module)
        report['tl_format'] = {'tl_version': document_module.TL_VERSION, 'engine_version': document_module.ENGINE_VERSION, 'compatible': False}
        native_format = subprocess.run([harness, '--tl-format'], capture_output=True, text=True, timeout=5)
        match = re.fullmatch(r'TL_FORMAT tl_version=([1-9][0-9]*) engine_version=([1-9][0-9]*)', native_format.stdout.strip())
        if native_format.returncode or native_format.stderr.strip() or not match:
            raise ValueError('Selected native build does not report the required TL format contract; rebuild or select the matching harness.')
        report['tl_format']['native'] = {'tl_version': int(match[1]), 'engine_version': int(match[2])}
        if report['tl_format']['native'] != {'tl_version': document_module.TL_VERSION, 'engine_version': document_module.ENGINE_VERSION}:
            raise ValueError('Selected native TL/engine compatibility versions differ from the SDK; rebuild or select the matching harness.')
        report['tl_format']['compatible'] = True
    except Exception as error:
        report.setdefault('tl_format', {}).update(compatible=False, error=str(error))
    try:
        import mcp_server
        report['server'] = {'ok': True, 'tools': len(mcp_server.create_server(harness, synth_root=root).operation_functions)}
    except Exception as error:
        report['server'] = {'ok': False, 'error': str(error)}
print(json.dumps(report))`;
function probe(executable, mode) {
  const check = spawnSync(executable, ['-B', '-c', probeScript, mode, path.join(root, 'tools/fm.py'), harness,
    path.join(packageRoot, 'server'), root], {encoding: 'utf8'});
  let details = {};
  try { details = JSON.parse(check.stdout); } catch {}
  return {check, details: details && typeof details === 'object' ? details : {}};
}
function serverProblem({check, details}) {
  if (typeof details.version !== 'string') return 'it did not report its version' + (check.error ? ' (' + check.error.message + ')' : '');
  if (details.supported !== true) return 'Python ' + details.version + ' is older than 3.10';
  const mcp = details.modules?.['mcp.server'];
  return mcp?.ok === true ? null : 'the MCP SDK is not importable (' + (mcp?.error || 'no report') + ')';
}
function synthRevision() {
  // A package records its bundled SDK; a separate checkout reports its own commit.
  try {
    if (root === path.join(packageRoot, 'synth'))
      return JSON.parse(fs.readFileSync(path.join(packageRoot, 'synth-release.json'), 'utf8')).source_revision ?? null;
  } catch { return null; }
  const git = spawnSync('git', ['-C', root, 'rev-parse', '--show-toplevel', 'HEAD'],
    {encoding: 'utf8', env: {...process.env, GIT_OPTIONAL_LOCKS: '0'}});
  const [top, revision] = git.status === 0 ? git.stdout.trim().split('\n') : [];
  try { return top && fs.realpathSync(top) === root ? revision : null; } catch { return null; }
}
if (help) {
  console.log(`JotTune MCP: local stdio server (no remote service)
Usage: jottune-mcp [serve] [--harness PATH] [--workspace DIR]
       jottune-mcp doctor | config [--harness PATH] [--workspace DIR]
       jottune-mcp svg INPUT.tl OUTPUT.svg [--harness PATH] [--workspace DIR]
       jottune-mcp interchange inspect|wav|midi|sfz|sfz-bank|verify ... [--harness PATH] [--workspace DIR]
       jottune-mcp guide | build
       jottune-mcp requirements [--analysis | --plots]
Use --synth-root PATH or JOTTUNE_SYNTH_ROOT for an explicit synth SDK.
Otherwise a bundled synth/ or sibling jottune-synth checkout is selected.
Python 3.10+ is required; requirements prints the core MCP SDK requirements.
--analysis prints the optional NumPy profile; --plots adds NumPy + Matplotlib.
Install the core profile and any optional profile explicitly in your venv.
Set JOTTUNE_PYTHON to that venv's executable. build explicitly requires Make
and a C99 compiler; --harness or JOTTUNE_HARNESS selects an existing renderer.
config prints a local MCP client configuration; it does not edit client files.
It refuses a JOTTUNE_PYTHON that cannot run the server unless --force is given.
--workspace chooses an existing session directory (default: current directory).
Tool-selected paths stay inside that workspace, including symlink targets.
Move files into it explicitly before composing, importing or exporting.
Installation never downloads or compiles an engine. No command publishes music.`);
} else {
  if ((hasHarness || hasWorkspace) && !['serve', 'doctor', 'config', 'svg', 'interchange'].includes(command))
    fail('--harness and --workspace apply to serve, doctor, config, svg and interchange.');
  if (command === 'requirements') {
    if (args.length > 1 || (args.length && !['--analysis', '--plots'].includes(args[0])))
      fail('requirements accepts either --analysis or --plots, or no flag for core MCP.');
    console.log(requirementProfiles[args.length ? args[0].slice(2) : 'core']);
  } else if (command === 'guide') {
    if (args.length) fail('guide takes no arguments.');
    process.stdout.write(fs.readFileSync(path.join(root, 'docs/INSTRUMENT.md')));
  } else if (command === 'build') {
    if (args.length) fail('build takes no arguments.');
    // Without this check Make reports only "cc: No such file or directory".
    for (const compiler of new Set([process.env.CC || 'cc', process.env.HOSTCC || 'cc'])) {
      const [executable, ...flags] = compiler.trim().split(/\s+/);
      const check = spawnSync(executable, [...flags, '--version'], {stdio: 'ignore'});
      if (check.error || check.status !== 0) fail('A C99 compiler is unavailable (' + compiler + ' did not run). ' +
        'Install one, for example with xcode-select --install on macOS or the build-essential package on Debian or Ubuntu, ' +
        'then run jottune-mcp build again.');
    }
    // Remove build directories that a failed build created and left empty.
    const created = ['build', 'build/s12e10p1v1'].map(name => path.join(root, name)).filter(name => !fs.existsSync(name));
    run('make', ['-C', root, 'all'], {}, () => {
      for (const directory of created.reverse()) try { fs.rmdirSync(directory); } catch {}
    });
  } else if (command === 'config') {
    const force = args.includes('--force');
    if (force) args.splice(args.indexOf('--force'), 1);
    if (args.length) fail('Unknown config arguments: ' + args.join(' '));
    checkWorkspace();
    const executable = pythonPath(), problem = serverProblem(probe(executable, 'config'));
    const advice = 'The selected Python (' + executable + ') cannot run the server: ' + problem +
      '. Install requirements/core.txt in a Python 3.10+ virtual environment and set JOTTUNE_PYTHON to its executable';
    if (problem && !force) fail(advice + ', or pass --force to print the configuration anyway.');
    if (problem) console.error('JotTune MCP: warning: ' + advice + '.');
    console.log(JSON.stringify({mcpServers: {jottune: {
      command: nodePath(),
      args: [launcher, 'serve', '--synth-root', root, '--harness', harness, '--workspace', workspace],
      env: {JOTTUNE_PYTHON: executable},
    }}}, null, 2));
  } else if (command === 'doctor') {
    if (args.length) fail('Unknown doctor arguments: ' + args.join(' '));
    checkWorkspace();
    const {check, details} = probe(python, 'doctor');
    const available = name => details.supported === true && details.modules?.[name]?.ok === true;
    const engine = details.native?.ok === true;
    const capabilities = {mcp: available('mcp.server') && available('mcp.client'),
      render: engine, analysis: available('numpy'), plots: available('numpy') && available('matplotlib')};
    // The server does not check the synth API itself, so ok includes it (synth_api says why).
    const ok = capabilities.mcp && capabilities.render && details.tl_format?.compatible === true && details.api?.compatible === true && details.server?.ok === true;
    console.log(JSON.stringify({ok, complete: ok && capabilities.analysis && capabilities.plots, capabilities,
      python, python_version: details.version || null, dependencies: details.modules || {},
      harness, engine: engine ? 'ENGINE ' + details.native.engine : null, root, workspace,
      synth_revision: synthRevision(), tl_format: details.tl_format || null, synth_api: details.api || null, tools: details.server?.tools ?? null,
      server_error: details.server?.error || null, requirements,
      requirements_profiles: requirementProfiles,
      install: installFor(requirements),
      install_by_capability: {mcp: installFor(requirements),
        analysis: installFor(requirementProfiles.analysis), plots: installFor(requirementProfiles.plots)},
      python_error: check.error?.message || check.stderr?.trim() || null,
      engine_error: details.native?.error || (engine ? null : 'Native compatibility could not be checked; run jottune-mcp build or supply --harness PATH and a working Python.')}, null, 2));
    process.exitCode = ok ? 0 : 1;
  } else if (command === 'interchange') {
    checkWorkspace();
    run(pythonPath(), ['-B', path.join(root, 'tools/interchange.py'), '--harness', harness, ...args], {cwd: workspace});
  } else if (command === 'serve' || command === 'svg') {
    if (command === 'serve' && args.length) fail('Unknown server arguments: ' + args.join(' '));
    if (command === 'svg' && args.length !== 2) fail('svg needs INPUT.tl and OUTPUT.svg.');
    checkWorkspace();
    if (!fs.existsSync(harness)) fail('Native harness is missing. Run jottune-mcp build or supply --harness PATH.');
    if (command === 'svg') run(process.execPath, [path.join(root, 'tools/tl_svg.mjs'), ...args, '--harness', harness], {cwd: workspace});
    const child = spawn(pythonPath(), ['-B', path.join(packageRoot, 'server/mcp_server.py'), '--synth-root', root,
      '--harness', harness], {cwd: workspace, stdio:['pipe','pipe','inherit'], env:{...process.env, JOTTUNE_SYNTH_ROOT:root}});
    process.stdin.pipe(child.stdin);
    child.stdout.pipe(process.stdout);
    child.stdin.on('error',error=>{ if (error.code !== 'EPIPE') console.error(error.message); });
    for (const signal of ['SIGTERM','SIGINT']) process.on(signal,()=>{
      process.stdin.unpipe(child.stdin);
      child.stdin.end();
      process.stdin.destroy();
      child.kill(signal);
    });
    child.on('error',error=>fail(error.message));
    child.on('exit',(code,signal)=>{process.exitCode=code ?? 128 + (os.constants.signals[signal] ?? 0);});
  } else fail('Unknown command: ' + command);
}
