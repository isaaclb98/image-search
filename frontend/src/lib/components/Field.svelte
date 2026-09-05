<script lang="ts">
  /**
   * Form field composition primitive. Bundles an optional Label,
   * the form control (slot), and an optional HelperText into a
   * single accessible unit. Adopt this from any new form surface;
   * existing Input consumers keep using Input directly with no
   * migration cost.
   *
   *   <Field>
   *     {#snippet label()}Filename{/snippet}
   *     <Input ... />
   *     {#snippet helper()}
   *       Wildcards: <Kbd>*</Kbd> and <Kbd>?</Kbd>
   *     {/snippet}
   *   </Field>
   *
   * Renders a real <label> wrapping the slot so click-on-label
   * focuses the contained control (which our <Input> achieves
   * inherently via the wrapping <label class="input-wrap">).
   */
  import type { Snippet } from 'svelte';

  type Props = {
    children: Snippet;
    label?: Snippet;
    helper?: Snippet;
    /** Optional id; forwarded to the wrapping element so the
     *  Label's `for` and the helper's `aria-describedby` can
     *  reference it. If omitted, a random id is generated. */
    id?: string;
  };
  let { children, label, helper, id }: Props = $props();
  let fallbackId = $state(crypto.randomUUID());
  let fieldId = $derived(id ?? fallbackId);
  let helperId = $derived(`${fieldId}-helper`);
</script>

<div class="field">
  {#if label}
    <label class="label" for={fieldId}>
      {@render label()}
    </label>
  {/if}
  <div class="control">
    {@render children()}
  </div>
  {#if helper}
    <p class="helper" id={helperId}>
      {@render helper()}
    </p>
  {/if}
</div>

<style>
  .field {
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: 100%;
  }
  .label {
    font-size: var(--fs-sm);
    color: var(--fg-2);
    /* Tabular alignment with input — pull label slightly into
       the padding gutter so the first character aligns with
       the input's first character. */
    padding-left: 2px;
  }
  .control { width: 100%; }
  .helper {
    margin: 0;
    font-size: var(--fs-xs);
    color: var(--fg-3);
    padding-left: 2px;
    line-height: 1.4;
  }
</style>
