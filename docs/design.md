# Atlas design system

Atlas is the design direction for the app: bright, spatial, modern. One calm
teal is the colour of the product; one warm tangerine is reserved for "this
needs attention" (hot matches, closing soon). Neutrals are cool-tinted, never
pure grey. Containers get large soft radii, controls small ones. Motion is
short and purposeful.

The tokens live in `web/app/globals.css` on `:root` (light values), with dark
overrides in two blocks: `@media (prefers-color-scheme: dark)` guarding
`:root:not([data-theme="light"])` (system preference), plus
`:root[data-theme="dark"]` (explicit pin, wins regardless of the OS setting).
Every variable gets its first definition on bare `:root`. `body` sets
`background: var(--bg)`.

The old v1 variables (`--background`, `--foreground`, `--card`, ...) are kept
next to the new ones so existing v1 class usage keeps working. One exception:
Atlas redefines `--accent` as the product teal, superseding the old v1 violet.

## Tokens

| token | light | dark | use |
|---|---|---|---|
| `--bg` | `#F2F6F6` | `#0B1516` | page background |
| `--surface` | `#FFFFFF` | `#112022` | cards, tiles |
| `--surface-2` | `#E7EEEE` | `#173033` | tracks, quiet fills |
| `--line` | `#D5E0E0` | `#21403F` | borders |
| `--fg` | `#0E1A1B` | `#E6F2F1` | body text |
| `--fg-2` | `#3D5557` | `#A5C4C2` | secondary text |
| `--fg-3` | `#5B7274` | `#86A8A6` | tertiary text |
| `--ink` | `#08292A` | `#CFEDEA` | headings |
| `--accent` (teal) | `#0B7A78` | `#4FD1C5` | the product colour |
| `--accent-fg` | `#FFFFFF` | `#06201F` | text on accent fills |
| `--accent-soft` | `#D8F0EE` | `#14403D` | soft accent fills |
| `--hot` (tangerine) | `#E8590C` | `#FF9A5C` | needs attention only |
| `--hot-soft` | `#FFE8DA` | `#3A2415` | soft hot fills |
| `--good` | `#1B7A4B` | `#63D394` | success, separate from teal |
| `--warn` | `#9A5600` | `#F0B04A` | warning |
| `--bad` | `#B3261E` | `#FF8C84` | error |

Semantic status colours (`good`/`warn`/`bad`) are separate from the teal
accent — never use the accent to mean success.

Colour contrast: the table values were chosen to pass 4.5:1 for body text on
their surfaces. If you must change one, measure the ratio and say so in your
report.

## Fonts

Loaded via `next/font/google` in `web/app/layout.tsx` (no external stylesheet
link) and exposed as CSS variables on `<html>`:

| variable | font | weights | use |
|---|---|---|---|
| `--font-display` | Bricolage Grotesque | 700, 800 | logo, titles, big numbers |
| `--font-body` | Hanken Grotesk | 400, 500, 600 | body text and UI |
| `--font-dm-mono` | DM Mono | 400, 500 | scores, pay, dates, small labels |

Body text is 14px / 1.5; numbers use `font-variant-numeric: tabular-nums` so
columns of figures don't jitter. Type scale: 12, 14, 16, 20, 24, 32.
Uppercase mono labels are 11px with 0.08em letter-spacing.

## Radii

| variable | value | use |
|---|---|---|
| `--r-sm` | 8px | inputs, buttons |
| `--r-md` | 14px | chip groups, stat tiles |
| `--r-lg` | 18px | cards |
| `--r-xl` | 22px | drawer |

## Motion

Durations: 120ms press (`--dur-press`), 150ms toggle and hover
(`--dur-toggle`, `--dur-hover`), 200ms drawer (`--dur-drawer`). Easing is
`cubic-bezier(.2,.7,.2,1)` (`--ease`). No animation may run longer than 300ms.
`prefers-reduced-motion` disables all transitions and animations.

## Rules

- **One primary action per view.** Each screen has exactly one visually primary
  action (one filled accent button). Everything else is secondary or ghost.
- **Chips and tags are never interactive.** `Chip`, `SkillTag` and
  `ProvenanceChip` render `<span>`s: no hover style, no pointer cursor, no
  button role. If it looks tappable, it must be a real control
  (`FilterToggle` uses a real `<button aria-pressed>`).
- **Every extracted value shows a provenance chip.** Any value pulled out of a
  posting (skills, salary, location, ...) is accompanied by a `ProvenanceChip`
  naming its source, so the reader always knows how much to trust it.

## Components (`web/components/atlas/`)

All are client-safe (no data fetching) and each has a test in
`web/tests/atlas/` plus joint coverage in `showcase.test.tsx`.

- `MatchDial` (`score: number`, `size?: number = 54`) — SVG arc; track
  `--surface-2`, arc `--accent` (`--hot` at 85+); display-font number in the
  middle; `role="img"`, `aria-label="Match score N out of 100"`. Clamps 0..100,
  rounds for display.
- `StatTile` (`value: string | number`, `label: string`,
  `tone?: "default" | "hot"`) — big display-font number plus small label; `hot`
  colours the number `--hot`.
- `FilterToggle` (`label`, `pressed`, `onPressedChange`) — real
  `<button aria-pressed>`; thumb travels 14px in 150ms; label is the accessible
  name.
- `Chip` (`tone?: "default" | "ok" | "hot"`, children) — small pill; `ok` uses
  `--accent-soft`/`--accent`, `hot` uses `--hot-soft`/`--hot` at weight 600.
- `SkillTag` (`skill: string`, `matched?: boolean`) — mono 11px; matched fills
  with `--accent`.
- `ProvenanceChip` (`source: "jsonld" | "source" | "rule" | "llm" | "manual" |
  "unknown"`) — tiny outlined mono tag; `title` explains it in plain words.
- `ScoreBar` (`label`, `points`, `max`) — label, bar with an
  `--accent`-to-`--hot` gradient fill at `points/max` width (clamped;
  `max = 0` renders empty), `points/max` in mono.

> Note: the mono font variable is `--font-dm-mono`, not `--font-mono`: the older pages already use `--font-mono` for Geist Mono, and two definitions of one variable on the `<html>` element would make the winner depend on stylesheet order.
