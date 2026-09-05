<script lang="ts">
  /**
   * SearchComposer — the search input controls. Pure UI: parent
   * owns positives / negatives state. Component just renders and
   * signals via callbacks.
   *
   * Layout (vertical stack):
   *   [ PromptChips              ]
   *   [ CollectionsChips (opt)   ]
   *
   * The Search button + saved-searches menu used to live here
   * too, but they were pulled out to the page level (rendered
   * as siblings AFTER the AdditionalFilters panel) so the
   * visual order matches what the user asked for: search inputs
   * → diversity/filename options → action buttons.
   */
  import PromptChips from './PromptChips.svelte';
  import CollectionsChips from './CollectionsChips.svelte';

  type Props = {
    // state
    positives: string[];
    negatives: string[];
    input: string;
    mode: 'pos' | 'neg';
    /** Selected collection (source) names. Empty = whole library. */
    collections: string[];
    // events
    onInput: (v: string) => void;
    onMode: (m: 'pos' | 'neg') => void;
    onAdd: (text: string, mode: 'pos' | 'neg') => void;
    onRemovePositive: (i: number) => void;
    onRemoveNegative: (i: number) => void;
    onToggleCollection?: (name: string) => void;
  };
  let {
    positives,
    negatives,
    input,
    mode,
    collections = [],
    onInput,
    onMode,
    onAdd,
    onRemovePositive,
    onRemoveNegative,
    onToggleCollection,
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
</div>

<style>
  .composer {
    display: flex;
    flex-direction: column;
    gap: var(--s-3);
  }
</style>
