// Vitest setup. Mock matchMedia for jsdom (some components check it)
// and any other test-time globals. Keep this small.

if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false
  });
}

// Vitest 2.x with jsdom does NOT hoist `localStorage` to the
// global scope — it's only on `window.localStorage`. Real
// browsers expose it as a global, so application code reads
// `localStorage.foo` without `window.` and would crash in tests
// without this hoist. Mirror jsdom's storage onto globalThis so
// the preferences store and any other browser-API code work
// under test without touching the window prefix.
if (typeof window !== 'undefined' && window.localStorage && typeof localStorage === 'undefined') {
  globalThis.localStorage = window.localStorage;
}
if (typeof window !== 'undefined' && window.sessionStorage && typeof sessionStorage === 'undefined') {
  globalThis.sessionStorage = window.sessionStorage;
}
