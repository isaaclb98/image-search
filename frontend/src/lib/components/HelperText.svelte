<script lang="ts">
  /**
   * Helper text primitive — small descriptive text below a form
   * control. Use directly when the Field composition isn't
   * appropriate (rare); most callers should use Field's
   * `helper` snippet which handles aria-describedby wiring.
   */
  import type { Snippet } from 'svelte';
  type Props = {
    children: Snippet;
    id?: string;
  };
  let { children, id }: Props = $props();
</script>

<p class="helper" {id}>
  {@render children()}
</p>

<style>
  .helper {
    margin: 0;
    font-size: var(--fs-xs);
    color: var(--fg-3);
    line-height: 1.4;
  }
</style>
