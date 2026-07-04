# vega closet — UI spec for redesign

You are helping redesign a mobile-first personal shopping app called **vega closet**. It is a daily
menswear feed: an agent scrapes Grailed, The RealReal and SSENSE, filters everything to the owner's
exact sizes, rates each piece against a learned visual taste model, and presents a scrollable feed
the owner trains by tapping love / cart / pass. It runs as an iOS home-screen web app (single page,
6 core screens/states). Design frame by frame; every element, its home, its copy and its behavior
are listed below.

## brand + tokens (current implementation — evolve, don't ignore)

- Typeface: **Plus Jakarta Sans** (300–700). ALL text renders lowercase (`text-transform:lowercase`).
- Palette: ink `#0a0a0a` background with two radial glows (blue `#0077CC` top-left, cyan `#44BBDD` top-right);
  text `#f4f6f8`; muted `rgba(244,246,248,0.6)`; hairlines `rgba(255,255,255,0.16)`;
  glass fills `rgba(255,255,255,0.07)` / `0.12`; accent cyan `#44BBDD`; gold `#E8C24A`
  (gold = warnings AND the cart identity); sand `#E8E4DC` (rarely used).
- Shape language: rounded-rectangle cards (18px), pill chips (999px), 10–14px radii on buttons/inputs.
- Motion: one easing everywhere `400ms cubic-bezier(0.2,0.7,0.2,1)`.
- Glass/blur is used ONLY on small chrome (sheet, bottom bar, note). Cards must NOT use backdrop blur
  (thousands of cards = iOS crash). Keep that constraint in the redesign.
- Feels: quiet-luxury dark, minimal, everything lowercase, data-dense but calm.

## screen 1 — sign in

Centered glass card on the glowing dark background.
- Brand row: gradient square logo mark + "vega closet" (19px semibold) + tag "your feed" (11.5px muted).
- Lead copy: `sign in to see today's picks.`
- Email input (placeholder `you@email.com`), password input (placeholder `password`) — identical styling.
- Primary button: `sign in` (blue→cyan gradient, dark text, soft glow).
- Text link below: `forgot password?` (muted, underlined).
- Success/info copy in green `#7ee0b8`, e.g. `reset link sent — check your email and open it on this device.`
- Error copy in gold, lowercase, e.g. `enter your email and password.`

## screen 1b — set new password (reset landing)

Same card, swapped body:
- Lead: `pick a new password for your closet.`
- One password input (placeholder `new password (8+ characters)`), button `save new password`,
  error line `use at least 8 characters.` On save it drops straight into the feed.

## screen 2 — the feed (home)

Top to bottom:
1. **Header**: brand row (logo, "vega closet", subtitle `your feed · 2026-07-03`) left; stat block right,
   right-aligned, 12.5px: `364 shown of 4839 tracked` + `feed updated 2h ago` (numbers in cyan bold).
2. **Note** (dismissible context strip): glass card, cyan left border, clamped to ONE line with ellipsis,
   tap to expand. Copy pattern: `364 new to review, 1035 liked, 3357 archived. one filter bar: show
   (feed / liked / archived / all) stacks with category, size, price and source. love a piece and it
   moves to liked; pass it to archived.`
3. **Show row** (always visible): label `show` + pill chips `feed 364` `liked 1040` `cart 4` `archived 3357`
   `all 4839` (count inside chip, 65% opacity) + a pinned `filters ⌄` chip (sticky at right edge while the
   row scrolls horizontally; shows active count like `filters · 2 ⌄` when filters are applied).
4. **Expanded filters** (only when toggled): one horizontally-scrollable row per dimension, each with a
   tiny muted label — `category` (all/outerwear/tops/pants/shoes/accessories), `size` (all/L/XL/XXL/w 36/
   w 38/us 14/us 15/eu 46-48/one size), `price` (all/under $200/$200–500/$500–1k/$1k+), `source`
   (all/grailed/the realreal/ssense), `sort` (relevance/newest action/price ↑/price ↓). Chips with zero
   results hide themselves. Active chip = solid cyan with dark text.
5. **Search row**: rounded search input (placeholder `search brand, title, style tag…`, ✕ clear button
   appears when typing) + square **view toggle** button to its right (⊞ = switch to compact grid,
   ▤ = switch to full cards). Search filters live as you type (debounced).
6. **Cart summary strip** (cart tab only — see screen 3).
7. **Sections**: `outerwear` / `bottoms` / `tops + knits` / `footwear` / `accessories`, each with big
   section title (23px), cyan count, muted subtitle (e.g. outerwear: `leather, denim, shearling and
   tech, ranked`; footwear: `in-size only, us 14-15 / eu 47-48 (+ balenciaga 46)`).
8. **Empty state** (centered muted text): `you're all caught up. tap scan now for fresh pieces, or 'all'
   to browse everything you've saved.` Search variant: `no results for "x". try a different search.`
