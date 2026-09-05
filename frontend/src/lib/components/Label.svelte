<script lang="ts">
  /**
   * Standalone label primitive. Used directly when a Field
   * composition is overkill (e.g. a simple labeled checkbox).
   *
   * Most callers should use Field + the `label` snippet instead —
   * that wires the Label to the contained control via `for`/`id`
   * automatically.
   */
  import type { Snippet } from 'svelte';
  type Props = {
    children: Snippet;
    for?: string;
  };
  let { children, for: htmlFor }: Props = $props();
</script>

<label class="label" for={htmlFor}>
  {@render children()}
</label>

<style>
  .label {
    font-size: var(--fs-sm);
    color: var(--fg-2);
  }
</style>
