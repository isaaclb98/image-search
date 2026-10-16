/**
 * Unit tests for usePagedAlbumList.
 *
 * Covers:
 *   - load(): items/hasMore populated from the page response.
 *   - load(): error path leaves a useful error string.
 *   - loadMore(): appends to existing list.
 *   - loadMore(): abort signal is non-fatal.
 *   - loadMore(): hasMore false → no fetch.
 *   - remove(): splices item out (useIndexMap path).
 *   - remove(): filter fallback path.
 *   - remove(): rollback on API failure.
 *   - indexById: rebuilt correctly after remove.
 */
import { describe, it, expect, vi } from 'vitest';
import { usePagedAlbumList } from './usePagedAlbumList.svelte';

type Item = { id: string; path?: string };

function makeController(opts: {
  useIndexMap?: boolean;
  onRemove?: ReturnType<typeof vi.fn>;
} = {}) {
  const pages: Array<{ results: Item[]; has_more: boolean }> = [
    { results: [{ id: 'a' }, { id: 'b' }, { id: 'c' }], has_more: true },
    { results: [{ id: 'd' }, { id: 'e' }], has_more: false }
  ];
  let p = 0;
  const fetchPage = vi.fn(async (limit: number, offset: number) => {
    if (offset === 0) p = 0;
    return pages[p++];
  });
  const onRemove = opts.onRemove ?? vi.fn().mockResolvedValue(undefined);
  return usePagedAlbumList<Item>({
    pageSize: 3,
    fetchPage,
    onRemove,
    useIndexMap: opts.useIndexMap
  });
}

describe('usePagedAlbumList', () => {
  it('load: populates items and hasMore', async () => {
    const c = makeController();
    await c.load();
    expect(c.items.map((i) => i.id)).toEqual(['a', 'b', 'c']);
    expect(c.hasMore).toBe(true);
    expect(c.loading).toBe(false);
  });

  it('load: error path leaves a useful error string', async () => {
    const c = usePagedAlbumList<Item>({
      pageSize: 3,
      fetchPage: vi.fn().mockRejectedValue(new Error('boom')),
      onRemove: vi.fn()
    });
    await c.load();
    expect(c.error).toBe('boom');
    expect(c.items).toEqual([]);
  });

  it('loadMore: appends to existing list', async () => {
    const c = makeController();
    await c.load();
    await c.loadMore();
    expect(c.items.map((i) => i.id)).toEqual(['a', 'b', 'c', 'd', 'e']);
    expect(c.hasMore).toBe(false);
  });

  it('loadMore: abort signal is non-fatal', async () => {
    const c = usePagedAlbumList<Item>({
      pageSize: 3,
      fetchPage: vi
        .fn()
        .mockResolvedValueOnce({ results: [{ id: 'a' }], has_more: true })
        .mockImplementationOnce(
          (_l: number, _o: number, signal?: AbortSignal) =>
            new Promise((_resolve, reject) => {
              signal?.addEventListener('abort', () =>
                reject(new DOMException('aborted', 'AbortError'))
              );
            })
        ),
      onRemove: vi.fn()
    });
    await c.load();
    const ctrl = new AbortController();
    ctrl.abort();
    await c.loadMore(ctrl.signal);
    // Existing list intact, hasMore unchanged.
    expect(c.items.map((i) => i.id)).toEqual(['a']);
  });

  it('loadMore: hasMore false → no fetch', async () => {
    const fetchPage = vi.fn();
    const c = usePagedAlbumList<Item>({
      pageSize: 3,
      fetchPage: fetchPage.mockResolvedValueOnce({
        results: [{ id: 'a' }],
        has_more: false
      }),
      onRemove: vi.fn()
    });
    await c.load();
    fetchPage.mockClear();
    await c.loadMore();
    expect(fetchPage).not.toHaveBeenCalled();
  });

  it('remove: splices item out (useIndexMap path)', async () => {
    const c = makeController({ useIndexMap: true });
    await c.load();
    await c.remove('b');
    expect(c.items.map((i) => i.id)).toEqual(['a', 'c']);
    // indexById reflects the new ordering.
    expect(c.indexById?.get('a')).toBe(0);
    expect(c.indexById?.get('c')).toBe(1);
  });

  it('remove: filter fallback (no index map)', async () => {
    const c = makeController({ useIndexMap: false });
    await c.load();
    await c.remove('b');
    expect(c.items.map((i) => i.id)).toEqual(['a', 'c']);
    expect(c.indexById).toBe(null);
  });

  it('remove: rolls back on API failure', async () => {
    const onRemove = vi.fn().mockRejectedValue(new Error('boom'));
    const c = makeController({ onRemove });
    await c.load();
    await c.remove('b');
    expect(c.items.map((i) => i.id)).toEqual(['a', 'b', 'c']);
  });

  it('remove: unknown id is a silent no-op', async () => {
    const c = makeController();
    await c.load();
    await c.remove('does-not-exist');
    expect(c.items.map((i) => i.id)).toEqual(['a', 'b', 'c']);
  });
});
