<script lang="ts">
  /**
   * Dropdown — positioned popover anchored to a trigger element.
   *
   * Why this exists (vs. an inline-floating div):
   *   - Position via getBoundingClientRect() so the menu sits flush
   *     against the trigger regardless of where the trigger is on the
   *     page (action bars centred at the bottom, inline buttons in
   *     sidebars, etc.) and regardless of how the layout shifts.
   *   - Glass surface + caret so the menu reads as a sibling of the
   *     trigger, not a disconnected bubble.
   *   - ARIA + keyboard (Esc closes) so it's usable without a mouse.
   *
   * The trigger is provided as a snippet. It is expected to be an
   * interactive element (typically a button) — the wrapper itself is
   * NOT interactive, so ARIA + keyboard semantics live entirely on
   * the trigger. The wrapper handles position bookkeeping only.
   *
   * Per AGENTS.md this is a primitive — no bespoke positioning logic
   * in callers. Adopt it from any place that needs a popover (the
   * lightbox's add-to-album being the first consumer).
   */
  import type { Snippet } from 'svelte';
  import { tick } from 'svelte';

  type Item = {
    id: string | number;
    label: string;
    disabled?: boolean;
  };

  type Props = {
    /** The trigger element. Should be interactive (button/a/input). */
    trigger: Snippet<[{ open: boolean; toggle: () => void }]>;
    /** Items shown in the popover. */
    items: Item[];
    /** Direction the menu opens relative to the trigger. */
    align?: 'up' | 'down';
    /** Called when the user picks an item. */
    onPick: (item: Item) => void | Promise<void>;
    /** Accessible label for the menu (referenced by trigger's aria-label or aria-controls). */
    label: string;
    /** Minimum width of the popover. */
    minWidth?: string;
    /** Optional message rendered when items is empty. */
    emptyMessage?: string;
  };

  let {
    trigger,
    items,
    align = 'up',
    onPick,
    label,
    minWidth = '200px',
    emptyMessage = 'No options available.'
  }: Props = $props();

  let open = $state(false);
  let wrapperEl: HTMLDivElement | undefined = $state();
  let menuEl: HTMLDivElement | undefined = $state();

  /** Coordinates for `position: fixed` placement, recomputed each open. */
  let pos = $state<{ top: number; left: number }>({ top: 0, left: 0 });

  /** Caret offset from the menu's left edge, in px. */
  let caretOffset = $state(0);

  /** Gap between the trigger edge and the menu (px). */
  const GAP = 8;

  function computePosition() {
    if (!wrapperEl) return;
    const rect = wrapperEl.getBoundingClientRect();
    if (align === 'up') {
      // Default: menu's bottom edge sits GAP above the trigger's top
      // edge. We anchor via `top` (the menu's top edge) — but we
      // don't know the menu's height until after mount, so this
      // helper just sets the right-aligned initial guess. The
      // two-pass post-mount clamp in toggle() corrects the top.
      pos = { top: rect.top - GAP, left: rect.right };
    } else {
      pos = { top: rect.bottom + GAP, left: rect.right };
    }
    // Caret starts centered under the trigger; post-mount clamp
    // adjusts if the menu was shifted left to stay in the viewport.
    caretOffset = rect.width / 2;
  }

  async function toggle() {
    open = !open;
    if (open) {
      computePosition();
      await tick();
      // After mount, clamp to viewport and re-center caret.
      if (menuEl && wrapperEl) {
        const menuRect = menuEl.getBoundingClientRect();
        const wrapperRect = wrapperEl.getBoundingClientRect();
        if (align === 'up') {
          // Menu's top edge = trigger.top - GAP - menu.height.
          pos = { ...pos, top: wrapperRect.top - GAP - menuRect.height };
        }
        // Keep menu within the viewport horizontally. Default
        // alignment is right-edge flush with trigger's right; shift
        // left if it would overflow, or right if it would clip.
        let left = wrapperRect.right - menuRect.width;
        if (left < GAP) left = GAP;
        if (left + menuRect.width > window.innerWidth - GAP) {
          left = window.innerWidth - GAP - menuRect.width;
        }
        pos = { ...pos, left };
        // Recompute caret offset so it stays centered under the
        // trigger even after horizontal clamping.
        caretOffset = wrapperRect.left + wrapperRect.width / 2 - left;
      }
      // Focus first item for keyboard users.
      menuEl?.querySelector<HTMLButtonElement>('button.item:not(:disabled)')?.focus();
    }
  }

  function close() {
    open = false;
  }

  function onDocPointer(e: MouseEvent | TouchEvent) {
    if (!open) return;
    const target = e.target as Node;
    if (!menuEl?.contains(target) && !wrapperEl?.contains(target)) {
      close();
    }
  }

  function onKey(e: KeyboardEvent) {
    if (!open) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }

  $effect(() => {
    if (open) {
      document.addEventListener('mousedown', onDocPointer);
      document.addEventListener('touchstart', onDocPointer);
      document.addEventListener('keydown', onKey);
      return () => {
        document.removeEventListener('mousedown', onDocPointer);
        document.removeEventListener('touchstart', onDocPointer);
        document.removeEventListener('keydown', onKey);
      };
    }
  });
