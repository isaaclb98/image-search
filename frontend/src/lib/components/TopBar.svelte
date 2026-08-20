<script lang="ts">
  /**
   * TopBar with tabs. Sticky on top, glass surface, single logo
   * + a horizontal row of tabs. No floating pill — sits as a
   * real document-flow strip with whitespace separating it from
   * content.
   */
  import { page } from '$app/stores';

  type Tab = { href: string; label: string };
  const tabs: Tab[] = [
    { href: '/', label: 'Home' },
    { href: '/search', label: 'Search' },
    { href: '/random', label: 'Random' },
    { href: '/for-you', label: 'For You' },
    { href: '/albums', label: 'Albums' }
  ];

  let currentPath = $derived($page.url.pathname);
  function isActive(href: string, path: string): boolean {
    if (href === '/') return path === '/';
    return path === href || path.startsWith(href + '/');
  }
</script>

<header class="topbar">
  <div class="bar">
    <a class="brand" href="/" aria-label="Home">
      <span class="brand-mark" aria-hidden="true"></span>
      <span class="brand-text">image-search</span>
    </a>
    <nav class="tabs" aria-label="Main">
      {#each tabs as t (t.href)}
        <a
          class="tab"
          class:active={isActive(t.href, currentPath)}
          href={t.href}
          aria-current={isActive(t.href, currentPath) ? 'page' : undefined}
        >{t.label}</a>
      {/each}
    </nav>
  </div>
</header>

<style>
  .topbar {
    position: sticky;
    top: 0;
    z-index: 50;
    background-color: rgba(14,15,20,0.42);
    backdrop-filter: blur(28px) saturate(180%);
    -webkit-backdrop-filter: blur(28px) saturate(180%);
    border-bottom: 1px solid var(--glass-edge);
    overflow: hidden;
    box-shadow: 0 4px 24px rgba(0,0,0,0.3);
  }
  .topbar::after {
    content: '';
    position: absolute;
    inset: 0;
    background: var(--glass-tint, none) no-repeat center / cover;
    filter: blur(32px) saturate(1.5);
    opacity: 0.5;
    pointer-events: none;
    z-index: -1;
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
  .brand-mark {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background:
      radial-gradient(circle at 30% 30%, var(--accent) 0%, transparent 60%),
      radial-gradient(circle at 70% 70%, var(--negative) 0%, transparent 60%),
      var(--glass-2);
    border: 1px solid var(--glass-edge-strong);
  }
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
