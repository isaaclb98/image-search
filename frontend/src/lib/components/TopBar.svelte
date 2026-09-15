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
    { href: '/for-you', label: 'For You' },
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
  /* Solid tinted header. Round-1 polished from a tokenless 0.12
     alpha to --topbar-alpha (0.32 at the time) so the bar read
     as a real surface against bg-0. Round-37 dropped the page
     backdrop AND the topbar's backdrop-filter AND raised
     --topbar-alpha to 1 — with no backdrop tint and no frost,
     any alpha < 1 lets the page content show through, which
     re-introduced per-route colour variation. Fully opaque keeps
     the topbar the same on every page. See the .topbar block
     below for the full reasoning. */
  .topbar {
    /* Round-1 polish: sticky so the brand + tabs stay reachable
       while scrolling. The scroll-to-top button stays — its job
       is to scroll the page back to the top, not restore topbar
       visibility (different concern). */
    position: sticky;
    top: 0;
    z-index: 100;
    /* Solid tinted surface. Previously used backdrop-filter:
       var(--glass-heavy) (32px blur + 180% saturate) — that
       sampled whatever was scrolling underneath, so the topbar
       tinted differently on /random (photo tiles) vs /photo/{id}
       (blurhash frame) vs /albums (text on dark). Round-37 dropped
       the per-page backdrop tint AND removed the topbar's
       backdrop-filter so it stays the same colour on every page.
       The glass effect only works when there's something
       interesting to frost against; on a uniform-dark page it's
       a no-op, on a colourful page it amplifies random page
       state — neither is a stable design. Per-panel colour
       lives in .glass-tint::before (each tile's surrounding
       glass picks up a soft-light sample from the photo inside).
       The topbar is chrome, not content. */
    background-color: rgba(14,15,20, var(--topbar-alpha));
    border-bottom: 1px solid var(--glass-edge);
    /* Box-shadow gives the topbar the impression of a surface
       floating just above the page — without the backdrop-filter
       frost, this is what sells the "layer above content" read. */
    box-shadow: var(--shadow-glass);
  }
  .bar {
    height: var(--topbar-h);
    display: flex;
    align-items: center;
    gap: var(--s-4);
    padding: 0 var(--shell-pad-x);
    /* Match the page header card width via --grid-width so the
       brand mark and tabs stay vertically aligned with the random
       / for-you / albums header below. The hardcoded 1600px that
       used to live here drifted away from --grid-width at every
       zoom level (bar widened to 2400px at 150%, header only to
       2190px), pulling the nav tabs noticeably rightward of the
       page chrome they were supposed to align with. */
    width: var(--grid-width, 100%);
    max-width: 100%;
    margin: 0 auto;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: var(--s-1);
    color: var(--fg-1);
    font-weight: 600;
    letter-spacing: 0.02em;
  }
  .brand:hover { color: var(--fg-1); }
  /* The brand mark is an SVG Icon — no CSS needed here. The
   * Icon's stroke uses currentColor so the surrounding .brand
   * color drives it; alignment with the text comes from the
   * Icon's own viewBox + the .brand flex layout. */
  .tabs {
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
  .tab.active {
    background: var(--glass-2);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge-strong);
  }
  @media (max-width: 640px) {
    .bar { padding: 0 12px; gap: var(--s-2); }
    .brand-text { display: none; }
    .tab { padding: 0 10px; }
  }
</style>
