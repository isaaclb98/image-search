<script lang="ts">
  /**
   * Dropdown of saved searches on the Search bar. Per spec:
   *   - newest-first
   *   - can delete a saved search (no editing — delete + re-save)
   *   - picking one re-applies its positives/negatives and
   *     triggers a search
   *
   * Surfaces a "save current search" action as well.
   */
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import {
    listSavedSearches,
    deleteSavedSearch,
    createSavedSearch
  } from '$lib/api/endpoints';
  import { toast } from './Toaster.svelte';
  import type { SavedSearch } from '$lib/api/endpoints';

  type Props = {
    positives: string[];
    negatives: string[];
    onPick: (s: SavedSearch) => void;
  };
  let { positives, negatives, onPick }: Props = $props();

  let open = $state(false);
  let items = $state<SavedSearch[]>([]);
  let loading = $state(false);

  async function refresh() {
    loading = true;
    try {
      const res = (await listSavedSearches()) as { saved_searches: SavedSearch[] };
      items = res.saved_searches ?? [];
    } catch (e) {
      toast.show('Failed to load saved searches', { kind: 'error' });
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    if (open) refresh();
  });

  function toggle() {
    open = !open;
    if (open && items.length === 0 && !loading) refresh();
  }

  async function save() {
    if (!positives.length && !negatives.length) {
      toast.show('Add at least one prompt before saving.', { kind: 'warn' });
      return;
    }
    const name = window.prompt(
      `Name this search (${positives.length}+/${negatives.length}-):`,
      ''
    );
    if (!name) return;
    try {
      await createSavedSearch({
        name: name.trim(),
        positives,
        negatives
      });
      toast.show('Saved.', { kind: 'success' });
      await refresh();
    } catch (e) {
      toast.show('Save failed.', { kind: 'error' });
    }
  }

  async function pick(s: SavedSearch) {
    onPick(s);
    open = false;
  }

  async function remove(e: MouseEvent, id: number) {
    e.stopPropagation();
    if (!window.confirm('Delete this saved search?')) return;
    try {
      await deleteSavedSearch(id);
      items = items.filter((s) => s.id !== id);
      toast.show('Deleted.');
    } catch {
      toast.show('Delete failed.', { kind: 'error' });
    }
  }
</script>

<div class="saved-search">
  <button
    class="trigger"
    type="button"
    onclick={toggle}
    aria-expanded={open}
    title="Saved searches"
  >
    Saved
    <span class="caret" aria-hidden="true">{open ? '▴' : '▾'}</span>
  </button>
  <button class="trigger save" type="button" onclick={save} title="Save current search">
    Save
  </button>
  {#if open}
    <div class="pop glass" role="menu">
      {#if loading}
        <div class="empty">Loading…</div>
      {:else if items.length === 0}
        <div class="empty">No saved searches yet.</div>
      {:else}
        <ul class="list">
          {#each items as s (s.id)}
            <li class="item-row">
              <button
                class="item"
                type="button"
                onclick={() => pick(s)}
                title={s.positives.join(', ') + (s.negatives.length ? '  −' + s.negatives.join(', −') : '')}
              >
                <span class="name">{s.name}</span>
                <span class="counts">
                  +{s.positives.length}{s.negatives.length ? ' −' + s.negatives.length : ''}
                </span>
              </button>
              <button
                class="del"
                type="button"
                onclick={(e) => remove(e, s.id)}
                aria-label="Delete {s.name}"
                title="Delete"
              >×</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}
</div>

<style>
  .saved-search {
    position: relative;
    display: inline-flex;
    gap: 6px;
  }
  .trigger {
    height: 38px;
    padding: 0 14px;
    border-radius: var(--r-pill);
    background: var(--glass-2);
    border: 1px solid var(--glass-edge-strong);
    color: var(--fg-1);
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: var(--fs-sm);
    transition: background var(--t-fast);
  }
  .trigger:hover { background: rgba(255,255,255,0.14); }
  .save {
    background: transparent;
    border-color: var(--glass-edge);
    color: var(--fg-2);
  }
  .save:hover {
    background: var(--accent-soft);
    color: var(--fg-1);
    border-color: var(--accent-soft);
  }
  .caret { color: var(--fg-2); font-size: 12px; }
  .pop {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    min-width: 220px;
    max-height: 320px;
    overflow: auto;
    padding: 6px;
    z-index: 100;
    box-shadow: var(--shadow-2);
  }
  .list { list-style: none; margin: 0; padding: 0; }
  .item-row {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .item {
    flex: 1;
    text-align: left;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    padding: 8px 12px;
    border-radius: var(--r-2);
    color: var(--fg-1);
    transition: background var(--t-fast);
  }
  .item:hover { background: var(--glass-2); }
  .name { font-size: var(--fs-sm); }
  .counts { color: var(--fg-3); font-size: var(--fs-xs); }
  .del {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    color: var(--fg-3);
    transition: background var(--t-fast), color var(--t-fast);
  }
  .del:hover { background: var(--glass-2); color: var(--negative); }
  .empty {
    color: var(--fg-3);
    font-size: var(--fs-sm);
    padding: 14px 12px;
    text-align: center;
  }
</style>
