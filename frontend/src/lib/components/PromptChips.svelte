<script lang="ts">
  /**
   * PromptChips — positive/negative prompt row. Negative chips
   * render darker (per spec). The +/− toggle flips which kind
   * the next chip will be. Pressing Enter commits the current
   * input as a chip and clears it (per spec) but does NOT
   * fire a search — the user explicitly clicks the Search
   * button (issue #6 from the bug list).
   *
   * Pure UI: state lives in the parent (SearchComposer /
   * SearchPage). The component just renders and signals via
   * callbacks.
   */
  import Chip from './Chip.svelte';

  type Mode = 'pos' | 'neg';
  type Props = {
    positives: string[];
    negatives: string[];
    input: string;
    mode: Mode;
    onInput: (v: string) => void;
    onAdd: (text: string, mode: Mode) => void;
    onRemovePositive: (i: number) => void;
    onRemoveNegative: (i: number) => void;
    onMode: (m: Mode) => void;
  };
  let {
    positives,
    negatives,
    input,
    mode,
    onInput,
    onAdd,
    onRemovePositive,
    onRemoveNegative,
    onMode
  }: Props = $props();

  function commit() {
    const trimmed = (input ?? '').trim();
    if (!trimmed) return;
    onAdd(trimmed, mode);
    onInput(''); // clear on commit per spec
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      // Only commit the chip. Do NOT fire a search — the user
      // explicitly clicks the Search button.
      e.preventDefault();
      commit();
    } else if (
      e.key === 'Backspace' &&
      !input &&
      mode === 'pos' &&
      positives.length
    ) {
      onRemovePositive(positives.length - 1);
    }
  }
</script>

<div class="prompt-chips">
  <div class="row">
    {#each positives as p, i (i + '|' + p)}
      <Chip text={p} onRemove={() => onRemovePositive(i)} />
    {/each}
    {#each negatives as n, i (i + '|' + n)}
      <Chip text={n} negative onRemove={() => onRemoveNegative(i)} />
    {/each}
  </div>
  <div class="composer">
    <div class="toggle" role="tablist" aria-label="Prompt polarity">
      <button
        type="button"
        role="tab"
        class:active={mode === 'pos'}
        class="seg"
        onclick={() => onMode('pos')}
        aria-selected={mode === 'pos'}
      >+</button>
      <button
        type="button"
        role="tab"
        class:active={mode === 'neg'}
        class="seg"
        onclick={() => onMode('neg')}
        aria-selected={mode === 'neg'}
      >−</button>
    </div>
    <input
      class="composer-input"
      type="text"
      placeholder={mode === 'pos' ? 'Add a positive prompt…' : 'Add a negative prompt…'}
      value={input ?? ''}
      oninput={(e) => onInput((e.target as HTMLInputElement).value)}
      onkeydown={handleKeydown}
      autocomplete="off"
      spellcheck="false"
      aria-label="Add prompt"
    />
    <button
      type="button"
      class="add"
      onclick={commit}
      disabled={!(input ?? '').trim()}
      title="Add prompt"
    >+</button>
  </div>
</div>

<style>
  .prompt-chips {
    background: var(--glass-1);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-3);
    padding: 14px 14px 12px;
    backdrop-filter: var(--glass-medium);
    -webkit-backdrop-filter: var(--glass-medium);
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    min-height: 28px;
    padding-bottom: 10px;
  }
  .row:empty { display: none; }
  .composer {
    display: flex;
    align-items: center;
    gap: 8px;
    background: rgba(14,15,20,0.45);
    border: 1px solid var(--glass-edge);
    border-radius: var(--r-pill);
    padding: 4px 6px 4px 4px;
  }
  .toggle {
    display: inline-flex;
    background: var(--glass-1);
    border-radius: var(--r-pill);
    border: 1px solid var(--glass-edge);
    overflow: hidden;
  }
  .seg {
    width: 32px;
    height: 32px;
    color: var(--fg-3);
    background: transparent;
    border-radius: 0;
    font-weight: 600;
    font-size: var(--fs-md);
  }
  .seg.active {
    background: var(--accent);
    color: var(--fg-on-accent);
  }
  .composer-input {
    flex: 1;
    height: 32px;
    color: var(--fg-1);
    font-size: var(--fs-md);
  }
  .composer-input::placeholder { color: var(--fg-3); }
  .add {
    width: 32px;
    height: 32px;
    border-radius: 50%;
    background: var(--glass-2);
    color: var(--fg-1);
    border: 1px solid var(--glass-edge-strong);
    font-size: 18px;
    transition: background var(--t-fast);
  }
  .add:hover { background: var(--accent-soft); color: var(--fg-1); }
  .add:disabled { opacity: 0.35; pointer-events: none; }
</style>
