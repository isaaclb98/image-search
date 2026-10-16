/**
 * Unit tests for scripts/check-css-tokens.mjs. The script
 * is a small CLI; we exercise both the "pass" path (the
 * real src/ tree) and the "fail" path (a temp tree with
 * one violation planted in it).
 */
import { describe, it, expect } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const script = path.resolve(
  import.meta.dirname,
  '..',
  '..',
  '..',
  'scripts',
  'check-css-tokens.mjs'
);

function runIn(cwd: string, src: string): { exit: number; stdout: string; stderr: string } {
  try {
    const out = execFileSync('node', [script, `--src=${src}`], {
      encoding: 'utf8',
      cwd,
      stdio: 'pipe'
    });
    return { exit: 0, stdout: out, stderr: '' };
  } catch (e) {
    const err = e as { status?: number; stdout?: Buffer; stderr?: Buffer };
    return {
      exit: err.status ?? 1,
      stdout: err.stdout?.toString() ?? '',
      stderr: err.stderr?.toString() ?? ''
    };
  }
}

describe('check-css-tokens', () => {
  it('passes on the current source tree', () => {
    const r = runIn(
      path.resolve(import.meta.dirname, '..', '..', '..'),
      path.resolve(import.meta.dirname, '..', '..', '..', 'src')
    );
    expect(r.exit).toBe(0);
    expect(r.stdout).toContain('CSS token check: clean.');
  });

  it('fails when a hardcoded box-shadow rgba appears in src/', () => {
    const tmp = mkdtempSync(path.join(tmpdir(), 'css-tokens-'));
    const srcDir = path.join(tmp, 'src');
    mkdirSync(path.join(srcDir, 'lib', 'components'), { recursive: true });
    writeFileSync(
      path.join(srcDir, 'lib', 'components', 'Bad.svelte'),
      '<style>\n  .x {\n    box-shadow: 0 1px 2px rgba(0,0,0,0.04);\n  }\n</style>\n'
    );
    const r = runIn(tmp, srcDir);
    rmSync(tmp, { recursive: true, force: true });
    expect(r.exit).toBe(1);
    expect(r.stderr).toContain('box-shadow');
    expect(r.stderr).toContain('Bad.svelte');
  });

  it('ignores rgba values in tokens.css (the source of truth)', () => {
    const tmp = mkdtempSync(path.join(tmpdir(), 'css-tokens-'));
    const srcDir = path.join(tmp, 'src');
    mkdirSync(path.join(srcDir, 'lib', 'styles'), { recursive: true });
    writeFileSync(
      path.join(srcDir, 'lib', 'styles', 'tokens.css'),
      ':root { --shadow-1: 0 1px 2px rgba(0,0,0,0.04); }\n'
    );
    const r = runIn(tmp, srcDir);
    rmSync(tmp, { recursive: true, force: true });
    expect(r.exit).toBe(0);
  });
});
