<script lang="ts">
  /**
   * Dropdown — positioned popover anchored to a trigger element.
   *
   * Why this exists (vs. an inline-floating div):
   *   - Position via getBoundingClientRect() so the menu sits flush
   *     against the trigger regardless of where the trigger is on the
   *     page (action bars centred at the bottom, inline buttons in
   *     sidebars, etc.) and regardless of how the layout shifts.
   *   - Glass surface so the menu reads as a sibling of the trigger,
   *     not a disconnected bubble.
   *   - ARIA + keyboard (Esc closes) so it's usable without a mouse.
   *   - **Portal** the menu to document.body so it escapes any
   *     ancestor's `backdrop-filter`/`transform`/`filter`/`contain`
   *     containing block (any of these would otherwise pin
   *     `position: fixed` to that ancestor and clip the menu). This
   *     is the bug that bit the first version when the trigger
   *     lived inside a `.glass`/`.glass-strong` element with
   *     `backdrop-filter`.
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

  /** Gap between the trigger edge and the menu (px). */
  const GAP = 8;

  /**
   * Svelte `use:` action that portals a node to document.body for
   * the lifetime of the component. The node is restored to its
   * original parent on destroy so SSR / unmount doesn't leak it.
   *
   * We portal the menu + caret because `backdrop-filter` (used by
   * `.glass`/`.glass-strong`) creates a new containing block that
   * traps `position: fixed` — without this, the menu would be
   * positioned and clipped relative to the trigger's glass ancestor
   * instead of the viewport.
   */
  function portal(node: HTMLElement) {
    const originalParent = node.parentElement!;
    document.body.appendChild(node);
    return {
      destroy() {
        if (node.parentElement === document.body) {
          document.body.removeChild(node);
        } else if (originalParent && node.parentElement) {
          // If Svelte moved it during teardown, drop it cleanly.
          node.remove();
        }
      }
    };
  }

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
  }

  async function toggle() {
    open = !open;
    if (open) {
      computePosition();
      await tick();
      // After mount, clamp to viewport and re-center caret.
      // IMPORTANT: getBoundingClientRect() returns viewport-relative
      // coords. After the portal moves the nodes to document.body,
      // that's still true (the elements are no longer nested, but
      // their rect is still measured against the viewport). So the
      // math works.
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
    // The menu is portaled to document.body, so it's no longer a
    // descendant of wrapperEl — check both the wrapper (for the
    // trigger) and the menu (for the popover).
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
</div>

{#if open}
  <div
    bind:this={menuEl}
    class="menu glass-strong"
    role="menu"
    aria-label={label}
    style:top="{pos.top}px"
    style:left="{pos.left}px"
    style:min-width={minWidth}
    use:portal
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

<style>
  .dropdown {
    position: relative;
    display: inline-flex;
  }
  /* Menu lives at document.body via the portal action. CSS
     `position: fixed` is still correct there because no ancestor
     between the portal root and the menu has transform / filter /
     contain / backdrop-filter (the whole point of the portal). */
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
