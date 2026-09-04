<script module lang="ts">
  /**
   * Lightweight toast store + component. Toasts auto-dismiss
   * after `ms`, with manual close via the × button.
   *
   *     import { toast } from '$lib/components/Toaster.svelte';
   *     toast.show('Saved!', { kind: 'success' });
   */
  export type ToastKind = 'info' | 'success' | 'error' | 'warn';
  export type Toast = { id: number; text: string; kind: ToastKind; ms: number };
  let nextId = 1;
  let toasts = $state<Toast[]>([]);

  function show(text: string, opts: { kind?: ToastKind; ms?: number } = {}) {
    const t: Toast = {
      id: nextId++,
      text,
      kind: opts.kind ?? 'info',
      ms: opts.ms ?? 3200
    };
    toasts = [...toasts, t];
    setTimeout(() => dismiss(t.id), t.ms);
  }
  function dismiss(id: number) {
    toasts = toasts.filter((t) => t.id !== id);
  }
  export const toast = { show, dismiss };
</script>

<script lang="ts">
  // Render layer. The store is in the module script above so
  // consumers can import it without an instance.
</script>

<div class="toaster" role="region" aria-label="Notifications">
  {#each toasts as t (t.id)}
    <div class="toast {t.kind}" role="status">
      <span class="dot" aria-hidden="true"></span>
      <span class="text">{t.text}</span>
      <button
        class="close"
        aria-label="Dismiss"
        onclick={() => toast.dismiss(t.id)}
      >×</button>
    </div>
  {/each}
</div>

<style>
  .toaster {
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: var(--s-1);
    pointer-events: none;
  }
  .toast {
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: var(--s-2);
    background: var(--glass-2);
    border: 1px solid var(--glass-edge-strong);
    color: var(--fg-1);
    border-radius: var(--r-pill);
    padding: 10px 18px;
    min-width: 220px;
    max-width: 540px;
    box-shadow: var(--shadow-glass);
    backdrop-filter: var(--glass-heavy);
    -webkit-backdrop-filter: var(--glass-heavy);
    animation: pop var(--t-med) var(--ease-out);
  }
  @keyframes pop {
    from { opacity: 0; transform: translateY(8px) scale(0.98); }
    to   { opacity: 1; transform: translateY(0)    scale(1);    }
  }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--accent);
  }
  .success .dot { background: var(--success); }
  .warn .dot    { background: var(--warn); }
  .error .dot   { background: var(--danger); }
  .text { flex: 1; }
  .close {
    color: var(--fg-3);
    font-size: 18px;
    line-height: 1;
    padding: 0 4px;
  }
  .close:hover { color: var(--fg-1); }
</style>
