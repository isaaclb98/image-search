<script lang="ts">
  import { onMount } from 'svelte';
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

  /* Brief visual confirmation that the button was just clicked.
     Sets `pressed` for a few hundred ms after each activation,
     which adds a flashing outline / background via the
     `.action--pressed` rule. Re-firing on a disabled button is a
     no-op; on a link, the click still opens the target so the
     flash is rarely seen, but the same handler is shared for
     consistency. */
  let pressed = $state(false);
  let pressTimer: ReturnType<typeof setTimeout> | undefined;

  function flash() {
    pressed = true;
    if (pressTimer) clearTimeout(pressTimer);
    pressTimer = setTimeout(() => (pressed = false), 220);
  }

  function handleClick(event: MouseEvent) {
    if (disabled) return;
    flash();
    if (onclick) onclick(event);
  }

  onMount(() => () => {
    if (pressTimer) clearTimeout(pressTimer);
  });
</script>

{#if href}
  <a
    class="action {pressed ? 'action--pressed' : ''}"
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
    class="action {pressed ? 'action--pressed' : ''}"
    {disabled}
    {title}
    aria-pressed={ariaPressed}
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
      color var(--t-fast),
      transform var(--t-fast),
      box-shadow var(--t-fast);
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
  /* Confirmation flash: a brighter background and a soft halo so the
     user knows the action was registered. */
  .action--pressed {
    background: var(--accent);
    color: var(--bg-1);
    border-color: var(--accent);
    box-shadow: 0 0 0 6px rgba(255, 255, 255, 0.12);
    transform: scale(0.96);
  }
</style>
