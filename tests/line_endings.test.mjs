// SPDX-License-Identifier: Apache-2.0
// Skipped outside a git checkout of this repository.
import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const git = args => execFileSync('git', args, {cwd: ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore']});

test('every tracked script with a shebang checks out with LF endings', t => {
  let top;
  try { top = git(['rev-parse', '--show-toplevel']).trim(); } catch { t.skip('not a git checkout'); return; }
  if (fs.realpathSync(top) !== fs.realpathSync(ROOT)) { t.skip('not the root of this repository'); return; }
  const scripts = git(['ls-files', '-z']).split('\0').filter(name => {
    try { return fs.readFileSync(path.join(ROOT, name)).subarray(0, 2).toString('latin1') === '#!'; } catch { return false; }
  });
  assert.ok(scripts.length >= 5, scripts.join(' '));
  const found = new Map(git(['check-attr', 'text', 'eol', '--', ...scripts]).trim().split('\n').map(line => {
    const at = line.lastIndexOf(': '), rest = line.slice(0, at), key = rest.slice(rest.lastIndexOf(': ') + 2);
    return [rest.slice(0, rest.lastIndexOf(': ')) + ' ' + key, line.slice(at + 2)];
  }));
  for (const name of scripts) {
    assert.equal(found.get(name + ' eol'), 'lf', name);
    assert.notEqual(found.get(name + ' text'), 'unset', name);
  }
});
