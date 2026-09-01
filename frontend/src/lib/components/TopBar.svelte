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
      <Icon name="logo" size={22} />
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
  /* Heavy frosted glass header. Sits on top of the full-viewport
     backdrop (see +layout.svelte) — only 12% dark overlay + heavy
     backdrop-filter creates the frosted look with the colour tint
     bleeding through from the current photo. */
  .topbar {
    /* Static (scrolls away with the page) — the user explicitly
       asked for the top tab bar to NOT follow the screen. The
       floating scroll-to-top button restores navigation reach
       after a long scroll. */
    position: static;
    background-color: rgba(14,15,20,0.12);
    backdrop-filter: blur(24px) saturate(180%);
    -webkit-backdrop-filter: blur(24px) saturate(180%);
    border-bottom: 1px solid var(--glass-edge);
    overflow: hidden;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
  }
  .bar {
    height: var(--topbar-h);
    display: flex;
    align-items: center;
    gap: var(--s-4);
    padding: 0 24px;
    max-width: 1600px;
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
