/**
 * User preferences — persisted to localStorage so they survive
 * across sessions on the same browser. Per-device by design
 * (no backend round-trip; this is purely a UX knob).
 *
 * Currently one knob:
 *   - slideshowIntervalMs: how long each photo stays up during
 *     Lightbox auto-advance (Play button in the action bar).
 *     Default 3000 (3s).
 *
 * Add new knobs by extending `Preferences` + `defaults` and
 * providing a typed setter. Subscribers are notified on every
 * update; the Svelte auto-subscription syntax (`$preferences`)
 * gives components a reactive read.
 *
 * localStorage is best-effort: private-browsing mode, disabled
 * storage, quota exceeded, jsdom in Vitest, etc. all result in
 * `localStorage === undefined` and we silently fall back to
 * in-memory defaults so the UI keeps working. The persistence
 * is a nice-to-have, never a hard requirement.
 */
import { writable } from 'svelte/store';

const STORAGE_KEY = 'image-search:preferences';

export type SlideshowPreset = 3000 | 5000 | 10000 | 20000 | 30000;

export const SLIDESHOW_PRESETS: { label: string; ms: SlideshowPreset }[] = [
  { label: '3s', ms: 3000 },
  { label: '5s', ms: 5000 },
  { label: '10s', ms: 10000 },
  { label: '20s', ms: 20000 },
  { label: '30s', ms: 30000 }
];

export const DEFAULT_SLIDESHOW_MS: SlideshowPreset = 3000;

type Preferences = {
  slideshowIntervalMs: number;
};

const defaults: Preferences = {
  slideshowIntervalMs: DEFAULT_SLIDESHOW_MS
};

function load(): Preferences {
  if (typeof localStorage === 'undefined') return { ...defaults };
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...defaults };
    const parsed = JSON.parse(raw) as Partial<Preferences>;
    const candidate = parsed.slideshowIntervalMs;
    // Validate: must be a positive finite number. Anything else
    // (corrupted JSON, a user-tampered zero, NaN from a busted
    // write) falls back to the default so the slideshow can't
    // be misconfigured into "auto-advance every 0ms".
    if (
      typeof candidate === 'number' &&
      Number.isFinite(candidate) &&
      candidate > 0
    ) {
      return { slideshowIntervalMs: candidate };
    }
    return { ...defaults };
  } catch {
    return { ...defaults };
  }
}

function save(prefs: Preferences) {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // localStorage unavailable (private mode, quota) — in-memory
    // value still works for the current session.
  }
}

export const preferences = writable<Preferences>(load());

// Subscribe once at module load: every set call goes through
// localStorage automatically. The store's own initial value is
// either the loaded-from-storage value or the defaults, so the
// first subscriber sees the correct state on the same tick.
preferences.subscribe(save);

export function setSlideshowInterval(ms: number) {
  // Clamp to a sane range: 500ms–60s. Outside that we just keep
  // the current value rather than writing nonsense; the Settings
  // page only offers presets so this is a defence-in-depth check
  // for any future programmatic caller.
  if (!Number.isFinite(ms) || ms < 500 || ms > 60_000) return;
  preferences.update((p) => ({ ...p, slideshowIntervalMs: ms }));
}