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

  /* Once the user activates the button it stays darker — the
     component is built for actions like Like/Dislike that are
     only meant to be pressed once per photo. Pressed state is
     derived from the parent-controlled `ariaPressed` prop when
     it is provided; otherwise we latch a local `selected` flag
     the first time the button is clicked. */
  let selected = $state(false);

  function effectivePressed(): boolean {
    if (ariaPressed === true || ariaPressed === 'true') return true;
    if (ariaPressed === false || ariaPressed === 'false') return false;
    return selected;
  }

  function handleClick(event: MouseEvent) {
    if (disabled) return;
    selected = true;
    if (onclick) onclick(event);
  }
</script>

{#if href}
  <a
    class="action"
    aria-pressed={effectivePressed() ? 'true' : 'false'}
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
    aria-pressed={effectivePressed() ? 'true' : 'false'}
    {disabled}
    {title}
    aria-expanded={ariaExpanded}
    aria-haspopup={ariaHaspopup}
    onclick={handleClick}
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
      color var(--t-fast);
    text-decoration: none;
    font-size: var(--fs-sm);
    font-weight: 500;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .action:hover { background: var(--glass-2); }
  .action:disabled { cursor: not-allowed; opacity: 0.5; }
  /* Permanent darken for actions that have already been taken. */
  .action[aria-pressed='true'] {
    background: rgba(0, 0, 0, 0.35);
    border-color: rgba(255, 255, 255, 0.18);
  }
</style>
