<script lang="ts">
  /**
   * PageHeader — the standard title bar used on the result-grid
   * pages (/random, /for-you, /similar, /albums, /albums/likes,
   * /albums/dislikes, /albums/[id]).
   *
   * Single source of truth for the page-header pattern. Variants:
   *   - title only          — random, for-you, similar
   *   - title + subtitle    — random, for-you, similar
   *   - title + actions     — albums (+ New album button)
   *   - title + meta        — albums/likes, albums/dislikes ("N photos")
   *   - title + all of the above — albums/[id] (zip download)
   *
   * Title can be a plain string OR a snippet (for cases that need
   * an inline icon, like the heart on /albums/likes).
   *
   * Layout: title block on the left (h1 + optional subtitle/meta),
   * actions slot on the right. When actions is empty, the title
   * block fills the row. The header is wrapped in `.glass` for
   * the standard tinted-panel look matching the rest of the app.
   */
  import type { Snippet } from 'svelte';

  type Props = {
    /** Page title — string for the common case, snippet for icon+text. */
    title: string | Snippet;
    /** Optional one-line subtitle under the title. */
    subtitle?: string;
    /** Optional small meta line under the subtitle (e.g. "182 photos"). */
    meta?: string;
    /** Optional right-side action area (buttons, links). */
    actions?: Snippet;
  };

  let { title, subtitle, meta, actions }: Props = $props();
</script>

<section class="head glass">
  <div class="text">
    <h1>
      {#if typeof title === 'string'}{title}{:else}{@render title()}{/if}
    </h1>
    {#if subtitle}<p>{subtitle}</p>{/if}
    {#if meta}<p class="meta">{meta}</p>{/if}
  </div>
  {#if actions}
    <div class="actions">
      {@render actions()}
    </div>
  {/if}
</section>

<style>
  .head {
    /* Standard page-header geometry. Aligned to the photo grid
       below via --grid-width so the chrome edges line up. */
    margin: 16px auto 24px;
    padding: 22px 26px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    width: var(--grid-width, 100%);
    max-width: 100%;
  }
  .text {
    flex: 1;
    min-width: 0;
  }
  h1 {
    margin: 0;
    font-size: var(--fs-2xl);
    font-weight: 600;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--fg-1);
  }
  p {
    margin: 4px 0 0;
    color: var(--fg-2);
  }
  .meta {
    color: var(--fg-3);
    font-size: var(--fs-sm);
  }
  .actions {
    flex-shrink: 0;
  }
</style>