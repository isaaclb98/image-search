<script lang="ts">
  /**
   * Icon — single source of truth for the app's icon set.
   *
   * Why this exists (vs. text glyphs like × ‹ › ♥ −):
   *
   *   1. Text glyphs inherit the surrounding font. The lightbox
   *      close button (×) and the chip × button and the
   *      dislike badge (−) all used to render slightly off-
   *      center because the inherited font's line-box doesn't
   *      equal the visual glyph bounds. SVGs sit in a viewBox
   *      so the optical center is exact.
   *
   *   2. Strokes are `currentColor` by default — set the icon's
   *      CSS `color` (or rely on the surrounding text color)
   *      and the icon follows. No per-icon colour prop, no
   *      duplicated classes.
   *
   *   3. One place to evolve the icon library. Adding a new
   *      icon = one entry in the `paths` map + one optional
   *      size. No ad-hoc SVGs scattered through the codebase
   *      (AGENTS.md: "modular primitives and components;
   *      no/very minimal bespoke design").
   *
   * Usage:
   *   <Icon name="close" />
   *   <Icon name="chevron-right" size={18} />
   *   <span style="color: var(--negative)"><Icon name="heart" /></span>
   *
   * Geometry: every glyph is designed in a 24×24 viewBox with a
   * 2-unit stroke and `stroke-linecap="round"` /
   * `stroke-linejoin="round"` so they look right at any rendered
   * size (the buttons render them at 16-26px). `fill="none"` on
   * strokes-only icons; the heart is the only filled one.
   */
  export type IconName =
    | 'close'
    | 'chevron-left'
    | 'chevron-right'
    | 'chevron-up'
    | 'chevron-down'
    | 'heart'
    | 'heart-filled'
    | 'minus'
    | 'plus'
    | 'check'
    | 'search'
    | 'menu'
    | 'external-link'
    | 'download'
    | 'trash'
    | 'info'
    | 'alert'
    | 'spinner'
    | 'dot'
    | 'play'
    | 'pause'
    // Brand mark: 4-point sparkle (the Cursor / Perplexity / Arc
    // family of marks). The two crossing strokes taper to
    // sharp points so it reads as a "real" mark, not just two
    // lines. Asymmetric: a small inner sparkle on the
    // upper-right adds personality so it doesn't look like a
    // stock asterisk.
    | 'sparkle';

  type Props = {
    name: IconName;
    /** Rendered pixel size. Both width and height. Default 18. */
    size?: number;
    /** Optional title for accessibility; sets aria-label + role. */
    title?: string;
    /** Override stroke colour. Defaults to currentColor. */
    color?: string;
  };

  let { name, size = 18, title, color }: Props = $props();

  /**
   * Each entry is one or more <path>/<line>/<circle> children
   * for a 24-unit viewBox. All strokes use currentColor.
   * Keep glyphs simple — these are UI icons, not illustrations.
   */
  const paths: Record<IconName, string> = {
    'close': '<path d="M6 6 L18 18 M18 6 L6 18"/>',
    'chevron-left': '<path d="M15 6 L9 12 L15 18"/>',
    'chevron-right': '<path d="M9 6 L15 12 L9 18"/>',
    'chevron-up': '<path d="M6 15 L12 9 L18 15"/>',
    'chevron-down': '<path d="M6 9 L12 15 L18 9"/>',
    // Heart drawn as a stroke path so it pairs with the
    // lightbox-style monochrome palette; filled variant
    // available for when a "saved" state needs to be loud.
    'heart': '<path d="M12 20 C12 20 4 14.5 4 9 C4 6 6 4 8.5 4 C10 4 11.5 5 12 6.5 C12.5 5 14 4 15.5 4 C18 4 20 6 20 9 C20 14.5 12 20 12 20 Z"/>',
    'heart-filled': '<path d="M12 20 C12 20 4 14.5 4 9 C4 6 6 4 8.5 4 C10 4 11.5 5 12 6.5 C12.5 5 14 4 15.5 4 C18 4 20 6 20 9 C20 14.5 12 20 12 20 Z" fill="currentColor" stroke="none"/>',
    'minus': '<path d="M5 12 L19 12"/>',
    'plus': '<path d="M12 5 L12 19 M5 12 L19 12"/>',
    'check': '<path d="M5 12 L10 17 L19 7"/>',
    'search': '<circle cx="11" cy="11" r="6"/><path d="M16 16 L20 20"/>',
    'menu': '<path d="M4 7 L20 7 M4 12 L20 12 M4 17 L20 17"/>',
    'external-link': '<path d="M14 4 L20 4 L20 10 M20 4 L11 13 M19 13 L19 19 L5 19 L5 5 L11 5"/>',
    'download': '<path d="M12 4 L12 15 M7 11 L12 16 L17 11 M5 20 L19 20"/>',
    'trash': '<path d="M5 7 L19 7 M9 7 L9 4 L15 4 L15 7 M7 7 L7 19 L17 19 L17 7"/>',
    'info': '<circle cx="12" cy="12" r="8"/><path d="M12 11 L12 16 M12 8 L12 8.5"/>',
    'alert': '<path d="M12 4 L22 20 L2 20 Z"/><path d="M12 10 L12 15 M12 17 L12 17.5"/>',
    // 8-stroke spinner — pass an animated wrapper if you want
    // motion; the static glyph renders as a sun-burst icon.
    'spinner': '<circle cx="12" cy="12" r="8" stroke-dasharray="6 32" stroke-linecap="round"/>',
    'dot': '<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>',
    // Play / pause: classic media-control glyphs. The play triangle
    // is shifted right by ~2 so it reads optically centred (a
    // geometrically-centred triangle looks left-heavy at small
    // sizes). Pause uses two equal vertical strokes — same width as
    // the triangle base so toggling between the two feels even.
    'play': '<path d="M8 5 L19 12 L8 19 Z" fill="currentColor" stroke="none"/>',
    'pause': '<path d="M9 5 L9 19 M15 5 L15 19"/>',
    // Logo: photo-frame glyph. Stroked square + horizon + sun.
    // Same motif as the favicon so the in-app brand mark and
    // the tab icon read as the same product.
    // Sparkle: a 4-point starburst made of two crossing
    // diamond strokes. The points taper to sharp tips via
    // two overlapping paths that meet at the centre, so the
    // silhouette reads as a real mark, not as two crossed
    // lines. The small secondary sparkle in the upper-right
    // breaks symmetry and signals "AI / intelligent search"
    // without leaning into the cliché of the 5-point star.
    'sparkle': '<path d="M12 2 L13.2 9.6 L20.8 10.8 L13.2 12 L12 19.6 L10.8 12 L3.2 10.8 L10.8 9.6 Z"/><path d="M19 16 L19.4 17.6 L21 18 L19.4 18.4 L19 20 L18.6 18.4 L17 18 L18.6 17.6 Z"/>'
  };
</script>

<svg
  xmlns="http://www.w3.org/2000/svg"
  viewBox="0 0 24 24"
  width={size}
  height={size}
  role={title ? 'img' : 'presentation'}
  aria-label={title}
  aria-hidden={title ? undefined : 'true'}
  fill="none"
  stroke={color ?? 'currentColor'}
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
>
  {#if title}<title>{title}</title>{/if}
  <!-- eslint-disable-next-line svelte/no-at-html-tags -->
  {@html paths[name]}
</svg>
