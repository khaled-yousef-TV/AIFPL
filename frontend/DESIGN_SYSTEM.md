# Hermes FPL — Design System

**"The Matchday Programme."** Ink on paper, FPL purple as flood colour, one
orange signal accent. The product's output is a *written verdict* backed by
seven agents, so the page is laid out like printed sports journalism: a
masthead, a poster hero carrying the verdict, and a two-column plate below it.

Replaced the previous dark/neon "Data-Dense Dashboard" system in full.

## Identity

- **Style:** printed matchday programme — poster typography, flat colour
  blocks, hairline rules. No gradients, no glow, no shadows, no blur.
- **Brand:** Fantasy Premier League purple `#37003c`, used as a flood colour
  (masthead, pitch, primary button) rather than as an outline.
- **Accent:** a single orange `#d9480f` for the captain, the active nav item,
  the Hermes action, and focus rings. If something is orange, it is *the*
  thing to look at.

## Colour tokens

| Role | Token | Hex |
|------|-------|-----|
| Paper (page) | `--paper` | `#ede7da` |
| Paper inset (fields, callouts) | `--paper-2` | `#f4f0e7` |
| Paper pressed / hover | `--paper-3` | `#e3dccd` |
| Rule | `--rule` | `#cdc5b4` |
| Rule soft (bar tracks) | `--rule-soft` | `#ded7c7` |
| Rule strong (section rules, borders) | `--rule-strong` | `#131313` |
| Ink | `--ink` | `#131313` |
| Ink muted | `--ink-muted` | `#6a6353` |
| Ink subtle | `--ink-subtle` | `#8a8474` |
| Brand (FPL purple) | `--purple` | `#37003c` |
| Brand hover | `--purple-2` | `#4b0a52` |
| Accent | `--orange` | `#d9480f` |
| Cream (on purple) | `--cream` | `#ede7da` |
| Boosted | `--up` | `#1f7a3f` |
| Faded | `--down` | `#b3271e` |
| Warning | `--warn` | `#a35a06` |

Tailwind semantic names (`bg`, `surface`, `border`, `content`, `primary`,
`accent`, `success`, `danger`, …) map onto these variables in
`tailwind.config.js`. Components use the semantic names, never raw hex.

## Typography

- **Everything:** Archivo Variable (weights 100–900, **width axis 62–125%**).
  The width axis is the point — the poster headline is `font-stretch: 70%`
  at weight 900, which no static font could do without a second family.
- **Numbers:** JetBrains Mono 400/700, `font-variant-numeric: tabular-nums`
  on every figure (prices, points, percentages, the countdown).
- Both are **self-hosted via `@fontsource`** and imported from `src/index.css`.
  There is no font CDN request — an e2e test enforces this.
- Headline sizes step down with verdict length (`.sz-lg` / `.sz-md` / `.sz-sm`,
  all `clamp()`-based) so a long verdict never blows out the hero.

## Layout

- **Masthead** (purple band) → **hero** (verdict + projected points) →
  **numbered run-type nav** (`01`–`07`) → **run controls** → **plate**.
- The plate is `1.55fr / 1fr` on `lg`, single column below, split by a rule
  rather than by cards.
- Section headings are `.sec-h`: 0.65rem caps, `0.2em` tracking, 2px ink rule
  underneath. They replace card headers — the programme has sections, not cards.
- Page padding `1rem` / `1.625rem` at `sm`. Breakpoints 375 / 640 / 900 / 1024.

## The pitch

The squad stays on a field, drawn in the programme's language: purple flood,
cream touchlines / centre circle / penalty area, 2px ink frame.

- Shirt numbers (`1`–`11`, top-right of each dot) preserve the teamsheet reading.
- Captain and vice are **armbands** (`C` / `V`, top-left) — not name suffixes,
  which truncated inside the dot column at mobile widths.
- `▲` / `▼` under a player marks a Hermes adjustment; the legend sits under
  the bench row.

## Interaction & performance

The old system's cost centres are deliberately gone:

- No `@import` of a font CDN (was render-blocking in CSS).
- No `body::before` full-viewport radial gradients.
- No `drop-shadow` glow on the countdown, and no `animate-pulse` ticking once
  a second. The countdown is **one mono string** in the masthead.
- No `box-shadow` anywhere — `boxShadow.elev-*` are mapped to `none`.
- Transitions are 120ms (`--t`) and only on colour, border, opacity, transform.
- Spinners remain only where something is genuinely in flight.
- `prefers-reduced-motion` disables animation and transitions.

## Anti-patterns

- Raw hex in components — use the semantic tokens.
- Emoji as structural icons — use `lucide-react`.
- Colour as the only signal — pair with a glyph or text (`▲`, `OK`, `DEGR`).
- Adding elevation, gradients or rounded "card" chrome. This system is printed:
  things are separated by rules and flat fills, not by floating above the page.
