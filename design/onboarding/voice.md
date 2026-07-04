# TRM onboarding — copy + ritual (every string ready to paste)

**Voice rule used throughout:** UPPERCASE IBM PLEX MONO = the machine talking (labels, statuses, verdicts). lowercase Cormorant serif = the atelier talking (warm, human, italic for asides). The two voices alternate like a fitting: the tailor murmurs, the chalk marks. No string below is decorative filler — each is annotated `(mono)` or `(serif)` / `(serif italic)`.

The ritual has a name. Don't call it onboarding anywhere in the UI. Call it **THE FITTING**. Steps are numbered like a spec sheet: `FITTING 01 — 06`. That single move reframes every screen from "form" to "appointment."

---

## 1 — Opening screen

```
(mono, small, top)      [ FITTING 01 / 06 ]
(mono logo)             TRM
(serif, large)          a taste model, fitted to you.
(serif)                 trm learns what you'd actually wear — then hunts
                        grailed, the realreal and ssense for it, every day,
                        only in your size.
(serif italic)          it can't read your mind yet. so first, a fitting.
(mono, button)          BEGIN THE FITTING
(mono, ghost link)      WHAT IS TRM ↗
```

Sub-line under the button (mono, faint): `NO FORMS. YOU JUST DECIDE THINGS.` — this is the promise Charles asked for, stated out loud.

---

## 2 — The swipe deck

**Verdicts: `PASS` / `PULL`.** Not like/dislike, not keep/cut. "Pull" is what a stylist does — pulls pieces from the racks for a client — and it's already canon in the app (`[ DAILY PULL ]`). Right-swipe literally means *pull it for me*. Left stamp is ink; right stamp is acid `#DEF65A` with glow. Avoid "CUT" as the negative — it collides with "MAKES THE CUT," where cut is good.

```
(mono, deck header)     [ THE DECK ]  ·  TASTE SIGNAL — NONE
(serif, instruction)    forty pieces. no wrong answers.
(serif italic)          swipe right to pull it. left to pass. go with your gut —
                        the model is watching how you decide, not what you can afford.

(mono, left stamp)      PASS
(mono, right stamp)     PULL
(mono, super-pull*)     GRAIL   *swipe up, optional third gesture — counts double

(mono, progress)        SIGNAL 07/40
```

**Progress arc** (the header status upgrades at milestones — this is the anticipation engine):

```
0–9    TASTE SIGNAL — NONE
10–19  TASTE SIGNAL — FAINT
20–29  TASTE SIGNAL — FORMING
30–39  TASTE SIGNAL — READING
40     TASTE SIGNAL — LOCKED (FIRST PASS)
```

**Milestone interstitials** — every 10 cards, one card-sized break slides in with a live read from the model. This is where the deck stops feeling like a chore and starts feeling like a conversation:

```
at 10 (serif italic)   interesting. you keep pulling volume.
        (mono)          SILHOUETTE READ — BOXY / RELAXED
at 20 (serif italic)   you pass on logos almost every time. noted.
        (mono)          BRANDING READ — SUBTLE TO NONE
at 30 (serif italic)   ten more. the picture is getting sharp.
        (mono)          PALETTE READ — INK / BONE / OLIVE
```

(These reads are generated from the running tally against the attribute schema — silhouette, branding, palette, mood. Even rough tallies feel like magic here.)

**Deck complete:**

```
(mono)                  DECK COMPLETE · 40 VERDICTS LOGGED
(serif)                 that's a first sketch of your taste.
(serif italic)          now make it a portrait.
(mono, button)          CONTINUE — FITTING 03
```

---

## 3 — The four upload buckets

Frame the screen as an archive intake, not an upload form. Every bucket is optional and says so. Photos or screenshots both work — say so, it kills the friction.

```
(mono, header)          [ FITTING 03 / 06 ]  ·  THE ARCHIVE
(serif, lead)           the deck showed us your instincts.
                        your history shows us your standards.
(serif italic)          add what you can. skip what you can't. screenshots count.
```

**Bucket 1 — previous purchases**

```
(mono, name)            [ THE RECEIPTS ]
(serif, one-liner)      what you actually bought. money is the loudest taste signal there is.
(serif italic, empty)   order screenshots, fit pics, tags still on — anything.
                        even one purchase teaches the model more than ten swipes.
(mono, confirm)         3 RECEIPTS LOGGED — SIGNAL WEIGHT: HEAVY
```

**Bucket 2 — grails**

```
(mono, name)            [ GRAILS ]
(serif, one-liner)      the pieces you hunted down — or the ones that got away.
(serif italic, empty)   the runway shot you saved in 2019. the sold listing you
                        still think about. this is where obsession goes.
(mono, confirm)         GRAIL LOGGED — THE HUNT LIST HEARS YOU
```

**Bucket 3 — dream items**

```
(mono, name)            [ THE DREAM RACK ]
(serif, one-liner)      never owned. always wanted. no budget on this shelf.
(serif italic, empty)   don't be realistic here. the model needs to know where
                        your taste is headed, not just where your wallet is.
(mono, confirm)         DREAM LOGGED — AIM RECORDED
```

**Bucket 4 — in your closet now**

```
(mono, name)            [ IN ROTATION ]
(serif, one-liner)      what's on your rack today. the wardrobe we're building around.
(serif italic, empty)   shoot the hangers. shoot the shelf. blurry is fine —
                        we're reading shapes and fabric, not taking portraits.
(mono, confirm)         7 PIECES ON RECORD — ROTATION MAPPED
```

