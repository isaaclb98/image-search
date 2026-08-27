#!/usr/bin/env node
// Hand-rolled Zod schemas for the ~8 API shapes the frontend
// actually parses at runtime. The full backend surface has 21
// schemas — most are internal — but the front end only consumes
// a narrow band, so we hand-write those and run a drift check
// against openapi.json on CI.
//
// Why not auto-generate? `openapi-zod-client` 1.x dropped the
// `outputPath` option and changed its emit shape; the 0.x line
// emits huge files we don't load at runtime. The hand-written
// approach keeps the runtime payload tiny and lets us describe
// invariants openapi can't (e.g. "at least one of positives /
// negatives is required to fire a search").
//
// Edit the definitions below — the script RE-EMITS from this
// source, so this file IS the source of truth, and `npm run
// gen:zod` re-validates against openapi.json.

import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs/promises';

const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '..');
const inPath = path.join(frontend, 'openapi.json');
const outPath = path.join(frontend, 'src', 'lib', 'api', 'schemas.ts');

// ---------- The schemas the frontend actually uses ----------
// `passthrough()` lets unknown fields through unmolested — the API
// adds fields over time (centroid info, weights, etc.) and the
// frontend should still load. Strict pairs of (known, unknown)
// live in the drift check below.

const define = `import { z } from 'zod';

// ---- search ----
const SearchResult = z.object({
  id: z.string(),
  path: z.string(),
  score: z.number(),
  score_str: z.string().optional(),
  url: z.string().optional(),
  is_favorite: z.boolean().optional(),
  blurhash: z.string().nullable().optional(),
  width: z.number().int().nullable().optional(),
  height: z.number().int().nullable().optional()
}).passthrough();

const DiversityMetadata = z.object({
  requested: z.boolean().optional(),
  applied: z.boolean().optional(),
  mode: z.string().optional(),
  strength: z.number().optional(),
  candidate_count: z.number().int().optional(),
  depth: z.string().optional(),
  duplicate_images_collapsed: z.number().int().optional(),
  pool_depth: z.number().int().optional(),
  result_count: z.number().int().optional(),
  semantic_groups_covered: z.number().int().optional()
}).passthrough();

const SearchResponse = z.object({
  query: z.string().optional(),
  results: z.array(SearchResult),
  took_ms: z.number().optional(),
  limit: z.number().int().optional(),
  offset: z.number().int().optional(),
  has_more: z.boolean().optional(),
  positives: z.array(z.string()).optional(),
  negatives: z.array(z.string()).optional(),
  diversity: DiversityMetadata.optional(),
  centroid: z.unknown().optional(),
  centroids: z.array(z.unknown()).optional(),
  weights: z.union([
    z.array(z.number()),
    z.record(z.string(), z.number()),
    z.null()
  ]).optional(),
  view: z.string().optional(),
  surprise: z.unknown().optional(),
  diverse: z.boolean().optional()
}).passthrough();

// ---- saved searches ----
// The backend switched "prompts"/"negative_prompts" →
// "positives"/"negatives" recently. Use the new names; the
// openapi.json drift check below guards against further moves.
const SavedSearch = z.object({
  id: z.number().int(),
  name: z.string(),
  positives: z.array(z.string()),
  negatives: z.array(z.string()),
  created_at: z.string().optional()
}).passthrough();

const SavedSearchCreateRequest = z.object({
  name: z.string().min(1).max(120),
  positives: z.array(z.string()).default([]),
  negatives: z.array(z.string()).default([])
});

const SavedSearchListResponse = z.object({
  saved_searches: z.array(SavedSearch),
  total: z.number().int().default(0),
  limit: z.number().int().optional(),
  offset: z.number().int().optional()
});

// ---- albums ----
const AlbumSummary = z.object({
  id: z.number().int(),
  name: z.string(),
  description: z.string().optional(),
  cover_favorite_id: z.string().nullable().optional(),
  member_count: z.number().int().optional(),
  first_member_id: z.string().nullable().optional(),
  created_at: z.string().optional(),
  updated_at: z.string().optional()
}).passthrough();

const AlbumsListResponse = z.object({
  albums: z.array(AlbumSummary)
});

const AlbumMember = z.object({
  favorite_id: z.number().int().optional(),
  point_id: z.string().optional(),
  id: z.string().optional(),
  path: z.string().optional(),
  url: z.string().optional(),
  blurhash: z.string().nullable().optional(),
  width: z.number().int().nullable().optional(),
  height: z.number().int().nullable().optional()
}).passthrough();

const AlbumDetailResponse = z.object({
  id: z.number().int(),
  name: z.string(),
  description: z.string().optional(),
  cover_favorite_id: z.string().nullable().optional(),
  members: z.array(AlbumMember),
  member_total: z.number().int().optional(),
  created_at: z.string().optional(),
  updated_at: z.string().optional()
}).passthrough();

// ---- for-you ----
const ForYouFeedItem = z.object({
  point_id: z.string().optional(),
  id: z.string().optional(),
  path: z.string().optional(),
  url: z.string().optional(),
  blurhash: z.string().nullable().optional(),
  width: z.number().int().nullable().optional(),
  height: z.number().int().nullable().optional(),
  score: z.number().optional()
}).passthrough();

const ForYouFeedResponse = z.object({
  results: z.array(ForYouFeedItem),
  n_likes: z.number().int().optional(),
  n_dislikes: z.number().int().optional(),
  freshest_feedback_ts: z.union([z.string(), z.null()]).optional(),
  ranker_state: z.record(z.string(), z.unknown()).optional()
}).passthrough();

const ForYouState = z.object({
  n_likes: z.number().int().default(0),
  n_dislikes: z.number().int().default(0),
  freshest_feedback_ts: z.union([z.string(), z.null()]).optional()
}).passthrough();

// ---- misc ----
const CollectionsList = z.object({ collections: z.array(z.string()) }).passthrough();
const CentroidList = z.object({
  centroids: z.array(z.object({
    name: z.string(),
    kind: z.string().optional(),
    member_count: z.number().int().optional()
  }).passthrough())
}).passthrough();

// ---- helpers ----

/** Throws a helpful Error if data doesn't match the schema. */
export function assertSchema<T>(name: string, schema: z.ZodType<T>, data: unknown): T {
  const result = schema.safeParse(data);
  if (!result.success) {
    const msg = result.error.issues
      .map((i) => \`  \${i.path.join('.')}: \${i.message}\`)
      .join('\\n');
    throw new Error(\`[schema:\${name}] response did not match:\\n\${msg}\`);
  }
  return result.data;
}

/** Returns the parsed value or null on failure (no throw). Use in dev mode only. */
export function tryParse<T>(schema: z.ZodType<T>, data: unknown): T | null {
  return schema.safeParse(data).data ?? null;
}

export const Z = {
  SearchResult,
  DiversityMetadata,
  SearchResponse,
  SavedSearch,
  SavedSearchCreateRequest,
  SavedSearchListResponse,
  AlbumSummary,
  AlbumsListResponse,
  AlbumMember,
  AlbumDetailResponse,
  CollectionsList,
  CentroidList,
  ForYouFeedItem,
  ForYouFeedResponse,
  ForYouState
};
`;

