<script lang="ts">
  /**
   * Settings page — hosts the Index button (the user-facing surface
   * of the in-app indexer). Two modes: incremental (default,
   * idempotent) and rebuild-from-scratch (wipes vectors + side store).
   *
   * While a job is running the page polls /api/admin/index/status
   * every second and shows live progress. The Cancel button sends
   * SIGTERM to the indexer subprocess; the page then returns to
   * idle.
   */
  import { onDestroy, onMount } from 'svelte';
  import { apiGet, apiPost, ApiError } from '$lib/api/client';
  import type { components } from '$lib/api/types.gen';
  import Button from '$lib/components/Button.svelte';
  import {
    preferences,
    SLIDESHOW_PRESETS,
    setSlideshowInterval
  } from '$lib/stores/preferences';

  type IndexerStatusResponse = components['schemas']['IndexerStatusResponse'];
  type IndexerLogResponse = components['schemas']['IndexerLogResponse'];
  type State = IndexerStatusResponse['state'];

  let status = $state<IndexerStatusResponse | null>(null);
  let logText = $state<string>('');
  let loading = $state(true);
  let busy = $state(false);
  let popoverOpen = $state(false);
  let errorMessage = $state<string | null>(null);

  let pollTimer: ReturnType<typeof setInterval> | null = null;

  async function refresh() {
    try {
      status = await apiGet<IndexerStatusResponse>('/api/admin/index/status');
      errorMessage = null;
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  async function refreshLog() {
    try {
      const resp = await apiGet<IndexerLogResponse>('/api/admin/index/log');
      logText = resp.lines.join('\n');
    } catch {
      // ignore log fetch errors — status is the source of truth
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      await refresh();
      await refreshLog();
      if (status && status.state !== 'running') {
        stopPolling();
      }
    }, 1000);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  onMount(async () => {
    await refresh();
    if (status?.state === 'running') startPolling();
  });

  onDestroy(stopPolling);

  async function startIndex(mode: 'incremental' | 'rebuild') {
    busy = true;
    errorMessage = null;
    popoverOpen = false;
    try {
      status = await apiPost<IndexerStatusResponse>('/api/admin/index', { mode });
      startPolling();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        errorMessage = 'An indexer job is already running.';
      } else {
        errorMessage = err instanceof Error ? err.message : String(err);
      }
    } finally {
      busy = false;
    }
  }

  async function cancelIndex() {
    busy = true;
    errorMessage = null;
    try {
      status = await apiPost<IndexerStatusResponse>('/api/admin/index/cancel');
      await refresh();
      await refreshLog();
    } catch (err) {
      errorMessage = err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }

  function isRunning(s: State | undefined): boolean {
    return s === 'running';
  }
</script>

<svelte:head>
  <title>Settings · Image Search</title>
</svelte:head>

<div class="settings-page">
  <h1 class="page-title">Settings</h1>

  <section class="card">
    <h2 class="card-title">Index</h2>
    <p class="card-desc">
      Walk the photo library, embed every image, and write it to the
      search index. Embeddings run in the background as a child
      process; you can keep using the app while a job runs.
    </p>

    {#if loading}
      <div class="muted">Loading…</div>
    {:else if status}
      <div class="status-row" data-state={status.state}>
        <span class="status-dot" aria-hidden="true"></span>
        <span class="status-label">
          {#if status.state === 'idle'}
            Idle{#if status.last_run_at} · last run {formatRelative(status.last_run_at)}{/if}
          {:else if status.state === 'running'}
            {#if status.phase === 'warming_up'}
              Warming up model ({status.progress.indexed} indexed so far)
            {:else if status.phase === 'scanning'}
              Scanning…
            {:else}
              Running · {status.progress.indexed} indexed, {status.progress.reembedded} re-embedded
            {/if}
          {:else}
            Failed{status.last_error ? ` — ${status.last_error}` : ''}
          {/if}
        </span>
      </div>

      {#if errorMessage}
        <div class="error">{errorMessage}</div>
      {/if}

      <div class="actions">
        {#if isRunning(status.state)}
          <Button onclick={cancelIndex} disabled={busy}>Cancel</Button>
        {:else}
          <div class="popover-wrap">
            <!-- Plain <button> (not the Button primitive) so we can
                 pass aria-expanded / aria-haspopup for the menu trigger. -->
            <button
              class="btn btn-secondary btn-md"
              type="button"
              disabled={busy}
              aria-expanded={popoverOpen}
              aria-haspopup="menu"
              onclick={() => (popoverOpen = !popoverOpen)}
            >
              Index
            </button>
            {#if popoverOpen}
              <div class="popover" role="menu">
                <button
                  class="menu-item"
                  role="menuitem"
                  onclick={() => startIndex('incremental')}
                >
                  <span class="menu-item-title">Index new &amp; changed files</span>
                  <span class="menu-item-desc">
                    Safe to spam. Embeds only files that are new or
                    have changed since the last index run.
                  </span>
                </button>
                <button
                  class="menu-item"
                  role="menuitem"
                  onclick={() => startIndex('rebuild')}
                >
                  <span class="menu-item-title">Rebuild from scratch</span>
                  <span class="menu-item-desc">
                    Wipes the index and your favourites, albums, and
                    saved searches, then re-embeds every photo.
                  </span>
                </button>
              </div>
            {/if}
          </div>
        {/if}
      </div>

      {#if logText}
        <details class="log-section">
          <summary>Log</summary>
          <pre class="log">{logText}</pre>
        </details>
      {/if}
    {/if}
  </section>

  <section class="card">
    <h2 class="card-title">Slideshow</h2>
    <p class="card-desc">
      How long each photo stays up during Lightbox auto-advance
      (press Play inside any lightbox with multiple images to start
      a slideshow). The setting applies the next time you press
      Play, even mid-playback.
    </p>
    <div
      class="preset-row"
      role="radiogroup"
      aria-label="Slideshow photo duration"
    >
      {#each SLIDESHOW_PRESETS as preset (preset.ms)}
        <button
          type="button"
          class="preset"
          role="radio"
          aria-checked={$preferences.slideshowIntervalMs === preset.ms}
          data-active={$preferences.slideshowIntervalMs === preset.ms}
          onclick={() => setSlideshowInterval(preset.ms)}
        >
          {preset.label}
        </button>
      {/each}
    </div>
  </section>
</div>

<script module lang="ts">
  function formatRelative(iso: string): string {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const sec = Math.round((now - then) / 1000);
    if (sec < 60) return `${sec}s ago`;
    const min = Math.round(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr}h ago`;
    return `${Math.round(hr / 24)}d ago`;
  }
</script>

<style>
  .settings-page {
    max-width: 720px;
    margin: 0 auto;
    padding: var(--s-5) var(--s-3);
    display: flex;
    flex-direction: column;
    gap: var(--s-4);
  }

  .page-title {
    font-size: 28px;
    font-weight: 400;
    margin: 0 0 var(--s-2) 0;
    color: var(--fg-1);
  }

  .card {
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    padding: var(--s-4);
    display: flex;
    flex-direction: column;
    gap: var(--s-3);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
  }

  .card-title {
    font-size: 18px;
    font-weight: 500;
    margin: 0;
    color: var(--fg-1);
  }

  .card-desc {
    margin: 0;
    color: var(--fg-2);
    font-size: 14px;
    line-height: 1.5;
  }

  .status-row {
    display: flex;
    align-items: center;
    gap: var(--s-2);
    padding: var(--s-2) var(--s-3);
    background: var(--glass-2);
    border-radius: var(--r-2);
    font-size: 14px;
    color: var(--fg-1);
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--fg-3);
    flex-shrink: 0;
  }

  .status-row[data-state="running"] .status-dot {
    background: var(--accent);
    animation: pulse 1.5s ease-in-out infinite;
  }
  .status-row[data-state="failed"] .status-dot {
    background: var(--danger);
  }
  .status-row[data-state="idle"] .status-dot {
    background: var(--success);
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .actions {
    display: flex;
    gap: var(--s-2);
    position: relative;
  }

  .popover-wrap {
    position: relative;
  }

  .popover {
    position: absolute;
    top: calc(100% + var(--s-1));
    left: 0;
    background: var(--bg-1);
    border: 1px solid var(--glass-edge-strong);
    border-radius: var(--r-2);
    padding: var(--s-1);
    min-width: 280px;
    z-index: 10;
    backdrop-filter: blur(20px);
    -webkit-backdrop-filter: blur(20px);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .menu-item {
    background: none;
    border: none;
    color: var(--fg-1);
    text-align: left;
    padding: var(--s-2);
    border-radius: var(--r-1);
    cursor: pointer;
    display: flex;
    flex-direction: column;
    gap: var(--s-0);
    font-family: inherit;
  }

  .menu-item:hover {
    background: var(--glass-2);
  }

  .menu-item-title {
    font-size: 14px;
    font-weight: 500;
  }

  .menu-item-desc {
    font-size: 12px;
    color: var(--fg-3);
  }

  .log-section {
    margin-top: var(--s-2);
  }

  .log-section summary {
    cursor: pointer;
    color: var(--fg-2);
    font-size: 13px;
    user-select: none;
  }

  .log {
    margin: var(--s-2) 0 0 0;
    padding: var(--s-2);
    background: var(--bg-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-2);
    color: var(--fg-2);
    font-size: 12px;
    max-height: 240px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
  }

  .error {
    color: var(--danger);
    font-size: 13px;
    padding: var(--s-2);
    background: rgba(255, 93, 108, 0.1);
    border-radius: var(--r-2);
  }

  .muted {
    color: var(--fg-3);
    font-size: 14px;
  }

  /* Slideshow preset row. Five pill buttons in a single horizontal
     line; the active preset swaps to a darker glass-2 fill so
     the current duration is unambiguous at a glance. Uses
     aria-checked + data-active so the same visual state is
     reachable from both the accessibility tree (screen readers
     announce the selected radio) and pure CSS (no JS toggle
     bookkeeping). */
  .preset-row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2);
  }
  .preset {
    appearance: none;
    background: var(--glass-1);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-pill);
    padding: 0 16px;
    height: 32px;
    font: inherit;
    font-size: 0.9rem;
    cursor: pointer;
    transition:
      background var(--t-fast),
      border-color var(--t-fast),
      color var(--t-fast);
  }
  .preset:hover {
    background: var(--glass-2);
    color: var(--fg-0);
  }
  .preset:focus-visible {
    outline: 2px solid var(--accent, #6ab7ff);
    outline-offset: 2px;
  }
  .preset[data-active='true'] {
    background: var(--glass-3);
    border-color: var(--accent, #6ab7ff);
    color: var(--fg-0);
  }
</style>
