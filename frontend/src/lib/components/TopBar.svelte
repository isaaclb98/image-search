<script lang="ts">
  /**
   * TopBar with tabs. Sticky on top, glass surface, single logo
   * + a horizontal row of tabs. No floating pill — sits as a
   * real document-flow strip with whitespace separating it from
   * content.
   *
   * Round‑29c: the Home tab uses `location.assign('/')` instead
   * of an anchor `href="/"` so clicking it from
   * `/?positives=…&diversity=…` actually navigates back to a
   * clean home (clears query string, resets composer state).
   *
   * A plain `<a href="/">` on the same pathname is a no-op for
   * the browser + SvelteKit — the URL doesn't change because
   * the path is already `/`, so the user stays on the search
   * results page. `goto('/')` from `$app/navigation` has the
   * same problem: it skips navigation when the URL is identical
   * to the current page.
   *
   * `location.assign('/')` always triggers a full page load,
   * which is exactly what we want for "reset to default home".
   */
  import { page } from '$app/stores';
  import Icon from './Icon.svelte';

  type Tab = { href: string; label: string; reset?: boolean };
  const tabs: Tab[] = [
    { href: '/', label: 'Home', reset: true },
    { href: '/random', label: 'Random' },
    { href: '/for-you', label: 'For you' },
    { href: '/albums', label: 'Albums' },
    { href: '/settings', label: 'Settings' }
  ];

  let currentPath = $derived($page.url.pathname);
  function isActive(href: string, path: string): boolean {
    if (href === '/') return path === '/';
    return path === href || path.startsWith(href + '/');
  }
  function onTabClick(t: Tab, e: MouseEvent) {
    // Modifier‑click (cmd / ctrl / shift) should still open in
    // a new tab / window — let the browser handle it.
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) {
      return;
    }
    // "Reset" tabs (Home) always do a full page load so the URL
    // and the composer state get cleared. Other tabs use plain
    // anchor navigation.
    if (t.reset) {
      e.preventDefault();
      location.assign(t.href);
    }
  }
</script>

<header class="topbar">
  <div class="bar">
    <a class="brand" href="/" aria-label="Home">
      <Icon name="sparkle" size={22} />
      <span class="brand-text">Image Search</span>
    </a>
    <nav class="tabs" aria-label="Main">
      {#each tabs as t (t.href)}
        <a
          class="tab"
          class:active={isActive(t.href, currentPath)}
          href={t.href}
          onclick={(e) => onTabClick(t, e)}
          aria-current={isActive(t.href, currentPath) ? 'page' : undefined}
        >{t.label}</a>
      {/each}
    </nav>
  </div>
</header>

<style>
  /* Solid tinted header. Round-38 switched from dark glass to
     light Immich-style. The topbar is a translucent white pill
     with hairline border — it sits on the mesh-gradient
     backdrop, lets the colour wash show through, and uses a
     border (not a shadow) to separate from content below. */
  .topbar {
    position: sticky;
    top: 0;
    z-index: 100;
    background-color: rgba(255,255,255,0.7);
    border-bottom: 1px solid var(--glass-edge);
    /* Box-shadow gives the topbar the impression of a surface
       floating just above the page — without the backdrop-filter
       frost, this is what sells the "layer above content" read. */
    box-shadow: var(--shadow-glass);
  }
  .bar {
    height: var(--topbar-h);
    display: grid;
    /* Two-column grid: brand on the left (auto), tabs take the
       remaining 1fr and center themselves within it. This
       balances the visual whitespace around the tabs — brand
       on left, ~213px gap, tabs centered, ~382px gap, empty
       right edge. The tabs sit at x ≈ 561-1015 in a 1216-wide
       bar, visually centered in the space to the right of the
       brand rather than in the full bar width. Previous
       (justify-content: center on a flex parent) put the tabs
       at x ≈ 493-946 — centered in the full bar but visually
       shifted right of center because the brand takes the
       left third. Isaac called the prior layout 'arbitrary';
       this version has clear left/right whitespace symmetry
       relative to the tabs. */
    grid-template-columns: auto 1fr;
    align-items: center;
    padding: 0 var(--shell-pad-x);
    width: var(--grid-width, 100%);
    max-width: 100%;
    margin: 0 auto;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: var(--s-1);
    color: var(--fg-1);
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .brand:hover { color: var(--fg-1); }
  /* The brand mark is an SVG Icon — no CSS needed here. The
   * Icon's stroke uses currentColor so the surrounding .brand
   * color drives it; alignment with the text comes from the
   * Icon's own viewBox + the .brand flex layout. */
  .tabs {
    /* Sits in the second grid column (1fr) and centers itself
       within that column — visually balanced in the space to
       the right of the brand. */
    grid-column: 2;
    justify-self: center;
    display: flex;
    gap: var(--s-1);
    align-items: center;
  }
  .tab {
    display: inline-flex;
    align-items: center;
    height: 34px;
    padding: 0 14px;
    border-radius: var(--r-pill);
    color: var(--fg-2);
    font-weight: 500;
    transition: background var(--t-fast) var(--ease-out),
                color var(--t-fast) var(--ease-out);
  }
  .tab:hover {
    background: var(--glass-1);
    color: var(--fg-1);
  }
  /* Active tab: soft blue wash + accent text. This is the
     Immich-style "you are here" affordance — not a solid blue
     pill, but a tinted backdrop with the accent colour text on
     top. Reads as "selected" without competing with the page
     content. */
  .tab.active {
    background: var(--accent-soft);
    color: var(--accent);
    border: 1px solid transparent;
  }
  @media (max-width: 640px) {
    .bar { padding: 0 12px; gap: var(--s-2); }
    .brand-text { display: none; }
    .tab { padding: 0 10px; }
  }
</style>
