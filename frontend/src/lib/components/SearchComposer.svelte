<script lang="ts">
  /**
   * SearchComposer — the search input controls. Pure UI: parent
   * owns positives / negatives state. Component just renders and
   * signals via callbacks.
   *
   * Layout (vertical stack):
   *   [ PromptChips              ]
   *
   * The Search button + saved-searches menu used to live here
   * too, but they were pulled out to the page level (rendered
   * as siblings AFTER the AdditionalFilters panel) so the
   * visual order matches what the user asked for: search inputs
   * → diversity/filename options → action buttons.
   *
   * The collections chip filter used to live here too; it now
   * lives inside the AdditionalFilters panel (rendered when
   * `onToggleCollection` is passed). Keeps the "library scoping"
   * choice with the other search-scope controls.
   */
  import PromptChips from './PromptChips.svelte';

  type Props = {
    // state
    positives: string[];
    negatives: string[];
    input: string;
    mode: 'pos' | 'neg';
    // events
    onInput: (v: string) => void;
    onMode: (m: 'pos' | 'neg') => void;
    onAdd: (text: string, mode: 'pos' | 'neg') => void;
    onRemovePositive: (i: number) => void;
    onRemoveNegative: (i: number) => void;
  };
  let {
    positives,
    negatives,
    input,
    mode,
    onInput,
    onMode,
    onAdd,
    onRemovePositive,
    onRemoveNegative,
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
</div>

<style>
  .composer {
    display: flex;
    flex-direction: column;
    gap: var(--s-3);
  }
</style>