9. **Footer**: `vega closet · prices and availability change, tap through to confirm on the platform ·
   love and pass train your next run.`
10. **Jump-to-top**: circular ↑ button, fixed bottom-right above the bar, fades in after scrolling.

## card anatomy (full mode)

- Image (aspect ~1:1.12) with optional badges: `new` (cyan chip, top-left) or `35% off`; optional gold
  flag strip along the image bottom for warnings (e.g. `confirm size`).
- Body: brand + source eyebrow (11px cyan, e.g. `rick owens · grailed`), title (14.5px), price row
  (`$495` bold + struck original + green `% off`), pill row (`size xl` `gently used` `outerwear`),
  reason tags row (tiny cyan-tinted chips from the taste model, e.g. `leather` `boxy-volume` `black`;
  raw-scanned items show `unrated`).
- Action row: three equal buttons `♥ love` `🛒 cart` `✕ pass`.
  - loved state: cyan ring around card, love button fills cyan.
  - carted state: gold ring, cart button fills gold.
  - passed state: card dims to 42%, pass button fills gold. Toggle logic: tapping the active state
    undoes it; tapping cart on a carted item downgrades it back to loved.
- Bottom CTA: full-width gradient link `view on grailed` (opens the listing; in the home-screen app it
  navigates in place and the app restores your spot when you come back).

## card anatomy (compact grid mode)

Two-plus columns. Keeps: image, brand eyebrow, one-line title, price, **size pill**, the three action
buttons as icons only (♥ 🛒 ✕). Hides: condition/category pills, reason tags, view link (tapping the
photo opens the listing instead). This is the fast-review mode.

## screen 3 — cart tab

Same feed layout filtered to carted items, plus a **cart summary strip** above the sections:
gold-bordered glass card reading e.g. `grailed: 3 · $740 · the realreal: 1 · $395 — total $1,135`
(counts and total in gold bold). This is a cross-marketplace wishlist of true purchase candidates —
stronger than a love. Design should make totals feel like a receipt-in-progress.

## screen 4 — scan sheet (bottom sheet over dim overlay)

Slides up, dark solid glass, 20px top radii. Title: `filter scan` (17px semibold).
Rows, each with a tiny uppercase muted label:
- `sources` — multi-select pills: grailed / the realreal / ssense (all on by default).
- `category` — single-select pills: all / outerwear / tops / bottoms / footwear / accessories.
- `keyword · style · color (grailed title search)` — text input, placeholder `e.g. black leather, cargo, double knee…`
- `specific brands (blank = your full loved list)` — text input, placeholder `rick owens, prada, maison margiela…`
  (partial names auto-resolve, e.g. "bottega" → bottega veneta).
- `new pieces to bring back (exact — keeps your best-scored)` — number input, default 50, max 200.
- `taste rating` — one toggle pill `rate against taste` (on by default). Helper copy below:
  `turn off for a raw search: everything found shows up unrated and sorted by newest — nothing gets
  buried by the taste model. faster, too.`
- Standing helper: `sizes are always locked to yours — every scan only pulls pieces that fit.`
- Footer buttons: primary gradient `start scan` + ghost `close`.

## screen 5 — bottom bar (persistent, all feed states)

Solid dark blurred bar, hairline top border, single row:
- Left: signal counts `♥ 1040 · 🛒 4 · ✕ 3412` (numbers cyan).
- Right: `scan all` button, `⊕` (opens the scan sheet), `⋯` menu (popover above the bar with
  `hide passed`, `clear feed`, `sign out`). `clear feed` uses a two-tap confirm: first tap turns the
  button into `sure? tap again` for ~3.5s.
- Status line above the row (green, only when present), e.g.:
  `scanning grailed for fresh picks… (~2-3 min)` / `raw-scanning grailed — unrated, newest first… (~1-2 min)` /
  `fresh picks in — refreshing` / `scan didn't start (github said 401) — token or workflow issue` /
  `stopped waiting — the scan finishes on its own, feed updates when done` /
  `cleared 152 pieces — they can return on future scans`.
- During a scan: 3px progress bar under the row (fills by realistic time estimate), `scan all` reads
  `scanning…` disabled, and a gold-outline `cancel scan` button appears. Scans survive closing the
  app: reopening resumes the progress state.

## screen 6 — states worth designing deliberately

- Scan-in-progress feed (bar + progress + status line).
- Post-clear empty feed.
- "no feed yet" first-run: note reads `no feed yet — run a haul and your picks will show up here.`
- Save failure toast: `couldn't save — try again`.
- PWA launch (standalone, black-translucent status bar, app title `vega closet`, icon = V mark on white).

## interaction principles already in place (keep)

- Every decision is one tap, optimistic UI, syncs live across devices.
- Taps are training data: love/cart teach taste; pass teaches avoid; a feed "clear" teaches nothing.
- The feed is cumulative; filters/search/sort/scroll all survive a refresh after scans.
- Products above the fold: chrome collapses (one-line note, one filter row) before content ever does.