</script>

<!-- Wrapper is positioning-only. NOT interactive. The snippet
     inside renders the actual interactive trigger. -->
<div class="dropdown" bind:this={wrapperEl}>
  {@render trigger({ open, toggle })}

  {#if open}
    {#if align === 'up'}
      <div class="caret caret-up" style:left="{caretOffset}px" aria-hidden="true"></div>
    {:else}
      <div class="caret caret-down" style:left="{caretOffset}px" aria-hidden="true"></div>
    {/if}
    <div
      bind:this={menuEl}
      class="menu glass-strong"
      role="menu"
      aria-label={label}
      style:top="{pos.top}px"
      style:left="{pos.left}px"
      style:min-width={minWidth}
    >
      {#if items.length === 0}
        <div class="empty">{emptyMessage}</div>
      {:else}
        {#each items as it (it.id)}
          <button
            type="button"
            class="item"
            role="menuitem"
            disabled={it.disabled}
            onclick={async () => {
              await onPick(it);
              close();
            }}
          >
            {it.label}
          </button>
        {/each}
      {/if}
    </div>
  {/if}
</div>

<style>
  .dropdown {
    position: relative;
    display: inline-flex;
  }
  /* Caret + menu use fixed positioning so they escape the wrapper's
     stacking context (otherwise nested z-indices / overflow-hidden
     ancestors could clip them). */
  .caret {
    position: fixed;
    width: 0;
    height: 0;
    pointer-events: none;
    z-index: 511; /* above the menu */
    transform: translateX(-50%);
  }
  .caret-up {
    border-left: 8px solid transparent;
    border-right: 8px solid transparent;
    border-top: 8px solid var(--glass-edge-strong);
  }
  .caret-down {
    border-left: 8px solid transparent;
    border-right: 8px solid transparent;
    border-bottom: 8px solid var(--glass-edge-strong);
  }
  .menu {
    position: fixed;
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 6px;
    border-radius: var(--r-2);
    z-index: 510;
    box-shadow: var(--shadow-2);
    animation: dd-fade var(--t-fast) var(--ease-out);
  }
  @keyframes dd-fade {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .item {
    appearance: none;
    background: transparent;
    border: 1px solid transparent;
    color: var(--fg-1);
    padding: 8px 12px;
    border-radius: var(--r-1);
    text-align: left;
    font: inherit;
    font-size: var(--fs-sm);
    cursor: pointer;
    transition:
      background var(--t-fast) var(--ease-out),
      border-color var(--t-fast) var(--ease-out);
  }
  .item:hover,
  .item:focus-visible {
    background: rgba(255, 255, 255, 0.06);
    border-color: var(--glass-edge);
    outline: none;
  }
  .item:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .empty {
    padding: 12px;
    font-size: var(--fs-sm);
    color: var(--fg-3);
    text-align: center;
  }
</style>