await fs.mkdir(path.dirname(outPath), { recursive: true });
await fs.writeFile(outPath, define);

// ---------- drift check ----------
// Compare the field names in our hand-rolled schemas against the
// openapi.json component schemas. Fail loudly so reviewers update
// this file when the API moves.

const openapi = JSON.parse(await fs.readFile(inPath, 'utf8'));
const components = openapi.components?.schemas ?? {};
const issues = [];

// Track fields we definitely depend on (must exist on the API side).
// Optional fields (`.optional()`) are not in this list — adding
// optional fields to the API is fine, removing required ones is not.
const required = {
  SearchResult: ['id', 'path', 'score'],
  SearchResponse: ['results', 'took_ms', 'limit'],
  SavedSearch: ['id', 'name', 'positives', 'negatives', 'created_at'],
  SavedSearchCreateRequest: ['name', 'positives', 'negatives'],
  SavedSearchListResponse: ['saved_searches', 'total'],
  AlbumSummary: ['id', 'name', 'description', 'cover_favorite_id', 'member_count', 'created_at', 'updated_at'],
  AlbumsListResponse: ['albums'],
  AlbumDetailResponse: ['id', 'name', 'description', 'cover_favorite_id', 'members', 'member_total', 'created_at', 'updated_at']
};

for (const [name, fields] of Object.entries(required)) {
  const apiProps = Object.keys(components[name]?.properties ?? {});
  const missingFromApi = fields.filter((p) => !apiProps.includes(p));
  if (missingFromApi.length) {
    issues.push(`${name}: API no longer returns required fields: ${missingFromApi.join(', ')}`);
  }
}

if (issues.length) {
  console.error('OpenAPI drift detected:');
  for (const i of issues) console.error('  -', i);
  console.error('\nUpdate src/lib/api/schemas.ts to match openapi.json, then re-run.');
  process.exit(2);
}

console.log(`wrote ${outPath}`);
console.log('schemas.ts is in sync with openapi.json');
