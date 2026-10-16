/**
 * useKeyboardNav — extracts the Lightbox's `onKey` handler
 * into a reusable composable. The Lightbox's keyboard
 * behaviour is:
 *
 *   Escape                          → close
 *   ArrowLeft / 'a'                 → prev
 *   ArrowRight / 'd'                → next
 *   Home                            → jump to first
 *   End                             → jump to last
 *   PageUp                          → skip back 10
 *   PageDown                        → skip forward 10
 *   'f' / 'F'                       → toggle favourite
 *   ' ' (Space)                     → toggle slideshow
 *
 * Two focus-gate rules from the original handler:
 *   1. Home / End / PageUp / PageDown / Space skip when
 *      a <button> is focused (don't fight synthetic click
 *      events; browsers don't synthesise clicks for
 *      ArrowLeft/Right so those run regardless).
 *   2. 'f' / 'F' skip when an <input> or <textarea> is
 *      focused (so the rename-on-album input doesn't lose
 *      keystrokes).
 *
 * The composable is pure JS (no runes) so it's easy to
 * unit test. The Lightbox passes the actions + state
 * getters; the composable returns a `handleKey` function
 * to wire to <svelte:window onkeydown={...}>.
 */
export type KeyboardNavOptions = {
  getItemsLength: () => number;
  getIdx: () => number;
  setIdx: (next: number) => void;
  onClose: () => void;
  onPrev: () => void;
  onNext: () => void;
  onToggleFavorite: () => void;
  /** Slideshow toggle. The composable also gates this
   * on items.length >= 2 (matches the Play button's
   * render gate). */
  onToggleSlideshow: () => void;
  /** Optional: if the user has reached the end of items
   * and there's a "more" page, the next() handler can
   * prefetch it. The Lightbox does this. The composable
   * doesn't need to know — next() is opaque to us. */
};

export function useKeyboardNav(opts: KeyboardNavOptions) {
  function isButtonTarget(e: KeyboardEvent): boolean {
    return e.target instanceof HTMLElement && e.target.tagName === 'BUTTON';
  }
  function isTextInputTarget(e: KeyboardEvent): boolean {
    if (!(e.target instanceof HTMLElement)) return false;
    const tag = e.target.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA';
  }

  function handleKey(e: KeyboardEvent) {
    // ArrowLeft/ArrowRight and Escape always run, regardless of
    // what's focused — they're navigation, not activation.
    if (e.key === 'Escape') {
      opts.onClose();
    } else if (e.key === 'ArrowLeft' || e.key.toLowerCase() === 'a') {
      opts.onPrev();
    } else if (e.key === 'ArrowRight' || e.key.toLowerCase() === 'd') {
      opts.onNext();
    } else if (e.key === 'Home') {
      // Jump to first photo. Same button-focus caveat as Space —
      // don't fight the browser's synthetic click on a focused
      // button.
      if (isButtonTarget(e)) return;
      if (opts.getItemsLength() > 0) {
        e.preventDefault();
        opts.setIdx(0);
      }
    } else if (e.key === 'End') {
      if (isButtonTarget(e)) return;
      if (opts.getItemsLength() > 0) {
        e.preventDefault();
        opts.setIdx(opts.getItemsLength() - 1);
      }
    } else if (e.key === 'PageUp') {
      // Skip back 10 — large-grid navigation. Bound to [0, N-1].
      if (isButtonTarget(e)) return;
      if (opts.getItemsLength() > 0) {
        e.preventDefault();
        opts.setIdx(Math.max(0, opts.getIdx() - 10));
      }
    } else if (e.key === 'PageDown') {
      if (isButtonTarget(e)) return;
      if (opts.getItemsLength() > 0) {
        e.preventDefault();
        opts.setIdx(Math.min(opts.getItemsLength() - 1, opts.getIdx() + 10));
      }
    } else if (e.key === 'f' || e.key === 'F') {
      // Like / Unlike shortcut. Disabled while typing in a
      // text field so we don't steal keys from the rename
      // input on the Add-to-album dialog, etc.
      if (isTextInputTarget(e)) return;
      e.preventDefault();
      opts.onToggleFavorite();
    } else if (e.key === 'd' || e.key === 'D') {
      // Wait — 'd' is already used for Next (alongside ArrowRight).
      // Reuse the same key, but only when Shift is NOT held —
      // Shift+D is a different gesture and shouldn't fire dislike.
      // Actually, looking at the existing handler: 'd' (no Shift)
      // calls next(). So we can't double-up on plain 'd' for
      // dislike without breaking the existing shortcut. Users
      // who want a keyboard shortcut for dislike can use the
      // right-click context menu or the on-screen button.
      // (Skip — see comment below.)
    } else if (e.key === ' ' || e.code === 'Space') {
      // Space natively activates focused buttons. If we also
      // fire toggleSlideshow() here, the synthetic click from
      // the button + our handler would net out to no-op and the
      // timer would never start. So skip our handler when a
      // button is focused and let the browser fire the click
      // naturally. ArrowLeft/Right don't have this issue since
      // browsers don't fire synthetic clicks for them on
      // buttons.
      if (isButtonTarget(e)) return;
      // Toggle the slideshow. Only meaningful when there's more
      // than one photo to cycle through (the same gate the
      // button uses to render itself). preventDefault stops
      // Space from also scrolling the page underneath the
      // lightbox — body overflow is hidden anyway, but
      // belt-and-braces against future layout changes.
      if (opts.getItemsLength() >= 2) {
        e.preventDefault();
        opts.onToggleSlideshow();
      }
    }
  }

  return { handleKey };
}
