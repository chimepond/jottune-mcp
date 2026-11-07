// Copyright (c) 2026 Nenad Micic
// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

export const packageRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const required = ['Makefile', 'tools/fm.py', 'tools/interchange.py', 'docs/INSTRUMENT.md', 'src/host/harness.c'];

export function resolveSDK(explicit, {root = packageRoot, env = process.env} = {}) {
  const selected = explicit ?? env.JOTTUNE_SYNTH_ROOT;
  const candidates = selected === undefined ? [path.join(root, 'synth'), path.join(root, '../jottune-synth')] : [selected];
  for (const candidate of candidates) {
    if (typeof candidate !== 'string' || !candidate.trim()) continue;
    const directory = path.resolve(candidate);
    try {
      if (required.every(file => fs.statSync(path.join(directory, file)).isFile())) return fs.realpathSync(directory);
    } catch {}
  }
  throw Error('A complete JotTune Synth SDK is required. Set JOTTUNE_SYNTH_ROOT or --synth-root PATH, use a package with bundled synth/, or place jottune-synth beside this checkout.');
}
