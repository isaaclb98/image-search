/**
 * Unit tests for lib/state/urlState.
 *
 * The three exports are independent pure functions:
 *   - readUrl: parses URLSearchParams into a typed record.
 *   - writeUrl: serializes a typed record back to a
 *     query string.
 *   - pushUrlSearch: calls history.replaceState.
 *
 * readUrl and writeUrl are inverses (modulo default
 * elision). pushUrlSearch uses jsdom's window.history
 * which is real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readUrl, writeUrl, pushUrlSearch } from './urlState';

type Schema = {
  positives: { kind: 'array'; param: 'positives' };
  negatives: { kind: 'array'; param: 'negatives' };
  filename: { kind: 'string'; param: 'filename' };
  collections: { kind: 'array'; param: 'collection' };
  centroid: { kind: 'string'; param: 'centroid' };
};

const schema: Schema = {
  positives: { kind: 'array', param: 'positives' },
  negatives: { kind: 'array', param: 'negatives' },
  filename: { kind: 'string', param: 'filename' },
  collections: { kind: 'array', param: 'collection' },
  centroid: { kind: 'string', param: 'centroid' }
};

describe('readUrl', () => {
  it('parses an empty search string', () => {
    const out = readUrl('', schema);
    expect(out.positives).toEqual([]);
    expect(out.negatives).toEqual([]);
    expect(out.filename).toBe('');
    expect(out.collections).toEqual([]);
    expect(out.centroid).toBe('');
  });

  it('parses leading "?" the same as no "?"', () => {
    const a = readUrl('?positives=cat', schema);
    const b = readUrl('positives=cat', schema);
    expect(a.positives).toEqual(['cat']);
    expect(b.positives).toEqual(['cat']);
  });

  it('parses array fields with repeated params', () => {
    const out = readUrl('?positives=cat&positives=dog', schema);
    expect(out.positives).toEqual(['cat', 'dog']);
  });

  it('parses string fields', () => {
    const out = readUrl('?filename=foo.jpg&centroid=album_2', schema);
    expect(out.filename).toBe('foo.jpg');
    expect(out.centroid).toBe('album_2');
  });

  it('ignores params not in the schema', () => {
    const out = readUrl('?positives=cat&unknown=xyz', schema);
    expect(out.positives).toEqual(['cat']);
    // The unknown key doesn't appear on the state object.
    expect((out as Record<string, unknown>).unknown).toBeUndefined();
  });
});

describe('writeUrl', () => {
  it('serializes array fields as repeated params', () => {
    const out = writeUrl(
      {
        positives: ['cat', 'dog'],
        negatives: [],
        filename: '',
        collections: [],
        centroid: ''
      },
      schema
    );
    expect(out).toBe('positives=cat&positives=dog');
  });

  it('serializes string fields as single param', () => {
    const out = writeUrl(
      {
        positives: [],
        negatives: [],
        filename: 'foo.jpg',
        collections: [],
        centroid: 'album_2'
      },
      schema
    );
    expect(out).toContain('filename=foo.jpg');
    expect(out).toContain('centroid=album_2');
  });

  it('omits fields that are at the default value', () => {
    const out = writeUrl(
      {
        positives: [],
        negatives: [],
        filename: '',
        collections: [],
        centroid: ''
      },
      schema,
      {
        positives: [],
        negatives: [],
        filename: '',
        collections: [],
        centroid: ''
      }
    );
    expect(out).toBe('');
  });

  it('omits empty string fields even without explicit defaults', () => {
    const out = writeUrl(
      {
        positives: ['cat'],
        negatives: [],
        filename: '',
        collections: [],
        centroid: ''
      },
      schema
    );
    expect(out).toBe('positives=cat');
    // No "filename=" for empty string.
    expect(out).not.toContain('filename=');
  });

  it('roundtrips through readUrl', () => {
    const original = {
      positives: ['cat', 'dog'],
      negatives: ['rabbit'],
      filename: 'p.jpg',
      collections: ['album_1', 'album_2'],
      centroid: 'album_3'
    };
    const serialized = writeUrl(original, schema);
    const parsed = readUrl(serialized, schema);
    expect(parsed).toEqual(original);
  });
});

describe('pushUrlSearch', () => {
  beforeEach(() => {
    // Reset jsdom history to a known starting URL.
    window.history.replaceState(null, '', '/');
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('calls history.replaceState with the new search', () => {
    const spy = vi.spyOn(window.history, 'replaceState');
    pushUrlSearch('', 'positives=cat');
    expect(spy).toHaveBeenCalledOnce();
    const [state, title, url] = spy.mock.calls[0];
    expect(state).toBe(window.history.state);
    expect(title).toBe('');
    expect(url).toBe('/?positives=cat');
  });

  it('is a no-op when the new search equals the current one', () => {
    // First write 'foo=bar' into the URL.
    window.history.replaceState(null, '', '/?foo=bar');
    const spy = vi.spyOn(window.history, 'replaceState');
    // Asking for the same search should be a no-op.
    pushUrlSearch('?foo=bar', 'foo=bar');
    expect(spy).not.toHaveBeenCalled();
  });

  it('writes "/" (no query) when the new search is empty', () => {
    const spy = vi.spyOn(window.history, 'replaceState');
    pushUrlSearch('?positives=cat', '');
    expect(spy).toHaveBeenCalledOnce();
    const url = spy.mock.calls[0][2];
    expect(url).toBe('/');
  });

  it('preserves the history.state across the call', () => {
    window.history.replaceState({ custom: 'marker' }, '', '/');
    const spy = vi.spyOn(window.history, 'replaceState');
    pushUrlSearch('', 'positives=cat');
    expect(spy.mock.calls[0][0]).toEqual({ custom: 'marker' });
  });
});
