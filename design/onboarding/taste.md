# TRM Onboarding — Taste-Science Spec

Grounded in the real code: `featurize()` in `/Users/vega/Desktop/Vega Closet/web/pipeline/taste_model.py` one-hots silhouette/materials/palette/construction/mood/branding/era/weight/category plus `statement/5`, `formality/5`, `logprice`, `brand_rate`, `bias`; `prune_rare(min_df=8)`; plain SGD logistic with `l2=1.2e-3`; `MIN_FIT_TAPS = 300` in `build_feed_cloud.py` (below it → user-neutral `base_score` fallback); labels are `liked/carted → 1`, `passed → 0`; stage-2 re-ranks the top slice against `visualBrief`.

## 1. Swipe count: 60 core, structured as 3 rounds of 20, early exit at 36

- **Floor is ~36.** Below that, `prune_rare(min_df=8)` kills almost every one-hot feature and the fit degenerates to bias + statement + formality. 36 swipes is where mood and silhouette main effects first become estimable.
- **Sweet spot is 60.** Each item carries ~10–14 active features, so 60 swipes ≈ 700 feature-observations across ~90 realistic feature values → ~7 observations per value. That's enough for a heavily regularized logistic to get *signs* right on the big axes (mood, silhouette, statement, palette), which is all a cold model needs — stage-2 vision does the fine ranking. 60 swipes at ~2s each is ~2 minutes: still a game, not a task.
- **Cap at 90–120 (optional bonus rounds of 12).** Past ~90 the deck must revisit attribute regions it already covered, so marginal information per swipe collapses while fatigue rises. Offer "KEEP TRAINING +12" for the obsessives; never require it.
- **UI framing:** progress is the model, not a bar — `TASTE SIGNAL — FAINT → FORMING → STRONG` ticking up as rounds complete, with `MODEL CALIBRATED 07.04.26` stamped at the end. Three gestures: right = love, left = pass, up = "GRAIL" (maps to `carted`, duplicated 2x in the fit set, mirroring the live cart-is-double-a-love convention).

## 2. Deck curation: greedy feature-coverage over `featurize()` keys, not a factorial grid

A full mood×silhouette×statement factorial is 15×10×3 = 450 cells — unfillable and wasteful. The right objective is coverage of the *actual feature space the model fits on*. Algorithm (offline, one-time, produces a ~240-item onboarding pool; each user gets a seeded 60-item sample):

```
1. ELIGIBLE = catalog items with attrs, silhouette != "na", price > 0,
   clean product image (SSENSE-style flat/model shot preferred)
2. LEGIBILITY pass (one-time, ~cheap): Haiku scores each candidate
   "is this piece an unambiguous archetype of its attributes? 0-1";
   keep >= 0.8. Ambiguous items are wasted swipes — a pass on a
   confusing item labels nothing.
3. Featurize all eligible items with the exact featurize() function.
   Set quota[feature_value] = 4 for every mood, silhouette, palette,
   material, construction, branding, era, weight, category value,
   and for each statement band {1-2, 3, 4-5} and price quartile.
4. Greedy max-coverage: repeatedly pick the item that satisfies the
   most still-unmet quota units, subject to: max 2 items per brand,
   category mix ≈ live feed mix (outerwear/tops/bottoms/footwear).
5. Stop at 240. Per-user deck = stratified sample of 60 from the pool.
```

Sequencing matters: **round 1 (20) = maximum spread** — the archetypes, one per major mood family, warm-opened with 4 mid-statement crowd-pleasers to teach the gesture. **Round 2 (20) = adaptive refine**: fit a throwaway logistic on round 1, sample round 2 from pool items whose predicted p is nearest 0.5 (uncertainty sampling — each swipe near the boundary is worth ~3 swipes in a settled region). **Round 3 (20) = confirm + contrast**: half from the user's emerging positive region at varied statement/price, half deliberate foils (same mood, opposite silhouette) to separate correlated attributes.

**Hide brand names and prices on deck cards.** A pass on a card showing "$1,240 / Rick Owens" is contaminated — is it the drape or the price or logo-lore? Deck swipes should be pure visual signal. Consequence for the fit: flag onboarding rows (`source: "deck"`) and drop `logprice` and `brand_rate` from those rows' feature dicts — the user never saw either, so those features would learn noise. Live-feed taps keep them.

## 3. What each source yields

