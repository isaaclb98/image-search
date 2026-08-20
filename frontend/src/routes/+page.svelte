<script lang="ts">
  /**
   * Home page — per spec:
   *   - Search page controls to begin a search
   *   - "For you" row of 20 randomly chosen from the top 800
   *     recommendation candidates — same engine as /for-you.
   */
  import SearchComposer from '$lib/components/SearchComposer.svelte';
  import ForYouRow from '$lib/components/ForYouRow.svelte';
  import { goto } from '$app/navigation';

  // Hoisted state — SearchComposer is a pure UI child.
  let positives = $state<string[]>([]);
  let negatives = $state<string[]>([]);
  let input = $state('');
  let mode = $state<'pos' | 'neg'>('pos');
  let filename = $state('');
  let diversityMode = $state('off');
  let diversityStrength = $state(0);
  let filtersOpen = $state(false);

  function addPrompt(text: string, kind: 'pos' | 'neg') {
    if (kind === 'pos') {
      if (!positives.includes(text)) positives = [...positives, text];
    } else {
      if (!negatives.includes(text)) negatives = [...negatives, text];
    }
  }
  function removePositive(i: number) {
    positives = positives.filter((_, idx) => idx !== i);
  }
  function removeNegative(i: number) {
    negatives = negatives.filter((_, idx) => idx !== i);
  }

  function runSearch() {
    const qs = new URLSearchParams();
    positives.forEach((x) => qs.append('positives', x));
    negatives.forEach((x) => qs.append('negatives', x));
    if (filename) qs.set('filename', filename);
    if (diversityMode && diversityMode !== 'off')
      qs.set('diversity_mode', diversityMode);
    if (diversityStrength > 0)
      qs.set('diversity_strength', String(diversityStrength));
    goto(`/search?${qs.toString()}`, { keepFocus: true });
  }
</script>

<svelte:head>
  <title>image-search</title>
</svelte:head>

<section class="hero">
  <h1>Find photos by what they look like.</h1>
  <p class="sub">
    Type what you remember — colours, moods, subjects — and pick from the
    results. Save the searches you love, pin your favourites, discover
    what's nearby.
  </p>
  <SearchComposer
    {positives}
    {negatives}
    {input}
    {mode}
    {filename}
    {diversityMode}
    {diversityStrength}
    {filtersOpen}
    onInput={(v) => (input = v)}
    onMode={(m) => (mode = m)}
    onAdd={addPrompt}
    onRemovePositive={removePositive}
    onRemoveNegative={removeNegative}
    onFilename={(v) => (filename = v)}
    onDiversityMode={(v) => (diversityMode = v)}
    onDiversityStrength={(v) => (diversityStrength = v)}
    onToggleFilters={() => (filtersOpen = !filtersOpen)}
    onSearch={runSearch}
    onPickSaved={(s) => {
      positives = [...s.positives];
      negatives = [...s.negatives];
      runSearch();
    }}
  />
</section>

<ForYouRow />

<style>
  .hero {
    max-width: 980px;
    margin: 0 auto;
    padding: 32px 16px 12px;
    text-align: center;
  }
  .hero h1 {
    font-size: var(--fs-3xl);
    font-weight: 600;
    margin: 0 0 12px;
    letter-spacing: -0.01em;
    line-height: var(--lh-tight);
  }
  .sub {
    color: var(--fg-2);
    margin: 0 auto 28px;
    max-width: 600px;
    font-size: var(--fs-md);
  }
</style>
