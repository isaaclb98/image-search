<script module lang="ts">
  /**
   * Tiny chip primitive used inside the prompt-chips list.
   * Has variants: positive (default) and negative (darker, red
   * soft accent). Each chip has a removal × button.
   */
</script>

<script lang="ts">
  type Props = {
    text: string;
    negative?: boolean;
    onRemove?: () => void;
    title?: string;
  };
  let { text, negative = false, onRemove, title }: Props = $props();
</script>

<span class="chip {negative ? 'neg' : 'pos'}" {title}>
  {#if negative}<span class="minus" aria-hidden="true">−</span>{/if}
  <span class="text">{text}</span>
  {#if onRemove}
    <button class="x" onclick={onRemove} aria-label="Remove {text}">×</button>
  {/if}
</span>

<style>
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--glass-1);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-pill);
    padding: 4px 8px 4px 12px;
    font-size: var(--fs-sm);
    line-height: 1;
    height: 28px;
    transition: background var(--t-fast) var(--ease-out);
  }
  .chip.neg {
    color: var(--fg-2);
    background: var(--negative-soft);
    border-color: rgba(255,122,138,0.30);
  }
  .chip:hover { background: var(--glass-2); }
  .chip.neg:hover { background: rgba(255,122,138,0.30); }
  .minus {
    color: var(--negative);
    font-weight: 600;
  }
  .text { white-space: nowrap; }
  .x {
    width: 18px;
    height: 18px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    color: var(--fg-3);
    border-radius: 50%;
    font-size: 14px;
    line-height: 1;
    transition: background var(--t-fast) var(--ease-out), color var(--t-fast);
  }
  .chip.neg .x { color: var(--negative); }
  .x:hover { background: var(--glass-2); color: var(--fg-1); }
</style>
