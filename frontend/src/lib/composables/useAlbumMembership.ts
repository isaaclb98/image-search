/**
 * useAlbumMembership — consolidate the add/remove photo
 * from-album mutation pattern that was duplicated across
 * Lightbox.svelte (toggleMembership) and
 * routes/photo/[id]/+page.svelte (addPhotoToAlbumAction).
 *
 * The shared shape:
 *   1. A SvelteSet<number> tracking which albums the current
 *      photo is a member of.
 *   2. Optimistic local flip: add or delete the albumId
 *      first, then call the API.
 *   3. On failure, restore the previous Set.
 *
 * Both call sites construct a new SvelteSet on every change
 * (rather than mutating in place) so Svelte's reactivity
 * tracks the new reference. The composable preserves that
 * convention so call-site subscribers keep working.
 *
 * The composable is callback-driven: each call site owns
 * its own `memberOf` SvelteSet (because the Lightbox and
 * the photo page each have a separate one with their own
 * lifecycles). The composable just owns the policy.
 */
import { SvelteSet } from 'svelte/reactivity';

export type AlbumMembershipOptions = {
  /** Current membership set. Read once per call; not
   * subscribed to (the set is already reactive at the
   * call-site level). */
  getMemberOf: () => SvelteSet<number>;
  /** Replace the membership set. The composable always
   * passes a fresh SvelteSet copy so Svelte's reference-
   * equality reactivity works. */
  setMemberOf: (next: SvelteSet<number>) => void;
  /** The photo id that all add/remove calls target. */
  getPointId: () => string | null | undefined;
  onAdd: (albumId: number, pointId: string) => Promise<unknown>;
  onRemove: (albumId: number, pointId: string) => Promise<unknown>;
};

export type AlbumMembershipController = {
  /** Toggle membership for the current photo. No-op if
   * the point id is missing. */
  toggle: (albumId: number, albumName: string) => Promise<void>;
  /** Replace the membership set wholesale. Used by the
   * `refreshMembership` flow after the page loads the
   * actual album-membership list from the server. */
  setAll: (albumIds: Iterable<number>) => void;
};

export function useAlbumMembership(
  opts: AlbumMembershipOptions
): AlbumMembershipController {
  return {
    async toggle(albumId: number, _albumName: string) {
      const pointId = opts.getPointId();
      if (!pointId) return;
      const current = opts.getMemberOf();
      const inThisAlbum = current.has(albumId);
      // Capture the prior reference so a rollback can
      // restore the exact same SvelteSet identity (any
      // subscriber using === will see no change rather
      // than a "revert" change).
      const before = current;
      const next = new SvelteSet(current);
      if (inThisAlbum) next.delete(albumId);
      else next.add(albumId);
      opts.setMemberOf(next);
      try {
        if (inThisAlbum) await opts.onRemove(albumId, pointId);
        else await opts.onAdd(albumId, pointId);
      } catch {
        opts.setMemberOf(before);
      }
    },
    setAll(albumIds: Iterable<number>) {
      opts.setMemberOf(new SvelteSet(albumIds));
    }
  };
}
