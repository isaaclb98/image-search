<script lang="ts">
  /**
   * Single button primitive. Used for every interactive surface
   * that triggers an action. Three intents: primary, secondary,
   * ghost. The `additional` slot lets us drop in extras like a
   * label or icon.
   */
  type Variant = 'primary' | 'secondary' | 'ghost' | 'icon';
  type Size = 'sm' | 'md' | 'lg';
  type Props = {
    variant?: Variant;
    size?: Size;
    href?: string;
    type?: 'button' | 'submit';
    disabled?: boolean;
    title?: string;
    onclick?: (e: MouseEvent) => void;
    children?: import('svelte').Snippet;
    /** Marker for the Modal primitive: when true, this button
     *  receives focus on dialog open. The Modal reads the
     *  attribute to find the initial-focus target rather than
     *  relying on DOM order, so destructive confirms can sit
     *  on the right without stealing focus from Cancel. */
    initialFocus?: boolean;
  };
  let {
    variant = 'secondary',
    size = 'md',
    href,
    type = 'button',
    disabled = false,
    title,
    onclick,
    initialFocus,
    children
  }: Props = $props();
</script>

{#if href}
  <a
    class="btn {variant} {size}"
    {href}
    aria-disabled={disabled ? 'true' : undefined}
    {title}
  >
    {#if children}{@render children()}{/if}
  </a>
{:else}
  <button
    class="btn {variant} {size}"
    data-initial-focus={initialFocus ? '' : undefined}
    {type}
    {disabled}
    {title}
    {onclick}
  >
    {#if children}{@render children()}{/if}
  </button>
{/if}

<style>
  .btn {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--s-1);
    border-radius: var(--r-pill);
    font-weight: 500;
    letter-spacing: 0.01em;
    transition: background var(--t-fast) var(--ease-out),
                transform var(--t-fast) var(--ease-out),
                border-color var(--t-fast) var(--ease-out),
                color var(--t-fast) var(--ease-out);
    border: 1px solid transparent;
    user-select: none;
    text-decoration: none;
    white-space: nowrap;
  }
  .btn:disabled, .btn[aria-disabled='true'] {
    opacity: 0.45;
    cursor: not-allowed;
    pointer-events: none;
  }

  /* sizes */
  .sm { height: 30px; padding: 0 12px; font-size: var(--fs-sm); }
  .md { height: 38px; padding: 0 16px; font-size: var(--fs-md); }
  .lg { height: 46px; padding: 0 22px; font-size: var(--fs-lg); }

  /* variants */
  .primary {
    background: var(--accent);
    color: var(--fg-on-accent);
  }
  .primary:hover { background: var(--accent-2); }

  .secondary {
    background: var(--glass-2);
    color: var(--fg-1);
    border-color: var(--glass-edge-strong);
    backdrop-filter: var(--glass-medium);
    -webkit-backdrop-filter: var(--glass-medium);
  }
  .secondary:hover {
    /* focus feedback: brighter glass + accent border hint */
    background: color-mix(in srgb, var(--fg-1) 14%, transparent);
    border-color: var(--accent-soft);
  }

  .ghost {
    background: transparent;
    color: var(--fg-2);
    border-color: var(--glass-edge);
  }
  .ghost:hover {
    background: var(--glass-1);
    color: var(--fg-1);
  }

  .icon {
    width: 38px;
    height: 38px;
    padding: 0;
    background: transparent;
    color: var(--fg-2);
    border-radius: 50%;
  }
  .icon:hover { background: var(--glass-2); color: var(--fg-1); }
  .icon.sm { width: 30px; height: 30px; }
  .icon.lg { width: 46px; height: 46px; }

  .btn:active { transform: translateY(1px); }
</style>
