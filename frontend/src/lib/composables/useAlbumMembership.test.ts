/**
 * Unit tests for useAlbumMembership.
 *
 * Covers:
 *   - optimistic add (set updated before API resolves)
 *   - optimistic remove (set updated before API resolves)
 *   - rollback on API failure
 *   - missing point id is a no-op
 *   - setAll replaces the membership
 *   - identity preservation: the rollback path restores
 *     the exact pre-call SvelteSet reference.
 */
import { describe, it, expect, vi } from 'vitest';
import { SvelteSet } from 'svelte/reactivity';
import { useAlbumMembership } from './useAlbumMembership';

function makeController(initial: number[], api: {
  onAdd?: ReturnType<typeof vi.fn>;
  onRemove?: ReturnType<typeof vi.fn>;
  pointId?: string | null;
} = {}) {
  let member: SvelteSet<number> = new SvelteSet(initial);
  const getMemberOf = () => member;
  const setMemberOf = (next: SvelteSet<number>) => {
    member = next;
  };
  const pointId = 'pointId' in api ? api.pointId : 'p1';
  const onAdd = api.onAdd ?? vi.fn().mockResolvedValue(undefined);
  const onRemove = api.onRemove ?? vi.fn().mockResolvedValue(undefined);
  const c = useAlbumMembership({
    getMemberOf,
    setMemberOf,
    getPointId: () => pointId,
    onAdd,
    onRemove
  });
  return { c, get current() { return member; }, onAdd, onRemove };
}

describe('useAlbumMembership', () => {
  it('adds to set optimistically', async () => {
    const m = makeController([], { onAdd: vi.fn().mockResolvedValue(undefined) });
    await m.c.toggle(7, 'Album 7');
    expect(m.current.has(7)).toBe(true);
    expect(m.onAdd).toHaveBeenCalledWith(7, 'p1');
  });

  it('removes from set optimistically', async () => {
    const m = makeController([7], { onRemove: vi.fn().mockResolvedValue(undefined) });
    await m.c.toggle(7, 'Album 7');
    expect(m.current.has(7)).toBe(false);
    expect(m.onRemove).toHaveBeenCalledWith(7, 'p1');
  });

  it('rolls back on API failure (add)', async () => {
    const m = makeController([], { onAdd: vi.fn().mockRejectedValue(new Error('boom')) });
    await m.c.toggle(7, 'Album 7');
    expect(m.current.has(7)).toBe(false);
  });

  it('rolls back on API failure (remove)', async () => {
    const before = new SvelteSet([7]);
    let member: SvelteSet<number> = before;
    const c = useAlbumMembership({
      getMemberOf: () => member,
      setMemberOf: (next) => {
        member = next;
      },
      getPointId: () => 'p1',
      onAdd: vi.fn().mockResolvedValue(undefined),
      onRemove: vi.fn().mockRejectedValue(new Error('boom'))
    });
    await c.toggle(7, 'Album 7');
    expect(member.has(7)).toBe(true);
    // Identity preserved on rollback.
    expect(member).toBe(before);
  });

  it('missing point id is a no-op', async () => {
    const m = makeController([], { pointId: null });
    await m.c.toggle(7, 'Album 7');
    expect(m.current.has(7)).toBe(false);
    expect(m.onAdd).not.toHaveBeenCalled();
  });

  it('setAll replaces membership', () => {
    const m = makeController([1, 2, 3]);
    m.c.setAll([9, 10]);
    expect(m.current.has(1)).toBe(false);
    expect(m.current.has(2)).toBe(false);
    expect(m.current.has(3)).toBe(false);
    expect(m.current.has(9)).toBe(true);
    expect(m.current.has(10)).toBe(true);
  });
});
