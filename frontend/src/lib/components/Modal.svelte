<script lang="ts">
  /**
   * Modal primitive — single reusable dialog. Consumers drive
   * visibility via the `open` prop; everything else (focus
   * trap, Escape handling, click-outside-to-close, ARIA) is
   * handled here.
   *
   * Replaces the native `window.prompt` / `window.confirm`
   * dialogs that were scattered across Albums and Saved Searches.
   * The dialog-store consumers (`confirmStore`, `promptStore`)
   * use this primitive so every dialog in the app renders with
   * the same glass aesthetic and accessibility tree.
   *
   * Layout:
   *   <Modal open onClose={…} title="…" kind="danger">
   *     <p>body</p>
   *     {#snippet footer()}
   *       <Button>Cancel</Button>
   *       <Button variant="primary">Confirm</Button>
   *     {/snippet}
   *   </Modal>
   */
  import { onMount, tick } from 'svelte';
  import type { Snippet } from 'svelte';

  type Props = {
    open: boolean;
    onClose: () => void;
    title?: string;
    /** Visual flavour. "danger" gives the primary button a red
     *  hue so destructive confirms (delete album, etc.) are
     *  scannable at a glance. */
    kind?: 'default' | 'danger';
    /** Body content. */
    children?: Snippet;
    /** Footer with action buttons. */
    footer?: Snippet;
    /** Selector for the element to focus when the dialog opens.
     *  Falls back to the first focusable inside, then the dialog
     *  itself. */
    initialFocus?: string;
  };

  let {
    open,
    onClose,
    title,
    kind = 'default',
    children,
    footer,
    initialFocus
  }: Props = $props();

  let dialog: HTMLDivElement | undefined = $state();
  let lastFocused: HTMLElement | null = null;

  // Focus trap. When the modal opens, remember the element
  // that had focus (so we can restore it on close) and shift
  // focus into the dialog. While open, Tab/Shift+Tab cycles
  // through the focusable elements inside the dialog.
  $effect(() => {
    if (open) {
      lastFocused =
        (document.activeElement as HTMLElement | null) ?? null;
      tick().then(() => {
        if (!dialog) return;
        const sel = initialFocus ?? '[data-initial-focus], input:not([disabled])';
        const target = (sel ? dialog.querySelector<HTMLElement>(sel) : null)
          ?? dialog.querySelector<HTMLElement>(focusableSelector(dialog) ?? 'button, [tabindex]:not([tabindex="-1"])');
        (target ?? dialog).focus();
      });
    } else if (lastFocused) {
      // Restore focus to the element that opened the dialog.
      // Without this, keyboard users lose their place.
      lastFocused.focus();
      lastFocused = null;
    }
  });

  function focusableSelector(root: HTMLElement): string {
    return [
      'a[href]',
      'button:not([disabled])',
      'input:not([disabled])',
      'textarea:not([disabled])',
      'select:not([disabled])',
      '[tabindex]:not([tabindex="-1"])'
    ].join(',');
  }

  function focusables(): HTMLElement[] {
    if (!dialog) return [];
    return Array.from(
      dialog.querySelectorAll<HTMLElement>(focusableSelector(dialog))
    ).filter((el) => !el.hasAttribute('disabled') && el.offsetParent !== null);
  }

  function onKeydown(e: KeyboardEvent) {
    if (!open || !dialog) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === 'Tab') {
      const els = focusables();
      if (els.length === 0) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = els[0];
      const last = els[els.length - 1];
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey) {
        if (active === first || !dialog.contains(active)) {
          e.preventDefault();
          last.focus();
        }
      } else if (active === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  function onBackdropClick(e: MouseEvent) {
    // Click on the backdrop itself closes the dialog; clicks on
    // the dialog content do not bubble here.
    if (e.target === e.currentTarget) onClose();
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <div
    class="backdrop"
    class:danger={kind === 'danger'}
    onclick={onBackdropClick}
    oncontextmenu={(e) => e.preventDefault()}
    role="presentation"
  >
    <div
      bind:this={dialog}
      class="dialog glass-strong"
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? 'modal-title' : undefined}
      tabindex="-1"
    >
      {#if title}
        <h2 id="modal-title" class="title">{title}</h2>
      {/if}
      <div class="body">
        {#if children}{@render children()}{/if}
      </div>
      {#if footer}
        <div class="footer">
          {@render footer()}
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    background: rgba(8, 8, 12, 0.65);
    backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9000;
    padding: 24px;
    animation: backdrop-in 150ms var(--ease-out, ease);
  }
  .backdrop.danger .dialog {
    border-color: rgba(220, 95, 95, 0.5);
  }
  .dialog {
    width: min(440px, 100%);
    max-height: calc(100vh - 48px);
    overflow-y: auto;
    border-radius: 14px;
    padding: 22px 22px 18px;
    display: flex;
    flex-direction: column;
    gap: 14px;
    color: var(--fg-1);
    animation: dialog-in 180ms var(--ease-out, ease);
  }
  .title {
    margin: 0;
    font-size: var(--fs-md, 16px);
    font-weight: 600;
    letter-spacing: 0.01em;
  }
  .body {
    font-size: 14px;
    line-height: 1.5;
    color: var(--fg-2);
  }
  .footer {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
    margin-top: 4px;
  }
  @keyframes backdrop-in {
    from { opacity: 0; }
    to { opacity: 1; }
  }
  @keyframes dialog-in {
    from {
      opacity: 0;
      transform: translateY(8px) scale(0.98);
    }
    to {
      opacity: 1;
      transform: translateY(0) scale(1);
    }
  }
</style>