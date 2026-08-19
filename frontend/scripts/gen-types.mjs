#!/usr/bin/env node
// Generates TypeScript types from frontend/openapi.json into
// src/lib/api/types.gen.ts via openapi-typescript. This is the
// compile-time backbone of the API client.

import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';
import openapiTS, { astToString } from 'openapi-typescript';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '..');
const inPath = path.join(frontend, 'openapi.json');
const outPath = path.join(frontend, 'src', 'lib', 'api', 'types.gen.ts');

const ast = await openapiTS(new URL('file://' + inPath));
const source = astToString(ast);

await fs.mkdir(path.dirname(outPath), { recursive: true });
await fs.writeFile(outPath, source);
console.log(`wrote ${outPath} (${source.length.toLocaleString()} bytes)`);
