# TRM Onboarding — Flow Architecture

**Spine of the design:** the signup gate goes AFTER the fun, sizes are the only form-like moment and last 25 seconds, and the four upload buckets are repositioned as the thing you do *while your first feed builds* — so there is zero dead waiting and zero "task" framing. Progress is never "step 3 of 9"; it's a **TASTE SIGNAL meter** that fills as the model locks on.

Total: **~4 min to first feed, only ~2.5 min of active input.**

---

## The one persistent chrome element

Every onboarding screen shares a thin header strip (IBM Plex Mono, uppercase, letterspaced):

```
TRM          [ MODEL INTAKE ]          SIGNAL: ▓▓░░░░ FAINT
```

The meter is a segmented acid (#DEF65A) bar that fills through named states — `BLIND → FAINT → FORMING → SET`. It advances on *signal collected*, not screens completed. This is the entire progress system. No pips, no step counters, no "3 of 9." The user experiences progress as *the model getting smarter about them*, which is literally true. Skip affordances everywhere are small mono text bottom-left (`skip calibration →`), never styled as buttons — visible if hunted for, invisible otherwise.

---

## Screen 0 — Cover (5 sec)

Paper background, 43px grid, skewed Minecraft `TRM` logo large. One serif line below, lowercase: *"a taste model that shops for you."* One mono sub-line: `GRAILED · THE REALREAL · SSENSE — FILTERED TO YOUR SIZE, RANKED TO YOUR EYE`.

- Single acid CTA (full-width, zero-radius, 1px ink border, glow): **`begin calibration`**
- Tiny mono link bottom: `already calibrated? sign in`

**Required:** trivially. No account yet. Tapping the CTA silently creates a **Supabase anonymous session** (`signInAnonymously()`) — this is the cleanest mechanism for pre-signup persistence; the anon user later converts via `updateUser({email, password})` and *all deck taps are already in the right rows*. LocalStorage mirror as offline fallback.

## Screen 1 — The Calibration Deck (60–90 sec) ★ the founder's opener

Full-bleed product card stack, one card visible, next peeking 4px behind (offset, ink border — a stack of spec sheets). Card = photo, then serif lowercase product name, mono eyebrow `SPECIMEN 007 / 030 — [ CALIBRATION ]`. **No prices, no brands shown on-card** — brands bias the read; this deck measures the *eye*, not label loyalty. (Brand capture happens on Screen 2.)

- **Swipe right = "i'd wear it" / swipe left = "never."** Two big bottom-anchored fallback buttons `✕` and `♥` for non-swipers — same glyphs as the feed, teaching the app's vocabulary before they ever see the feed.
- Binary only. No cart, no super-like, no undo stack (one small `undo` mono link for the last card). Speed is the product here; every extra choice halves throughput.
- **Deck contents:** ~30 cards drawn from a pre-curated pool of ~120 of the 5,600 attributed items, chosen offline for **coverage of the attribute space** — clean, legible, single-mood exemplars spanning silhouette (slim→oversized), mood (minimal→avant-garde→workwear→streetwear…), statement 1–5, palette, materials. Cards 1–10 are fixed max-spread anchors; cards 11–30 are picked adaptively toward the regions where the user's responses are most informative (v1 can ship 3 pre-built spread decks, randomized). **Deck is NOT size-filtered** — this is taste calibration, not shopping; size-filtering would gut the pool.
- **Micro-readouts as reward:** every 10 swipes, a one-second interstitial strip flashes across the card in mono: `reading: drawn to boxy volume` … `reading: black, olive, no logos`. The meter ticks `BLIND → FAINT → FORMING`. This is what makes it feel like a conversation, not a quiz.
- **Exit ramps:** at card 15 a mono line appears under the stack: `signal forming — continue sharpening, or` `see the read →`. Full skip available from card 1 (`skip calibration →` → one honest line: *"the model starts blind. it learns from every tap in your feed."*).

**Required:** no. **Encouraged:** ruthlessly, by fun.

## Screen 2 — The Read (20–30 sec) — the reward that smuggles in brands + budget

This is where survey content dies and becomes a *result*. One editorial spec-sheet card, generated live from deck taps:

```
[ FIRST READ ]                    SAMPLE: 030 TAPS
────────────────────────────────────────────
mood        —  minimal · workwear · quiet archive   (serif, italic)
silhouette  —  relaxed → boxy
palette     —  ■ ■ ■ ■  (actual swatch squares)
statement   —  ▓▓▓░░  RESTRAINED
```

Below it, two tap-fixable rows — corrections, not questions:

1. **`BRANDS WE'D BET ON`** — 6–8 brand chips *inferred* from nearest-neighbor deck loves (Lemaire, Our Legacy…). Tap-to-cycle exactly like the old survey's beloved interaction: tap = confirm-love (acid), tap again = kill (strikethrough), again = clear. One small `+ add a name` search input. Confirmed brands become pseudo-positive taps in `brand_rate`.
2. **`HUNT RANGE`** — one row of five chips, one tap: `<$150 / $150–400 / $400–1K / $1K+ / no ceiling`. That's the entire budget capture. Per-category sliders are a v2 nobody needs on day one.

CTA: **`lock it in`**. Both rows skippable by just not touching them.

## Screen 3 — Fit Lock (25 sec) — REQUIRED, and honest about why

The only form-shaped moment, framed as the unlock it actually is. Mono header: `[ FIT LOCK ]` — serif sub-line: *"nothing that doesn't fit will ever appear. not once."*

Four rows of big tap-chips (multi-select), one thumb, bottom-half of the screen:

- `TOPS` S M L XL XXL · `WAIST` 28…40 numeric strip · `SHOES` US strip · `OUTERWEAR` (prefilled = tops, tweakable)

**Required** because the hard filter is the product's core promise; a feed built without sizes is a lie. This is also *why* it doesn't feel like a form — it's visibly transactional: give sizes, get the guarantee. CTA disabled until tops + waist + shoes each have ≥1 chip.

## Screen 4 — Claim the Model (20–30 sec) — signup at peak sunk cost

**This is where signup goes: after the deck, after the read, after fit lock.** The user is now holding a warm, personalized artifact; the account is how they keep it, not a toll gate before the demo. Conversion logic is straightforward sunk-cost, and Supabase anonymous→permanent conversion makes it one `updateUser` call with zero data migration.

Copy: mono `[ REGISTRY ]`, serif *"your model is warm. give it somewhere to live."* Email + password, one acid button **`claim my model`**. Nothing else — no name, no confirm-password field. **Required** (server-side feed builds need an owner).

## Screen 5 — The Build + The Archive (90–150 sec, zero perceived wait)

The moment the account exists, **the first feed build fires immediately** (stage-1 scoring of ~5,600 items with the deck-seeded prior, vision re-rank of the top 60 against a v0 `visualBrief` an LLM drafts from the loved deck images). That takes 1.5–2.5 minutes. Instead of a spinner, this screen *is* the four-bucket upload offer:

Top: progress strip in the existing scan-bar pattern — `BUILDING PULL 001 — ~2 MIN` with a live mono log ticker (`scoring 5,612 pieces against your weights…` `vision pass: top 60…` `pull 001: composing…`). The product visibly working for you is the best loading state there is.

Below, header serif: *"feed the archive while it works."* Four boxy ink-bordered tiles, 2×2:

```
[ PROVEN ]              [ GRAILS ]
things you bought       the ones that got away
and loved

[ DREAMS ]              [ CLOSET ]
always wanted,          what you own
never pulled            right now
```

Each opens camera-roll multi-select; thumbnails drop into the tile with a mono count (`PROVEN — 4 PIECES LODGED`). Uploads run through the same `extract_attrs` pipeline asynchronously — they strengthen the *next* build, and the UI says so: `absorbed into your model overnight`. **Fully optional, deferrable forever** — the tiles live permanently behind the `⋯` menu as `the archive`, and the build finishing never waits on them.

## Screen 6 — Pull 001 lands

The build completes and the screen cuts to the feed with an editorial cover moment: `[ DAILY PULL 001 ]` · `MODEL CALIBRATED 07.04.26` · top-ranked item full-bleed. The header signal meter persists into the app reading `SIGNAL: FORMING` — honest about MIN_FIT_TAPS — with a one-line explainer on first view: *"every tap sharpens the read. the signal sets around 300."* It quietly fills toward `STRONG` over their first weeks, converting the 300-tap requirement from a cold-start liability into a visible progression mechanic. PWA "add to home screen" prompt appears as a pinned spec card at the top of Pull 001 — never during onboarding.

---

## Summary table

| # | Screen | Required? | Active time |
|---|--------|-----------|-------------|
| 0 | Cover | — | 5 s |
| 1 | Calibration deck (30 swipes, ramp at 15) | skippable | 60–90 s |
| 2 | The Read (brands + hunt range as corrections) | skippable per-row | 20–30 s |
| 3 | Fit Lock (sizes) | **REQUIRED** | 25 s |
| 4 | Claim the Model (email+password) | **REQUIRED** | 25 s |
| 5 | Build + four buckets (parallel) | optional, deferrable | 0 s perceived |
| 6 | Pull 001 | — | — |

**Time to first feed: ~4 min wall clock, ~2.5 min of input, no dead waiting anywhere.**

## Cold-start mechanics (what the 30 swipes actually buy)

30 taps can't drive `logistic_fit` alone (MIN_FIT_TAPS ≈ 300). The deck output is a **prior**, not a fitted model: fit with heavy L2 shrinkage toward a population-mean weight vector, add brand-confirm chips as pseudo-taps into `brand_rates`, and let the deck's loved images seed `visualBrief` v0 via one LLM call. Because the deck was coverage-sampled across the attribute space, 30 well-spread taps constrain the big axes (statement level, silhouette family, palette, mood cluster) far better than 30 random feed taps would — which is exactly what Pull 001 needs to not feel random. The UI never overclaims: the signal meter says `FORMING` until real history takes over.

Key files this maps onto: `/Users/vega/Desktop/Vega Closet/web/index.html` (Supabase auth at lines ~453–497 — needs `signInAnonymously` + `updateUser` conversion added; sign-in-only today), `/Users/vega/Desktop/Vega Closet/web/pipeline/taste_model.py` (deck prior plugs into `fit_user_weights` / `brand_rates`; uploads reuse `extract_attrs`), `/Users/vega/Desktop/Vega Closet/survey.html` (kill steps 2–8; its tap-to-cycle chip interaction survives as the brand-correction row on Screen 2; its size step survives as Fit Lock).