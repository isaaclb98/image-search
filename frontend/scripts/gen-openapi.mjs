#!/usr/bin/env node
// Re-export of the python dump-openapi.py script. Triggers the
// backend to serialise openapi.json next to this script so the
// typescript/zod generators below have stable input. Run from
// this directory: `npm run gen:openapi`.

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, '..', '..');

execFileSync(
  '.venv-test/bin/python',
  ['scripts/dump-openapi.py'],
  { cwd: repo, stdio: 'inherit' }
);
