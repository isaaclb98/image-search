/**
 * usePagedAlbumList — shared pagination + optimistic-remove
 * controller for the Likes and Dislikes built-in albums.
 *
 * Both pages used to be 80% identical:
 *   - same state shape (items, loading, loadingMore, error,
 *     offset, hasMore)
 *   - same load() / loadMore() flow
 *   - same optimistic-remove pattern (drop from list, call
 *     API, restore on failure)
 *   - same template structure
 *   - only the endpoint function and copy strings differed.
 *
 * The composable owns the loading/pagination/error state and
 * the remove action; the page owns the endpoint wiring and
 * the copy.
 *
 * `useIndexMap: true` rebuilds an O(1) SvelteMap<id,index>
 * shadow of the items array on every change. Likes uses this
 * (the perf win matters at 1000+ items). Dislikes uses a
 * simple filter (sparse use, the O(n) is fine).
 */
import { SvelteMap } from 'svelte/reactivity';

export type PagedAlbumOptions = {
  /** First page size. */
  pageSize: number;
  /** Fetch a single page. The endpoint is `as_results=1` —
   * the backend returns a SearchResponse envelope with
   * `results` + `has_more`. */
  fetchPage: (
    limit: number,
    offset: number,
    signal?: AbortSignal
  ) => Promise<{
    results?: Array<{ id: string; [k: string]: unknown }>;
    has_more?: boolean;
  }>;
  /** Optimistic-remove a single id. The composable
   * splices the item out of the list before the call
   * and restores the prior reference on failure. */
  onRemove: (id: string) => Promise<unknown>;
  /** Build an O(1) SvelteMap<id,index> shadow. Off by
   * default; Likes opts in. */
  useIndexMap?: boolean;
};

export type PagedAlbumController<T extends { id: string }> = {
  items: T[];
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  indexById: SvelteMap<string, number> | null;
  load: () => Promise<void>;
  loadMore: (signal?: AbortSignal) => Promise<void>;
  remove: (id: string) => Promise<void>;
};

export function usePagedAlbumList<T extends { id: string }>(
  opts: PagedAlbumOptions
): PagedAlbumController<T> {
  let items = $state<T[]>([]);
  let loading = $state(true);
  let loadingMore = $state(false);
  let error = $state<string | null>(null);
  let offset = $state(0);
  let hasMore = $state(false);
  let indexById: SvelteMap<string, number> | null = opts.useIndexMap
    ? new SvelteMap()
    : null;

  async function load() {
    loading = true;
    try {
      const res = await opts.fetchPage(opts.pageSize, 0);
      const fresh = (res.results ?? []) as T[];
      items = fresh;
      offset = fresh.length;
      hasMore = !!res.has_more && fresh.length >= opts.pageSize;
      rebuildIndex();
      error = null;
    } catch (e: unknown) {
      error = (e as Error).message ?? 'Failed to load';
    } finally {
      loading = false;
    }
  }

  async function loadMore(signal?: AbortSignal) {
    if (loading || loadingMore || !hasMore) return;
    loadingMore = true;
    try {
      const res = await opts.fetchPage(opts.pageSize, offset, signal);
      const more = (res.results ?? []) as T[];
      items = [...items, ...more];
      offset += more.length;
      hasMore = !!res.has_more && more.length >= opts.pageSize;
      rebuildIndex();
    } catch {
      // Leave the existing list intact; the user can keep
      // paging — losing scroll progress on a transient
      // error is worse than a stuck spinner. A clean cancel
      // from the pre-fetch retrigger is silent — no
      // hasMore=false penalty.
      if (signal?.aborted) return;
    } finally {
      loadingMore = false;
    }
  }

  async function remove(id: string) {
    // O(1) splice when the index map is available, O(n)
    // filter otherwise. Either way, capture the prior
    // reference for rollback.
    const before = items;
    if (indexById) {
      const idx = indexById.get(id);
      if (idx === undefined) return;
      const next = items.slice();
      next.splice(idx, 1);
      items = next;
    } else {
      items = items.filter((it) => it.id !== id);
    }
    try {
      await opts.onRemove(id);
      rebuildIndex();
    } catch {
      items = before;
    }
  }

  function rebuildIndex() {
    if (!indexById) return;
    const map = new SvelteMap<string, number>();
    for (let i = 0; i < items.length; i++) {
      map.set(items[i].id, i);
    }
    indexById = map;
  }

  return {
    get items() {
      return items;
    },
    get loading() {
      return loading;
    },
    get loadingMore() {
      return loadingMore;
    },
    get error() {
      return error;
    },
    get hasMore() {
      return hasMore;
    },
    get indexById() {
      return indexById;
    },
    load,
    loadMore,
    remove
  };
}
