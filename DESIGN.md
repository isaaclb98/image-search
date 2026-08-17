# Design

This app is built on a small number of opinionated design principles. They exist
to keep the codebase honest as it grows: when every new screen feels like the same
screen, viewers trust the product and reviewers stop noticing the chrome.

## Principles

### 1. Tokens are the source of truth

Every visible value — colour, size, type, radius, opacity, motion — is defined
once in `search/static/css/tokens.css` and referenced everywhere else as a
`var(--name)`. There are no raw `#hex` codes, `16px` literals, or `1rem` magic
numbers in any other file. If a value needs to exist, it lives in a token first.

**Why:** a single source is the only way to keep a design coherent as more
authors touch the codebase. Tier 3 was a forced walk through the entire CSS
because earlier files had drifted — every page had its own spacing scale, its
own opacity value, its own opinion about what "subtle" meant. The fix is to
make drift impossible.

**How to use:** if you need a colour, find it in `tokens.css`. If it isn't there,
add it. If you find yourself reaching for a raw value, stop — you are creating
the next drift.

### 2. Glass is a primitive, not a vibe

The app is "glassmorphic," but `glass` is a defined CSS class with documented
variants. There are five:

| Class | Surface | Use |
| --- | --- | --- |
| `.glass` | 50% surface, blur `var(--blur-glass)` | Default panel |
| `.glass--sharp` | 65% surface, radius-xl | Hero panels (Discover header, Album detail header) |
| `.glass-frost` | 92% opaque surface | Text-heavy panels where backdrop blur hurts readability |
| `.glass-pill` | 50% surface, pill radius, blur `var(--blur-pill)` | Chip-like controls (chips, view toggle, suggestions) |
| `.glass-tinted` | Photo-dominant radial gradient | Cards whose tint comes from the photo they contain |

**Why:** "glass design" quickly becomes a soup of half-transparent rectangles
when every panel invents its own opacity. Five primitives, used consistently,
read as a *system* — that's the difference between "professional" and "demo."

**How to use:** reach for one of the five. If none of them fits, you probably
want a different problem solved by a different layout, not a sixth glass
variant.

### 3. Token-driven type

Type lives at three scale steps:

- **Eyebrow / micro labels** (`--text-xs`, `--weight-semibold`, `--tracking-eyebrow`, uppercase) — page labels, section headings, "DISCOVERY" over "Find your taste"
- **Body** (`--text-sm` / `--text-base`, `--weight-regular`, `--leading-normal`) — most UI text
- **Title** (`--text-xl` / `--text-2xl`, `--weight-bold`, `--tracking-tight`) — page titles, panel headers

DaisyUI's `.text-sm`, `.text-base`, `.text-xl`, `.font-semibold`, `.tracking-tight`
utilities are **overridden** in `layout.css` to flow through these tokens. The
override means every existing template that uses `.text-sm` automatically picks
up the new scale — no template edits required.

**Why:** type hierarchy is the most-noticed, least-articulated quality signal.
When two adjacent panels use slightly different sizes for the same role, the
brain registers "amateur." When they use the same token, it registers "system."

### 4. Buttons share one shape vocabulary

Every actionable surface — primary button, secondary button, view toggle, surprise
me, diversity dropdown, search submit — uses the same `min-height: 2.5rem`, the
same pill radius (`--radius-pill`), the same hairline border, the same hover
lift. The only thing that varies is *which* surface token they use for tinting
(`--photo-dominant` for primary, transparent for inactive, `--surface-glass`
for inputs).

**Why:** the eye locks on uniform heights and shared shapes long before it
reads labels. Mix heights and the toolbar looks like a yard sale.

### 5. Layout files use sections, not drifts

`layout.css` is organised into 28 numbered sections. Each section has a header
comment explaining what it owns. New styling belongs in the section that owns
its domain:

```
/* ============================================================
   1. AMBIENT BACKGROUND + RESET
   ============================================================ */
/* ============================================================
   2. HEADER
   ============================================================ */
/* ...etc...
```

**Why:** without section headers, CSS files become a write-only pile. You find
a rule by `grep` and you have no idea what other rules are competing with it.
Numbered sections turn the file into a navigable map.

### 6. Re-skin by overriding the token block

Both `light` and `dark` themes live in `tokens.css` as two `:root[data-theme=…]`
selector blocks. To re-skin a feature, change the tokens, not the consumers.

**Why:** the only way to keep a dark mode that actually feels intentional is to
make it impossible to theme a component without going through the token system.
If a consumer uses `hsl(220 50% 50%)` directly, dark mode will silently break.

### 7. Pages opt-in to the shell

Every page has a consistent chrome:

- `.app-page` — outer wrapper with the page rail width
- `.app-page-header` — glass page header (title + count) — both `.app-page-header` and `.page-header` map to the same panel
- `.app-toolbar` — glass toolbar above content
- `.app-empty-state` — glass empty-state card

Pages still own their interior structure; the shell is the part that always
reads the same.

## File map

```
search/static/css/
  tokens.css        Single source of truth. Palette, glass, type, spacing,
                    radii, motion, themes. ~280 lines.
  glass.css         Glass primitives (.glass, .glass--sharp, .glass-frost,
                    .glass-pill, .glass-tinted) + button family (.btn,
                    .btn-primary, .btn-ghost, .btn-xs, .btn-circle).
                    ~250 lines.
  layout.css        App shell + every page's component. 28 numbered sections.
                    ~1500 lines.
  photo-card.css    Photo grid/feed cards. Score badges + favourite icon.
                    ~150 lines.
```

Load order in `base.html`: `app.css` → `tokens.css` → `glass.css` →
`layout.css` → `photo-card.css`. Components are layered so later files win
ties on specificity, which lets us override third-party classes (DaisyUI,
Tailwind) without `!important`.

## Do / don't

**✓ DO** use a token for any value that appears more than once.

```css
/* Good */
padding: var(--space-3) var(--space-4);
border-radius: var(--radius-lg);
background: hsl(var(--surface-glass) / 65%);

/* Bad — creates drift */
padding: 12px 16px;
border-radius: 14px;
background: hsl(220 25% 85% / 0.65);
```

**✓ DO** put a new colour in `tokens.css` before using it.

```css
/* tokens.css */
:root[data-theme="light"] {
  --accent-cool: 220 70% 50%;
}

/* consumer */
color: hsl(var(--accent-cool));
```

**✗ DON'T** invent a sixth glass variant because the five don't fit.

The five exist because the design has exactly five legitimate roles for
translucent surfaces. If you need something different, the question is whether
your component is really a *glass* panel, or whether it should be a different
kind of surface entirely (a tooltip, a callout, a toast).

**✗ DON'T** write a new shadow. Use `--shadow-glass`, `--shadow-glass-strong`,
or `--shadow-glass-hover`. If you need a fifth tier, add it to `tokens.css`
first.

**✗ DON'T** set padding/spacing/radius in pixels. Use `--space-*` and `--radius-*`.

## When the principles conflict

They don't, by design. If two principles seem to fight, the right move is
usually to add a new token that resolves both:

- "I need a darker shadow than `--shadow-glass-strong`" → add `--shadow-glass-heavy` to `tokens.css`.
- "I need a different glass tint for this one panel" → add a token in
  `tokens.css` and use it.
- "I need a non-pill button for this one action" → ask whether the action
  really is a button or whether it's a link or a tag.

The architecture is a funnel: real design needs come in; tokens come out.
