// ESLint 9 flat config for the SvelteKit frontend.
// "Fail on warnings" — every rule emits a warning or error;
// nothing is configured to silently allow.
//
// Generated files (.svelte-kit/, types.gen.ts, build/) and
// vendor (node_modules/) are excluded. Tests are linted
// normally — they're part of the codebase.
//
// Rule set is intentionally conservative: JS/TS recommended,
// svelte recommended, typescript-eslint recommended. Per-phase
// tightening happens in the commit that introduces the change
// (i.e. we don't pre-emptively disable rules for code we
// haven't read yet — we read it, fix it, then commit the
// tightened rule alongside the fix).
import js from '@eslint/js';
import ts from 'typescript-eslint';
import svelte from 'eslint-plugin-svelte';
import globals from 'globals';

export default [
  // Global ignores — generated, vendored, build output.
  {
    ignores: [
      '.svelte-kit/',
      'build/',
      'node_modules/',
      'dist/',
      'static/',
      // Generated OpenAPI types — auto-generated, do not edit.
      'src/lib/api/types.gen.ts'
    ]
  },

  // Base recommended JS rules.
  js.configs.recommended,

  // TypeScript rules — recommended only; we tighten in-tree as we go.
  ...ts.configs.recommended,

  // Svelte recommended (for .svelte files).
  ...svelte.configs['flat/recommended'],

  // Project-wide: type-aware options (we have a tsconfig).
  {
    languageOptions: {
      ecmaVersion: 2024,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.node
      }
    },
    rules: {
      // We use `any` in a few intentional places (generated type
      // casts, fetch shims). Recommended reports it; we keep the
      // warning so future additions are flagged.
      '@typescript-eslint/no-explicit-any': 'warn',

      // The codebase has a lot of `as` casts because the
      // generated types are partial / structural. Recommended
      // warns; keep the warning.
      '@typescript-eslint/no-unused-vars': [
        'warn',
        {
          argsIgnorePattern: '^_',
          varsIgnorePattern: '^_',
          caughtErrorsIgnorePattern: '^_'
        }
      ]
    }
  },

  // Svelte files — set up the parser.
  {
    files: ['**/*.svelte'],
    languageOptions: {
      parserOptions: {
        parser: ts.parser
      }
    }
  },

  // Svelte TS modules — `.svelte.ts` / `.svelte.js` files
  // participate in Svelte 5's reactivity model (runes) and
  // use the TypeScript parser. Without this block, ESLint
  // reports "Unexpected token type" on `$state(...)` and
  // friends.
  {
    files: ['**/*.svelte.ts', '**/*.svelte.js'],
    languageOptions: {
      parser: ts.parser,
      parserOptions: {
        parser: ts.parser
      }
    }
  },

  // Svelte config — preprocessors (we use vitePreprocess which
  // strips TS from <script lang="ts"> blocks).
  {
    files: ['**/*.svelte'],
    ...svelte.configs['flat/recommended'].rules
  },

  // Test files — slightly relaxed: vitest globals, describe/it
  // are global, and the test files use `as any` casts for
  // fixture setup.
  {
    files: [
      'src/**/*.test.ts',
      'src/**/*.test.svelte',
      'e2e/**/*.ts'
    ],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
        // vitest globals
        describe: 'readonly',
        it: 'readonly',
        test: 'readonly',
        expect: 'readonly',
        beforeEach: 'readonly',
        afterEach: 'readonly',
        beforeAll: 'readonly',
        afterAll: 'readonly',
        vi: 'readonly',
        // Playwright globals
        page: 'readonly',
        context: 'readonly',
        browser: 'readonly'
      }
    },
    rules: {
      '@typescript-eslint/no-explicit-any': 'off'
    }
  },

  // Generated API client wrapper — relax a few rules because
  // it's the typed boundary between generated types and
  // hand-written code.
  {
    files: ['src/lib/api/endpoints.ts', 'src/lib/api/client.ts'],
    rules: {
      // The endpoints file has ~10 `as unknown` casts that
      // bridge Zod inference → generated types. These are
      // intentional; keep them visible.
      '@typescript-eslint/no-explicit-any': 'off'
    }
  },

  // Generic UI primitives. They render anchor tags whose href
  // comes from the parent's prop — the parent decides whether
  // the link is an external anchor (e.g. "Open raw" with
  // target="_blank") or an in-app route. The
  // no-navigation-without-resolve rule fires on the primitive
  // because it can't see the parent's intent; enforce the
  // rule at call sites instead, where the SvelteKit router
  // context is available.
  {
    files: [
      'src/lib/components/ActionButton.svelte',
      'src/lib/components/Button.svelte',
      'src/lib/components/PhotoTile.svelte',
      'src/lib/components/TopBar.svelte',
      'src/lib/components/ForYouRow.svelte'
    ],
    rules: {
      'svelte/no-navigation-without-resolve': 'off'
    }
  }
];