**Skip affordance** (bottom of screen, always visible — this is what makes it not-homework):

```
(mono, ghost)           SKIP FOR NOW
(serif italic, under)   the archive stays open. add pieces whenever one comes to mind.
```

---

## 4 — Sizes: the measurement, not the form

The tailor conceit, played straight. Keep survey.html's tap-to-cycle (it was loved) — no keyboards, no dropdowns. Each measurement is one big card you tap to cycle through values, chalk-mark style: current value huge in serif, options ticking in mono.

```
(mono, header)          [ FITTING 04 / 06 ]  ·  MEASUREMENTS
(serif, lead)           stand still. this is the part that makes trm ruthless:
(serif)                 nothing that doesn't fit you will ever reach your feed. not once.
(serif italic)          tap each card to cycle. close enough is fine — you can
                        re-measure anytime.

(mono, card labels)     CHEST / TOPS        →  value serif, e.g.  xl
                        WAIST               →  w 36
                        INSEAM              →  32
                        SHOE                →  us 14
                        OUTERWEAR           →  xl / eu 54

(mono, per-card hint)   TAP TO CYCLE
(mono, lock line)       SIZES LOCKED — EVERY SCAN, EVERY SOURCE, FITTED
(serif italic, close)   that's the whole point of a fitting. it fits.
```

---

## 5 — Brand loves + budget

**Brands = HOUSES.** Type-ahead chips, not a list of 200 checkboxes. Loves only — no "avoid" list in onboarding (the model learns avoidance from passes; asking for hate up front is form-energy).

```
(mono, header)          [ FITTING 05 / 06 ]  ·  HOUSES
(serif, lead)           name the houses you trust.
(serif italic)          three is plenty. the model treats these as compass
                        points, never as a fence — it will still surprise you.
(mono, input hint)      TYPE A NAME — RICK OWENS, LEMAIRE, OUR LEGACY…
(mono, chip state)      MARGIELA ✓ LOGGED
(mono, skip)            NO HOUSES — LET THE DECK SPEAK
```

**Budget = THE CEILING.** Never say "budget." Frame it as a hunting parameter — and sell the resale math while you're at it:

```
(mono, label)           [ THE CEILING ]
(serif, lead)           we shop resale. the $2,400 coat shows up at $600.
                        so set your ceiling per piece, not your dream.
(mono, tap-cycle)       UNDER $200 · $200–500 · $500–1K · $1K+ · NO CEILING
(serif italic, aside)   grails are exempt. if the one appears, we'll show you anyway —
                        clearly marked, zero pressure.
```

---

## 6 — The finale: calibration theater

Full-screen, paper background, the grid faintly animating. Mono status lines tick in one by one (600–900ms apart, real work happening behind them where possible). This is the payoff — spend the drama here.

```
(mono, header)          [ FITTING 06 / 06 ]  ·  CALIBRATION

(mono, ticking lines)   COMPILING VERDICTS ..................... 40 LOGGED
                        READING ARCHIVE ........................ 11 PIECES
                        WEIGHTING ATTRIBUTES ................... SILHOUETTE / PALETTE / MOOD
                        LOCKING SIZES .......................... XL · W36 · US 14
                        SCANNING 5,600 PIECES AGAINST YOU ...... ████████░░
                        FIRST PULL ............................. RANKED

(mono, stamp, acid)     MODEL CALIBRATED 07.04.26
(mono, sub)             TASTE SIGNAL — FORMING
```

Then the welcome, the one serif moment that should feel signed:

```
(serif, large)          your model is live.
(serif)                 it knows your shape, your ceiling, and forty of your
                        opinions. every verdict from here sharpens it.
(serif italic)          around three hundred, it stops guessing and starts knowing.
(mono, button, acid)    OPEN THE DAILY PULL
```

**Honesty beat, kept in-voice** (this handles MIN_FIT_TAPS without apologizing): the feed header carries the signal status permanently — `TASTE SIGNAL — FORMING` with a thin progress hairline toward `STRONG` — so early-feed roughness reads as *the model is young*, not *the app is bad*. When the user crosses ~300 taps, fire one interstitial:

```
(mono)                  TASTE SIGNAL — STRONG
(serif italic)          the model isn't guessing anymore. welcome to the good part.
```

---

## One-line ritual map

`BEGIN THE FITTING → THE DECK (PASS/PULL, signal NONE→LOCKED) → THE ARCHIVE (receipts/grails/dream rack/rotation, skippable) → MEASUREMENTS (tap-to-cycle, "it fits") → HOUSES + THE CEILING → CALIBRATION (spec-sheet theater) → OPEN THE DAILY PULL`

Total asks the user can *fail* at: zero. Everything is a verdict, a tap-cycle, or a skip — nothing is a blank field except the brand type-ahead, and that one has a skip that flatters them (`LET THE DECK SPEAK`).

Sources read: `/Users/vega/Desktop/Vega Closet/survey.html` (tap-to-cycle pattern preserved), `/Users/vega/Desktop/Vega Closet/web/index.html` (`[ filter scan ]` bracket-label convention confirmed as canon), `/Users/vega/Desktop/Vega Closet/web/design/UI-SPEC.md` (love/cart/pass verbs, gold = cart identity, resale price-drop framing).