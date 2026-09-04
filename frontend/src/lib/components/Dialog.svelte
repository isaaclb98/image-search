<script module lang="ts">
  /**
   * Dialog store: Promise-based `confirm()` and `prompt()`
   * helpers that render via the `Modal` primitive. Replaces
   * the scattered `window.prompt` / `window.confirm` calls in
   * Albums and Saved Searches with a styled, accessible
   * dialog that fits the rest of the app's glass aesthetic.
   *
   * Usage:
   *   import { dialog } from '$lib/components/Dialog.svelte';
   *   const ok = await dialog.confirm({ title: 'Delete?', kind: 'danger' });
   *   const name = await dialog.prompt({ title: 'Album name?', defaultValue: '' });
   *
   *   // `name === null`  → user cancelled (Esc, Cancel, backdrop click)
   *   // `name === ''`    → user confirmed with empty input
   *   // `name === 'foo'` → user confirmed with "foo"
   *
   * Single-instance: only one dialog can be open at a time. If
   * a caller opens a second one while the first is resolving,
   * the latest call wins — the older promise stays pending until
   * the newer one resolves. This is fine for our use cases
   * (user actions are serial) but worth knowing if a future
   * caller races two confirms.
   */
  export type ConfirmOpts = {
    title: string;
    body?: string;
    confirmLabel?: string;
    cancelLabel?: string;
    kind?: 'default' | 'danger';
  };
  export type PromptOpts = {
    title: string;
    label?: string;
    defaultValue?: string;
    confirmLabel?: string;
    cancelLabel?: string;
  };

  // Plain class + module-level instance rather than $state —
  // the module script's reactive boundary makes a typed
  // discriminated union awkward (the store lives in the
  // module scope but is consumed by the per-instance render
  // layer). A writable that exposes a subscribe callback is
  // the standard Svelte 4-style escape hatch, and the render
  // layer below subscribes to it.
  type DialogState =
    | { kind: 'none' }
    | { kind: 'confirm'; opts: ConfirmOpts; resolve: (v: boolean) => void }
    | { kind: 'prompt'; opts: PromptOpts; resolve: (v: string | null) => void };

  function createDialogStore() {
    let current: DialogState = { kind: 'none' };
    const subs = new Set<() => void>();
    const api = {
      confirm(opts: ConfirmOpts): Promise<boolean> {
        return new Promise((resolve) => {
          current = { kind: 'confirm', opts, resolve };
          subs.forEach((s) => s());
        });
      },
      prompt(opts: PromptOpts): Promise<string | null> {
        return new Promise((resolve) => {
          current = { kind: 'prompt', opts, resolve };
          subs.forEach((s) => s());
        });
      },
      close(result: boolean | string | null) {
        if (current.kind === 'confirm') {
          current.resolve(result as boolean);
        } else if (current.kind === 'prompt') {
          current.resolve(result as string | null);
        }
        current = { kind: 'none' };
        subs.forEach((s) => s());
      },
      subscribe(fn: () => void): () => void {
        subs.add(fn);
        return () => subs.delete(fn);
      },
      get state(): DialogState {
        return current;
      }
    };
    return api;
  }

  const store = createDialogStore();
  export const dialog = store;
</script>

<script lang="ts">
  import Modal from './Modal.svelte';
  import Button from './Button.svelte';

  // Bridge: the store is in module scope, but the render layer
  // needs reactive access. Svelte 5 has $state in module scripts
  // but a typed discriminated union there creates edge cases for
  // the type checker (the bug we hit). A plain subscribe/store
  // pattern keeps the types clean and matches Svelte 4 idiom.
  let view: DialogState = $state({ kind: 'none' });
  $effect(() => {
    return dialog.subscribe(() => {
      view = dialog.state;
    });
  });

  // Local form state for the prompt. Reset every time the
  // prompt opens.
  let promptValue = $state('');
  let promptInput: HTMLInputElement | undefined = $state();

  $effect(() => {
    if (view.kind === 'prompt') {
      promptValue = view.opts.defaultValue ?? '';
    }
  });

  function onPromptSubmit(e: Event) {
    e.preventDefault();
    if (view.kind === 'prompt') {
      dialog.close(promptValue);
    }
  }
</script>

{#if view.kind === 'confirm'}
  {@const confirmView = view}
  <Modal
    open
    kind={confirmView.opts.kind ?? 'default'}
    title={confirmView.opts.title}
    onClose={() => dialog.close(false)}
  >
    {#if confirmView.opts.body}
      <p>{confirmView.opts.body}</p>
    {/if}
    {#snippet footer()}
      <Button onclick={() => dialog.close(false)}>
        {confirmView.opts.cancelLabel ?? 'Cancel'}
      </Button>
      <Button
        variant="primary"
        onclick={() => dialog.close(true)}
        initialFocus
      >
        {confirmView.opts.confirmLabel ?? 'Confirm'}
      </Button>
    {/snippet}
  </Modal>
{:else if view.kind === 'prompt'}
  {@const promptView = view}
  <Modal
    open
    title={promptView.opts.title}
    onClose={() => dialog.close(null)}
  >
    <form onsubmit={onPromptSubmit}>
      {#if promptView.opts.label}
        <label class="lab" for="dialog-prompt-input">{promptView.opts.label}</label>
      {/if}
      <input
        id="dialog-prompt-input"
        bind:this={promptInput}
        bind:value={promptValue}
        type="text"
        class="input"
      />
    </form>
    {#snippet footer()}
      <Button onclick={() => dialog.close(null)}>
        {promptView.opts.cancelLabel ?? 'Cancel'}
      </Button>
      <Button
        variant="primary"
        onclick={() => dialog.close(promptValue)}
      >
        {promptView.opts.confirmLabel ?? 'Save'}
      </Button>
    {/snippet}
  </Modal>
{/if}

<style>
  .lab {
    display: block;
    font-size: 12px;
    color: var(--fg-2);
    margin-bottom: 6px;
  }
  .input {
    width: 100%;
    height: 38px;
    padding: 0 12px;
    border-radius: 8px;
    background: rgba(8, 10, 14, 0.55);
    border: 1px solid var(--glass-edge);
    color: var(--fg-1);
    font-size: 14px;
    font-family: inherit;
  }
  .input:focus {
    outline: none;
    border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent);
  }
</style>