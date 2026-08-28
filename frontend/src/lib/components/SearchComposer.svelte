<script lang="ts">
  /**
   * SearchComposer — the reusable search controls. Pure UI:
   * parent owns positives / negatives / filename / diversity
   * state. Component just renders and signals via callbacks.
   *
   * Layout:
   *   [ PromptChips        ]
   *   [ AdditionalFilters  ]
   *   [ Saved | Save | Search ]
   */
  import PromptChips from './PromptChips.svelte';
  import AdditionalFilters from './AdditionalFilters.svelte';
  import SavedSearchesMenu from './SavedSearchesMenu.svelte';
  import CollectionsChips from './CollectionsChips.svelte';
  import type { SavedSearch } from '$lib/api/endpoints';

  export type SearchParams = {
    positives: string[];
    negatives: string[];
    filename: string;
    diversityMode: string;
    diversityDepth?: string;
  };

  type Props = {
    // state
    positives: string[];
    negatives: string[];
    input: string;
    mode: 'pos' | 'neg';
    filename: string;
    diversityMode: string;
    diversityDepth?: string;
    /** Selected collection (source) names. Empty = whole library. */
    collections: string[];
    filtersOpen: boolean;
    loading?: boolean;
    // events
    onInput: (v: string) => void;
    onMode: (m: 'pos' | 'neg') => void;
    onAdd: (text: string, mode: 'pos' | 'neg') => void;
    onRemovePositive: (i: number) => void;
    onRemoveNegative: (i: number) => void;
    onFilename: (v: string) => void;
    onDiversityMode: (v: string) => void;
    onDiversityDepth?: (v: string) => void;
    onToggleCollection?: (name: string) => void;
    onToggleFilters: () => void;
    onSearch: () => void;
    onPickSaved: (s: SavedSearch) => void;
    // presentation
    showFilters?: boolean;
    showSavedSearches?: boolean;
    searchButtonLabel?: string;
  };
  let {
    positives,
    negatives,
    input,
    mode,
    filename,
    diversityMode,
    diversityDepth = 'auto',
    collections = [],
    filtersOpen,
    loading = false,
    onInput,
    onMode,
    onAdd,
    onRemovePositive,
    onRemoveNegative,
    onFilename,
    onDiversityMode,
    onDiversityDepth,
    onToggleCollection,
    onToggleFilters,
    onSearch,
    onPickSaved,
    showFilters = true,
    showSavedSearches = true,
    searchButtonLabel = 'Search'
  }: Props = $props();
</script>

<div class="composer">
  <PromptChips
    {positives}
    {negatives}
    {input}
    {mode}
    {onInput}
    {onAdd}
    {onRemovePositive}
    {onRemoveNegative}
    {onMode}
  />
  {#if onToggleCollection}
    <CollectionsChips
      selected={collections}
      onToggle={onToggleCollection}
    />
  {/if}
  {#if showFilters}
    <AdditionalFilters
      open={filtersOpen}
      {filename}
      {diversityMode}
      {diversityDepth}
      onToggle={onToggleFilters}
      {onFilename}
      {onDiversityMode}
      {onDiversityDepth}
    />
  {/if}
  <div class="actions">
    {#if showSavedSearches}
      <SavedSearchesMenu
        {positives}
        {negatives}
        onPick={onPickSaved}
      />
    {/if}
    <button
      type="button"
      class="primary"
      onclick={onSearch}
      disabled={(!positives.length && !negatives.length && !filename.trim() && !collections.length) || loading}
      title="Run search"
    >
      {searchButtonLabel}
    </button>
  </div>
</div>

<style>
  .composer {
    display: flex;
    flex-direction: column;
    gap: var(--s-3);
  }
  .actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
  }
  .primary {
    height: 44px;
    padding: 0 28px;
    border-radius: var(--r-pill);
    background: var(--accent);
    color: var(--fg-on-accent);
    font-weight: 600;
    font-size: var(--fs-md);
    transition: background var(--t-fast);
    box-shadow: 0 4px 18px rgba(108,198,255,0.30);
  }
  .primary:hover { background: var(--accent-2); }
  .primary:disabled {
    background: var(--glass-1);
    color: var(--fg-3);
    box-shadow: none;
    cursor: not-allowed;
  }
</style>