| Source | Mechanism | Weight |
|---|---|---|
| Deck swipes | Literal `signals` rows, same schema as live taps (love=1, pass=0, grail=carted dup 2x). Count toward MIN_FIT_TAPS. | 1x (grail 2x) |
| Previous purchases / closet-now uploads | `extract_attrs()` on each photo → synthetic positive row, **duplicated 3x** in `labeled` (logistic_fit has no sample weights; duplication is the stdlib move — matches Charles's own OWNED ~3x > likes rubric) | 3x |
| Grail uploads | Synthetic positive, dup 2x | 2x |
| Dream uploads | Synthetic positive, dup 1x — aspirational taste predicts love-taps, not purchases | 1x |

Uploads yield zero negatives; deck passes (~40–50% of swipes) supply the negative class, so balance stays healthy.

**visualBrief composition (the real day-1 workhorse):** one LLM call after onboarding, fed (a) the top ±15 fitted weights verbalized, (b) upload attrs grouped by bucket with bucket semantics spelled out — *"he OWNS these: fit, fabric weight, formality baseline"* vs *"he DREAMS of these: direction of travel, statement ceiling"* — and (c) the survey remnants (loved brands, budget) captured as chips. The brief must explicitly separate buy-now taste from aspiration so stage-2 doesn't flood the feed with costume pieces he'd love but never cart. Loved brands also seed `brand_rates()` and the loved-brand list; budget seeds the passed-price soft cap.

## 4. Cold-start math: prior-anchored fit, not a lower cliff

End state: ~60 swipes (+~10 grail dups) + ~15 uploads at 1–3x ≈ **100–120 effective rows vs MIN_FIT_TAPS=300**. Don't just lower the threshold — replace the cliff with shrinkage:

- **Fit a population prior `w_prior`** once from all pooled signals (at v0, a de-branded version of Charles's mature weights: zero out `brand=` and `brand_rate` terms, keep the attribute weights as "house taste").
- **Penalize toward the prior instead of zero.** One-line change in `logistic_fit`: gradient term becomes `l2 * (w[k] - w_prior.get(k, 0))`, with `l2` scaled up by `300/max(n, 40)` so shrinkage grows as data shrinks. At n=100 the user's data moves weights where it's confident and inherits the prior everywhere else.
- **Equivalent cheap alternative** if touching the optimizer is unwanted: fit personal weights with strong l2, then blend dicts: `w = λ·w_user + (1−λ)·w_prior`, `λ = n/(n+150)` → λ≈0.4 at onboarding-end, ≈0.67 at 300 taps, →1 as history grows. Continuous, no mode switch, delete the fallback branch.
- **Stage-2 carries week one.** The visualBrief is fully personal from minute zero, so let vision re-rank a wider slice during the provisional period (top 90 instead of 60) and weight it heavier: `final = 0.5·s1 + 0.5·vfit` until n≥300, then revert to the current formula. Stage-1's only job cold is *nominating* plausible candidates; the rubric orders them.
- Drop `MIN_FIT_TAPS` as a hard gate to **40** (the fit floor) — with prior-anchoring it can't do damage below 300, and every post-onboarding tap smoothly buys autonomy. A daily user crosses 300 real taps in 2–4 weeks with no visible transition.

## 5. Sizes: agree — the deck is never size-filtered

Emphatically agree. Deck items are taste probes: silhouette reads off the photo regardless of the listing's tagged size, and size-filtering 5,600 items down to one man's measurements would gut the stratification (and telegraph "shopping" instead of "training"). The code supports this cleanly: `featurize()` contains no size feature, so a swipe on an out-of-size listing is a 100% valid `signals` row. Sizes are a **feed-time hard filter only**.

Capture sizes as a single interstitial *between rounds 1 and 2* — `[ CUTTING YOUR PATTERN ]` — three or four tap-to-cycle chips (chest / waist / inseam / shoe) reusing the well-liked cycle interaction from `survey.html`. Placed mid-deck it reads as the atelier taking your measurements between fittings, not a form; it also gives the round-2 adaptive fit a beat to compute. Everything else from the old 9-step survey either dies (vibe, silhouettes, textures, colors — the deck measures those better than self-report) or shrinks to chips inside the upload step (brands love, budget).

**One-screen summary:** 60 swipes in 3 adaptive rounds (greedy feature-coverage deck, brands/prices hidden, grail-swipe = cart) + 4 upload buckets weighted 3x/3x/2x/1x as synthetic positives + prior-anchored logistic (λ = n/(n+150)) + rubric-heavy stage-2 for week one + sizes as a mid-deck tap-to-cycle interstitial that filters the feed, never the deck.