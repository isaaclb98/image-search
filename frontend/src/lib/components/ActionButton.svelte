<script lang="ts">
  import type { Snippet } from 'svelte';

  type Props = {
    children: Snippet;
    href?: string;
    onclick?: (event: MouseEvent) => void;
    disabled?: boolean;
    title?: string;
    ariaPressed?: boolean | 'true' | 'false';
    ariaExpanded?: boolean;
    ariaHaspopup?: 'menu' | 'dialog' | 'listbox' | 'tree' | 'grid' | 'false';
    target?: string;
    rel?: string;
  };

  let {
    children,
    href,
    onclick,
    disabled = false,
    title,
    ariaPressed,
    ariaExpanded,
    ariaHaspopup,
    target,
    rel
  }: Props = $props();
</script>

{#if href}
  <a
    class="action"
    {href}
    {target}
    {rel}
    {title}
    aria-expanded={ariaExpanded}
    aria-haspopup={ariaHaspopup}
    {onclick}
  >
    {@render children()}
  </a>
{:else}
  <button
    type="button"
    class="action"
    {disabled}
    {title}
    aria-pressed={ariaPressed}
    aria-expanded={ariaExpanded}
    aria-haspopup={ariaHaspopup}
    {onclick}
  >
    {@render children()}
  </button>
{/if}

<style>
  .action {
    height: 32px;
    padding: 0 14px;
    border-radius: var(--r-pill);
    background: var(--glass-1);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge);
    transition:
      background var(--t-fast),
      border-color var(--t-fast),
      color var(--t-fast),
      transform var(--t-fast);
    text-decoration: none;
    font-size: var(--fs-sm);
    font-weight: 500;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .action:hover { background: var(--glass-2); }
  .action:active { transform: scale(0.96); }
  .action:disabled { cursor: not-allowed; opacity: 0.5; }
</style>
