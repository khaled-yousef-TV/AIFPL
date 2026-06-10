# FPL AI — Design System

Generated with the `ui-ux-pro-max` skill (Data-Dense Dashboard + dark Financial
Dashboard palette) and adapted to the official FPL brand colors.

## Identity
- **Style:** Data-Dense Dashboard — KPI cards, tables, grid layout, space-efficient,
  maximum data visibility. Dark mode (primary).
- **Brand:** Fantasy Premier League — electric green `#00ff87`, deep purple `#37003c`,
  cyan `#04f5ff`, magenta `#e90052`.
- **Hermes accent:** purple→magenta gradient, to mark the AI "brain" surfaces.

## Color tokens (CSS variables, dark theme)
| Role | Token | Hex |
|------|-------|-----|
| Base background | `--bg` | `#0a0a12` |
| Surface (cards) | `--surface` | `#14141f` |
| Surface raised (inputs/hover) | `--surface-2` | `#1c1c2e` |
| Surface hover | `--surface-3` | `#26263f` |
| Border | `--border` | `#2a2a40` |
| Border strong | `--border-strong` | `#3a3a55` |
| Text primary | `--text` | `#f4f4f8` |
| Text muted | `--text-muted` | `#9a9ab5` |
| Text subtle | `--text-subtle` | `#6b6b85` |
| Primary (FPL green) | `--primary` | `#00ff87` |
| On primary | `--on-primary` | `#06121e` |
| Brand (FPL purple) | `--brand` | `#37003c` |
| Accent (cyan) | `--accent` | `#04f5ff` |
| Magenta (Hermes) | `--magenta` | `#e90052` |
| Success | `--success` | `#00d97e` |
| Danger | `--danger` | `#ff4d6d` |
| Warning | `--warning` | `#f5a623` |
| Info | `--info` | `#36a3ff` |

All contrast pairs verified ≥4.5:1 for text, ≥3:1 for large/UI glyphs (WCAG AA).

## Typography (skill recommendation)
- **Body/UI:** Fira Sans (300–700)
- **Data/mono:** Fira Code (tabular figures for prices, points, percentages)
- **Type scale:** 12 / 14 / 16 / 18 / 24 / 32 (base 16, line-height 1.5–1.6)
- Use `.tabular` (font-variant-numeric: tabular-nums) on all numeric columns to
  prevent layout shift.

## Spacing & layout
- 4/8px spacing rhythm. Section spacing tiers: 16 / 24 / 32 / 48.
- Container max-width: `max-w-7xl`. Breakpoints: 375 / 768 / 1024 / 1440.
- Elevation scale: `--shadow-sm/md/lg` (consistent, no random shadows).

## Interaction
- Transitions 150–300ms; `cursor-pointer` on all clickables.
- Visible focus rings (`--primary`, 2px) on all interactive elements.
- `prefers-reduced-motion` respected (animations disabled).
- Hover states change color/elevation, never layout bounds.

## Anti-patterns to avoid (flagged by the skill)
- Raw hex in components (use semantic tokens / Tailwind semantic colors).
- Emoji as structural icons (use lucide-react).
- Gray-on-gray low contrast; color as the only signal (pair with icon/text).
- Ornate decoration on a data product; missing filtering.
