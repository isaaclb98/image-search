<script lang="ts">
  let { color = 'transparent' }: { color?: string } = $props();
</script>

<div class="tint" style:background={color}></div>

<style>
  .tint {
    position: absolute;
    inset: 0;
    z-index: -1;
    border-radius: inherit;
    pointer-events: none;
    mix-blend-mode: soft-light;
    opacity: 0.8;
    filter: blur(8px);
  }
</style>
