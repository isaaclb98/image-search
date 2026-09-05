<script lang="ts">
  /**
   * AdditionalFilters — the collapsible panel below the search
   * controls. Has a header with title + chevron, body has the
   * filename input, diversity controls, and (on the home page)
   * the collections chip filter.
   *
   * Diversity API contract (see search/diversity.py):
   *   diversity_mode  ∈ {off, low, balanced, high}
   *   diversity_depth ∈ {auto, 500, 1000, 2000, 5000}
   *   (diversity_strength was a 0–1 slider; removed in round-4
   *    per king — the mode dropdown is enough.)
   *
   * Collections: optional. Pass `collections` + `onToggleCollection`
   * to surface the chip-row filter inside this panel. Used on the
   * home page only — other pages don't need library scoping.
   */
  import CollectionsChips from './CollectionsChips.svelte';

  type Props = {
    open: boolean;
    filename: string;
    diversityMode: 'off' | 'low' | 'balanced' | 'high' | string;
    diversityDepth?: 'auto' | '500' | '1000' | '2000' | '5000' | string;
    onToggle: () => void;
    onFilename: (v: string) => void;
    onDiversityMode: (v: string) => void;
    onDiversityDepth?: (v: string) => void;
    collections?: string[];
    onToggleCollection?: (name: string) => void;
  };
  let {
    open,
    filename,
    diversityMode,
    diversityDepth = 'auto',
    onToggle,
    onFilename,
    onDiversityMode,
    onDiversityDepth,
    collections = [],
    onToggleCollection
  }: Props = $props();
</script>

<section class="filters glass" class:open>
  <button
    class="head"
    type="button"
    onclick={onToggle}
    aria-expanded={open}
  >
    <span>Additional options</span>
    <span class="chev" aria-hidden="true">{open ? '▴' : '▾'}</span>
  </button>
  {#if open}
    <div class="body">
      <label class="field">
        <span class="lab">Filename contains</span>
        <input
          type="text"
          placeholder="e.g. IMG_2024"
          value={filename}
          oninput={(e) => onFilename((e.target as HTMLInputElement).value)}
          autocomplete="off"
          spellcheck="false"
        />
      </label>
      <div class="field">
        <span class="lab">Diversity</span>
        <div class="row">
          <select
            value={diversityMode}
            onchange={(e) => onDiversityMode((e.target as HTMLSelectElement).value)}
            aria-label="Diversity mode"
          >
            <option value="off">Off</option>
            <option value="low">Low</option>
            <option value="balanced">Balanced</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>
      {#if onDiversityDepth}
        <div class="field">
          <span class="lab">Diversity depth</span>
          <div class="row">
            <select
              value={diversityDepth}
              onchange={(e) => onDiversityDepth((e.target as HTMLSelectElement).value)}
              aria-label="Diversity depth"
              title="How many top results to re-rank across for diversity"
            >
              <option value="auto">Auto</option>
              <option value="500">500 photos</option>
              <option value="1000">1,000 photos</option>
              <option value="2000">2,000 photos</option>
              <option value="5000">5,000 photos</option>
            </select>
          </div>
        </div>
      {/if}
      {#if onToggleCollection}
        <div class="collections-row">
          <span class="lab">Limit to library</span>
          <CollectionsChips
            selected={collections}
            onToggle={onToggleCollection}
          />
        </div>
      {/if}
    </div>
  {/if}
</section>

<style>
  .filters {
    overflow: hidden;
    padding: 0;
    /* Width matches the SearchComposer card width above us
       (which fills .hero minus its 16px horizontal padding).
       The hero itself is --grid-width on the home page, matching
       the .head width on the other grid pages, but inside the
       hero the composer card has internal padding so its chrome
       is slightly narrower — we match that chrome edge so the
       two cards visually line up. */
    width: 100%;
    margin: 0 auto;
    /* The hero is text-align: center; override so this panel's
       labels (Filename, Diversity, Diversity depth, Limit to
       library) read as a form, not as centered prose. */
    text-align: left;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    padding: 12px 16px;
    color: var(--fg-1);
    font-weight: 500;
  }
  .chev {
    color: var(--fg-2);
    font-size: 14px;
  }
  .body {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 14px;
    padding: 4px 16px 16px;
    border-top: 1px solid var(--glass-edge);
  }
  /* Inline label + control pattern, matching /for-you. The label
     sits on the left at a fixed width so "Diversity" and
     "Diversity depth" align at the same x, and the input/select
     fills the remaining row width via flex: 1. Without this the
     fields stack label-on-top-of-control which forces the control
     to fill the full column width (235px) and look oversized for
     short values like "Balanced" or "Auto". */
  .field {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .field .lab {
    color: var(--fg-2);
    font-size: var(--fs-sm);
    /* Wider than /for-you (96px) because "Filename contains" and
       "Limit to library" don't fit in 96px without wrapping. The
       +24px keeps all labels on one line while still aligning
       the dropdowns at a consistent x. */
    width: 120px;
    flex-shrink: 0;
  }
  .field input[type='text'] {
    background: rgba(14,15,20,0.45);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-pill);
    padding: 0 12px;
    height: 36px;
    color: var(--fg-1);
    transition: border-color var(--t-fast);
    /* Fill the grid cell so every column reads the same width.
       Without this, native <select> sizes to its content (often
       narrower than the field's allocated column), making the
       dropdown look pinched next to a wider text input. */
    width: 100%;
    box-sizing: border-box;
  }
  .field input[type='text']:focus {
    border-color: var(--accent);
  }
  /* The collections chip-row spans the full grid width — it's a
     row of toggleable chips, not a single labeled input. */
  .collections-row {
    grid-column: 1 / -1;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .collections-row .lab {
    color: var(--fg-2);
    font-size: var(--fs-sm);
  }
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  select {
    height: 36px;
    border-radius: var(--r-pill);
    background: rgba(14,15,20,0.45);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge);
    padding: 0 12px;
    /* Fill the grid cell so dropdowns match the text input width
       in their row. Without this, the <select> shrinks to fit
       its current value (e.g. "Balanced") and looks pinched
       next to the wider filename input. */
    width: 100%;
    box-sizing: border-box;
  }
  input[type='range'] {
    flex: 1;
    accent-color: var(--accent);
  }
</style>
