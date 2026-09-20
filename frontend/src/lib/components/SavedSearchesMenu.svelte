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
  import { dialog } from './Dialog.svelte';
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
      return;
    }
    const name = await dialog.prompt({
      title: 'Save search',
      label: `Name this search (${positives.length}+/${negatives.length}-)`,
      confirmLabel: 'Save',
      defaultValue: ''
    });
    if (!name) return;
    try {
      await createSavedSearch({
        name: name.trim(),
        positives,
        negatives
      });
      await refresh();
    } catch (e) {
    }
  }

  async function pick(s: SavedSearch) {
    onPick(s);
    open = false;
  }

  async function remove(e: MouseEvent, id: number) {
    e.stopPropagation();
    const ok = await dialog.confirm({
      title: 'Delete saved search',
      body: 'Delete this saved search?',
      confirmLabel: 'Delete',
      kind: 'danger'
    });
    if (!ok) return;
    try {
      await deleteSavedSearch(id);
      items = items.filter((s) => s.id !== id);
    } catch {
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
    gap: var(--s-1);
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
    gap: var(--s-1);
    font-size: var(--fs-sm);
    transition: background var(--t-fast);
  }
  .trigger:hover { background: var(--glass-2); border-color: var(--accent-soft); }
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
  .caret { color: var(--fg-2); font-size: var(--fs-xs); }
  .pop {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    min-width: 220px;
    max-height: 320px;
    overflow: auto;
    padding: var(--s-1);
    z-index: 100;
    box-shadow: var(--shadow-2);
  }
  .list { list-style: none; margin: 0; padding: 0; }
  .item-row {
    display: flex;
    align-items: center;
    gap: var(--s-0);
  }
  .item {
    flex: 1;
    text-align: left;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--s-3);
    padding: var(--s-1) var(--s-3);
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
    padding: var(--s-3) var(--s-3);
    text-align: center;
  }
</style>
