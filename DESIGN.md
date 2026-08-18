# Design

The non-negotiable design rules for this app. If a screen, component, or
commit contradicts anything below, the contradiction is the bug.

The four principles live in [`PRINCIPLES.md`](./PRINCIPLES.md). This
document is the *implementation* of those principles: the named tokens,
the glass vocabulary, the spacing basis, and a worked example. Read
PRINCIPLES.md first; this file assumes them.

## 1. Tokens are the source of truth

Every visible value — colour, size, type, radius, opacity, shadow,
motion — is declared once in `search/static/css/tokens.css` and
referenced everywhere else as `var(--name)`. There are no raw `#hex`
codes, no `16px` literals, and no `1rem` magic numbers in any
component file or template. The only exceptions are:

- `1px` and `1.5px` in `border`, `outline`, and `transform` rules
  (these are intrinsic values, not layout values).
- Inline `width: 100%`, `max-width: 80rem` on page rails that
  compose multiple tokens into a container (and are themselves
  candidates for tokenization in a later phase).

If a value is referenced more than once across the codebase, it
belongs in a token. If it's used in exactly one place, inline it
at that call site (this is the **single-caller rule** — it prevents
the token vocabulary from drifting toward unbounded growth).

## 2. Token basis

The token vocabulary in `tokens.css` is anchored to three ratios:

- **Spacing** is built on `--u` (0.25rem / 4px). The four named
  stops are:
  - `--space-1` (~4-8px) — tight, gaps inside chips and small rows.
  - `--space-2` (~8-12px) — default, the "default padding" feel.
  - `--space-3` (~20-32px) — panel and page padding.
  - `--space-4` (~32-40px) — hero / section padding.
  Each stop is a `clamp()` over a fluid range. Components never write
  a padding in raw pixels.

- **Type** is a major-third ratio (1.250) anchored at 14.9px. Five
  named sizes:
  - `--text-xs` (0.72rem) — eyebrows, badges.
  - `--text-sm` (0.82rem) — captions, labels.
  - `--text-base` (0.93rem) — body.
  - `--text-lg` (1.1rem) — sub-headers.
  - `--text-xl` (1.35rem) — page h1.
  - `--text-hero` (`clamp(1.75rem, 2.5vw + 1rem, 2.75rem)`) — hero
    text. Fluid, ratio-anchored.

- **Container widths** are golden-ratio derived (φ ≈ 1.618).
  - `--container-narrow` (60rem) — primary content width.
  - `--container-wide` (97rem) — hero / wide layouts (narrow × φ).
  Components never write `max-width: 1200px`.

Other named tokens (radii, elevation, motion, z-index) follow
similar single-purpose rules; see `tokens.css` for the full list.

### When to use which spacing stop

- **Card or chip interior padding** → `--space-1` or `--space-2`.
- **Card-to-card gap in a grid** → `--space-3`.
- **Page padding on a single-page layout** → `--space-3`.
- **Hero section padding** → `--space-4`.
- **Section break (between major regions of a page)** → `--space-4`.

When none of these fit, the value is probably an outlier and
should be inlined at the call site, not promoted to a fifth stop.

## 3. Glass vocabulary

The app is dark-glass. There are **three** glass classes, each with
a single job:

| Class | Job | Surface | Radius | Shadow |
| --- | --- | --- | --- | --- |
| `.glass` | Default panel | 70% surface, blur 22px | radius-lg | shadow-2 |
| `.glass--sharp` | Hero panel (page's primary visual anchor) | 85% surface, blur 22px | radius-lg | shadow-3 |
| `.glass-pill` | Chip / pill / toggle | 70% surface, blur 14px | radius-pill | shadow-2 |

### Selection rule

Ask: "is this the primary visual anchor of the page?" If yes,
`.glass--sharp`. If it's a chip-shaped control, `.glass-pill`.
Everything else, `.glass`. There is no fourth class.

Historical classes (`.glass-frost`, `.glass-tinted`) were removed
in Phase B of the UI overhaul because:

- `.glass-frost` (95% opaque) was indistinguishable from `.glass`
  on a dark background — the opacity didn't add information.
- `.glass-tinted` pulled the dominant colour from the photo
  underneath, which directly violated Principle 4 ("single theme").

If a future use case seems to call for a fourth class, the answer
is "use one of the three correctly" — not "add a fourth class."

## 4. Elevation

A four-stop ladder named `--shadow-0` through `--shadow-3`. No
component writes `box-shadow` inline; they reference one of the
four named shadows. `0` is "no shadow" (sentinel for explicit
override; default elements that should not raise should not set
a shadow at all).

## 5. Dark, single theme

There is one palette. There is no `prefers-color-scheme: light`
branch. There is no `[data-theme="light"]` plumbing. The
`:root[data-theme="dark"]` block in `tokens.css` is the only theme
override; it exists for completeness but is not actively toggled
in the running app — the default values are already dark.

If a user-visible element should look different on light and dark,
the design is wrong: pick one. Currently everything is dark.

## Worked example: `/random`

`/random` is the simplest page in the app — a single page header, a
result grid, no other chrome. It is the canonical example of a page
that follows all four principles.

The page header (`search/templates/random.html`):

```jinja
{{ ui.page_header(
  "Random",
  eyebrow="Random",
  description=random_desc
) }}
```

- "Random" is the page title, rendered at `--text-xl`.
- The eyebrow appears at `--text-xs` with `tracking-eyebrow`.
- Spacing inside the header is `--space-3` (panel padding).
- The header itself uses `.glass` (default panel — not hero).

The result grid (`search/templates/_result_grid.html`):

```jinja
{% include "_result_grid.html" %}
```

- Tiles are photo cards. Each card uses `.glass`.
- The grid gap is `--space-3`.
- Tile internal padding is `--space-2`.
- No bespoke card styles. The `.photo-card` rule in
  `search/static/css/photo-card.css` is the single source.

What this page does **not** do:

- It does not define a custom colour, radius, or shadow.
- It does not use a one-off panel class.
- It does not introduce a new spacing value.
- It does not fork the chrome (header, footer) for the page.

That is the bar. Every other page in the app should look like this:
same tokens, same vocabulary, same composition. Variety comes from
content (the photos themselves), not from bespoke chrome.
