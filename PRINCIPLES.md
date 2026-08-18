# Principles

The non-negotiable design rules for this app. Every commit, page, component,
and CSS class either satisfies these or it doesn't ship.

## 1. Consistency everywhere

The same rules apply to every page, every component, every interaction.
There are no per-page exceptions. If two screens need to look the same,
they use the same vocabulary. If a screen wants to look different,
it does so through *content*, not through bespoke chrome.

Consistency is a result, not a rule — it's the test that the other three
principles are working. When consistency fails, one of rules 2, 3, or 4
is being violated; find which.

## 2. Modular components, no bespoke elements

Every visible element is a named, reusable component. If something only
appears on one page, it is either:

- **promoted** to a component used in two or more places, or
- **inlined** as plain content of an existing component (e.g., a page
  section, a macro slot), with its styles drawn from existing tokens.

Anything in between — a one-off class, a one-off variable, a one-off
helper — is bespoke by definition and is forbidden. Single-use CSS
classes, single-use tokens, page-specific JS modules that don't share
helpers: all of these are symptoms of bespoke code and should be
removed or generalized.

This includes spacing, type, radii, elevation, and motion. Each is
a named thing used by many things. A page doesn't redefine a button —
it composes one.

## 3. No hard-coded values; ratios, not memory

Every visible value derives from a mathematical basis. The basis is
declared once and referenced everywhere else as a `var(--name)` or a
function of a constant.

- **Spacing** flows from a single unit (`--u`) at one of a small number
  of stops. No `padding: 12px` in a component; if 12 is the right
  answer, 12 is a token.
- **Type** flows from a fixed ratio (major third, 1.250). No literal
  font sizes in components. Hero text may use `clamp()` over a token
  range.
- **Radii** flow from the element's size class. Two or three named
  radius tokens, used everywhere.
- **Container widths** use the golden ratio (φ ≈ 1.618). Two named
  breakpoints: a narrow content width and a wide content width (the
  wide width is the narrow width × φ).
- **Elevation** is a small ladder of named shadow tokens. No inline
  `box-shadow`.

Math > memory. The rule isn't "use a ratio"; it's "any value a
component might want to use is named." Naming the ratio is how we keep
the rule enforceable.

## 4. Frosted glass over user photos

The image is the page's colour source; the glass is what sits on
top of it. Every surface is a frosted panel over a photo-tinted
ground — clean, minimalist, sleek. Variety comes from the user's
images, not from chrome.

---

## How these four interact

Rules 2 and 3 do the work; rule 1 is the success criterion; rule 4 is
the aesthetic commitment. A commit that satisfies all four looks the
same product on every page, uses named components and tokens
exclusively, sits frosted glass over a user-photo tint, and nothing
bespoke. A commit that violates any one of them is a regression.
