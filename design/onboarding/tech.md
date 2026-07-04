# TRM onboarding — technical architecture read

Read: `web/index.html` (1146 lines, vanilla JS single-page), `pipeline/taste_model.py`, `pipeline/build_feed_cloud.py`, `pipeline/conductor.py`, `cloud/schema.sql`, `cloud/run_conductor.sql`, `.github/workflows/conductor.yml`, `pipeline/attr_extract.py`.

## 1. Swipe deck in the existing PWA — nothing structurally blocking

The app is framework-free vanilla JS with all state in `boot()` (`index.html:570`). The deck is a new full-screen overlay div (sibling of `#auth`/`#app`), shown when the user's signal count is low; reuse the existing bottom-sheet pattern (`.scan-sheet`) for enter/exit transitions.

**Implementation:**
- **Card stack:** render top 3 cards absolutely stacked, `transform: translateY(n*6px) scale(1-n*0.03)`. Do NOT reuse `.card` markup (it's grid-tuned); a new `.deck-card` with the same `esc()`/`safeUrl()` helpers and the paper/ink/zero-radius styles is ~60 lines of CSS.
- **Touch:** Pointer Events (`pointerdown/move/up` + `setPointerCapture`), not touch events — one code path for testing on desktop. Drag = `translate(dx,dy) rotate(dx*0.06deg)`; commit threshold ~90px or velocity flick; overlay a `[ LOVED ]` / `[ PASS ]` acid/gold stamp whose opacity tracks `|dx|`. Add `touch-action: none` on the card so iOS doesn't scroll-fight. Buttons under the stack mirror the gestures (accessibility + the existing love/cart/pass muscle memory). Swipe-up = cart is a natural third gesture since `signals.action` already allows `'carted'` (`cloud/add_cart_state.sql`).
- **Writing signals:** the persistence code already exists verbatim at `index.html:882` — `sb.from('signals').upsert({user_id, url, action, brand, category, reasons, price}, {onConflict:'user_id,url'})`. Extract that into a shared `persistSignal(it, action)` and both the deck and the existing tap handler call it. Batch is unnecessary; one upsert per swipe is fine and gives per-swipe durability.
- **One hard constraint:** `signals.url` has a FK to `catalog(url)` (`schema.sql:71`). Deck items **must be catalog rows**. Since the deck is curated from the ~5,600 attributed catalog items, this is satisfied for free — but it means uploads can never be written as `signals` rows (see §3).
- **Killer feature the static-deck choice unlocks:** the deck can run **before signup**. Buffer swipes in `localStorage`, replay them through `persistSignal()` right after `signUp()` succeeds. "Gets the user immediately going" with literally zero forms first.

**Blocking?** No. The realtime channel (`index.html:1123`) and `applyStates()` already reconcile signals; deck swipes just appear as pre-acted cards in the feed.

## 2. Deck curation — static `web/deck.json`, regenerated weekly

**Where it lives:** a static file in the repo, `web/deck.json`, served by the same GitHub Pages deploy as the app. Not a feeds row (feeds are per-user + RLS'd, deck must be readable pre-auth), not a new table (adds an anon-read policy and a query for what is a cacheable constant). Static file = works logged-out, CDN-cached, versioned in git.

**Generator:** new `pipeline/build_deck.py` (~120 lines, mirrors `attr_extract.py` plumbing):
1. Fetch catalog rows where `attrs` is not null, image is http, `last_seen` recent.
2. Greedy max-coverage over the one-hot space `taste_model.featurize()` emits: repeatedly pick the item covering the most not-yet-seen attribute values (silhouette × mood × palette × construction × branding × era × weight, plus statement/formality bands). Cap 2 per brand, then stratified backfill per mood to ~80 items.
3. Emit only what the deck needs: `{url, image, brand, title, price, category, platform}` — ~40 KB.

**Cadence:** new `.github/workflows/deck.yml`, weekly cron + `workflow_dispatch`, runs the script and commits `web/deck.json` (commit triggers the Pages redeploy). Weekly regen also handles dead listing images. Alternatively add it as a third step in `conductor.yml` weekly — separate workflow is cleaner.

## 3. Uploads — Storage bucket + a GitHub Actions vision job (reuse the dispatch machinery)

**Storage:** one private bucket `wardrobe`, path convention `{user_id}/{bucket}/{uuid}.jpg` where bucket ∈ `purchases|grails|dreams|closet`. Storage RLS: insert/select/delete `to authenticated using ((storage.foldername(name))[1] = auth.uid()::text)`. Client uploads with the publishable key via `sb.storage.from('wardrobe').upload(...)`; compress client-side with canvas to ~1600px before upload (iOS photos are 4-12 MB).

**Vision pass → visualBrief:** run it in **GitHub Actions**, not an edge function. Reasons: (a) the exact dispatch→poll pattern already exists and is battle-tested (`run_conductor` RPC → pg_net → `workflow_dispatch` → poll, `run_conductor.sql` + `index.html:1043 fireScan()`); (b) edge functions have wall-clock limits and 4 buckets × N photos × vision calls plus a synthesis call is minutes of work; (c) the Anthropic key already lives in Actions secrets (`conductor.yml:56`).

Concretely:
- New `pipeline/onboard_brief.py`: given `USER_ID`, list the user's `wardrobe/` objects with the service key, download + base64 the images (no signed-URL dance), send batched to Claude with the four bucket labels as context ("previous purchase" vs "dream" carry different weight), synthesize a prose `visualBrief` (same shape `vision_score()` at `conductor.py:396` expects: `{brief, lovedSignals, passedSignals}`), extract seed brand names, and `PATCH taste.payload` (merge, don't clobber — the deck swipes are already writing tallies). Also stamp `payload.meta.briefBuiltAt`.
- New `onboard.yml` workflow with a `user_id` input; new RPC `run_onboarding()` (copy of `run_conductor`, security definer, passes `auth.uid()::text` as the input — server-injected, not client-supplied), logged in `conductor_dispatches`.
- App polls `taste.payload.meta.briefBuiltAt` the way `startFeedPoll` polls `feeds.built_at`, and renders it as "MODEL CALIBRATED {date}" theater. 1-3 min latency is fine because the user is swiping the deck meanwhile.

**Cold-start blocker found here (must fix):** `build_feed_cloud.py:226` — stage-2 vision runs only `if ANTHROPIC_KEY and brief and weights:`, and `weights` is only fitted at `MIN_FIT_TAPS = 300`. So a brand-new user with a fresh upload-derived visualBrief gets **zero personal ranking** until 300 taps — exactly the users onboarding serves. Change the condition to `if ANTHROPIC_KEY and brief:` and rank stage-2 candidates by `base_score` when `weights` is empty. Also consider a low-trust early fit (higher `l2`, taps ≥ 50) in `fit_user_weights` rather than the hard 300 cliff.

## 4. Signup — `signUp()` plus a taste-row trigger

- `index.html` has **no signup path at all** — only `signInWithPassword` (`:491`). Add a "create account" toggle to the authcard calling `sb.auth.signUp({email, password})`. Decide the Supabase email-confirmation setting deliberately: for a PWA onboarding flow, keep confirmations ON but let the deck swipe-buffer (from §1) absorb the wait; flush on first real session.
- **Per-user rows:** `taste` and `feeds` rows are hand-created today, and `build_feed_cloud.main()` only builds users that have a `taste` row (`:312 fetch_all("taste", ...)`). Fix with a Postgres trigger — `on auth.users after insert`, security-definer function inserting `taste (user_id, payload) values (new.id, '{"meta":{"createdAt":...}}')`. The `feeds` row then materializes automatically on the next rebuild (`build_feed_cloud.py:281` upserts `on_conflict=user_id`). The app already tolerates a missing feeds row (`PGRST116` handling, `index.html:581`).
- After onboarding completes, fire one feed rebuild (the `onboard.yml` workflow just runs `build_feed_cloud.py` as its second step, same as `conductor.yml:68`).

## 5. Sizes — store in `taste.payload.sizes` now; per-user filtering is the real project

Today sizes exist **nowhere in data** — they are code:
- `conductor.py:84 in_size()` — Charles's exact rules incl. the Balenciaga-46 exception
- `conductor.py:187 GRAILED_SIZES` — Algolia facets
- `conductor.py:304` — TRR `clothingSize: ["27","28","29"]`
- `conductor.py:591 size_bucket()`, `build_feed_cloud.py:173 foot_ok()`, section subtitles `build_feed_cloud.py:65`, filter chips `index.html:663`

**The structural problem:** size is a **scrape-time hard filter**, so the shared catalog only contains Charles-sized items. User #2 with different sizes gets an empty (or wrong) feed no matter what the feed builder does.

**Must change for user #2:** (a) onboarding writes `taste.payload.sizes = {tops:["L","XL"], waist:[36,38], footwear:{us:[14,15], eu:[47,48]}, exceptions:[...]}`; (b) `conductor.py` builds `GRAILED_SIZES` as the **union of all users' sizes** (Grailed is the free source — union costs nothing) and `in_size()` becomes `size_ok(sizes_union, ...)`; (c) `build_feed_cloud.build_for_user()` applies a per-user `size_ok(user.sizes, item)` where `foot_ok` is now (the pattern already exists — `n_size_retired`).

**Can wait:** TRR/SSENSE per-user size facets (paid sources, keep them Charles-tuned or on the union's coarse buckets), `size_bucket()` generalization, chip labels (`szSeen` already self-prunes; generate the list from the feed payload later), section subtitles (make them generic strings now, 5-minute fix).

## Active multi-user blockers in current code (beyond sizes)

1. **`conductor.py:461`** — `taste?select=payload&limit=1` with the comment "first taste row = him for now": loved brands, visualBrief, tasteWeights, deep-gated brands, and the un-dismiss patch (`:509`) all read/write **whoever's row comes back first**. User #2's signup makes this nondeterministic. Fix: scheduled runs union `brands.loved` across all taste rows; brand-specific dispatches carry the requesting user's id.
2. **`run_conductor` RPC is global** — user #2's "scan all" scans Charles's brands; and `conductor.yml` `concurrency: vega-conductor` serializes all users' scans (fine at N=2, note it).
3. **Stage-2 gated on `weights`** (`build_feed_cloud.py:226`) — the cold-start killer described in §3.
4. **No signup path / hand-created rows** — §4.
5. **`signals.url` FK to catalog** — uploads must never be inserted as signals; upload-derived taste lives only in `visualBrief` + seed brands (or a future `uploads` table if you want them ranked).

## Effort sizing

| Piece | Size | Notes |
|---|---|---|
| Swipe deck UI + signal writes + pre-auth buffer | **M** | ~400 lines in `index.html`; persistence already exists |
| `build_deck.py` + `deck.yml` weekly regen | **S** | mirrors `attr_extract.py` |
| Signup flow + taste-row trigger | **S** | ~40 lines JS + 1 SQL migration |
| Sizes capture UI (tap-to-cycle, from `survey.html` patterns) → `taste.payload.sizes` | **S** | UI only; consumers are the L item below |
| Uploads: bucket + RLS + upload UI (4 buckets, client compress) | **M** | |
| `onboard_brief.py` + `onboard.yml` + `run_onboarding()` RPC + calibration polling UI | **M** | copies the proven dispatch/poll pattern |
| Stage-2 cold-start fix (brief without weights + early low-trust fit) | **S** | small diff, big product effect — do first |
| Per-user sizes end-to-end (conductor union + per-user feed filter + de-Charles `in_size`) | **L** | the only structural rework; catalog is pre-filtered to one body |

Key files: `/Users/vega/Desktop/Vega Closet/web/index.html`, `/Users/vega/Desktop/Vega Closet/web/pipeline/conductor.py`, `/Users/vega/Desktop/Vega Closet/web/pipeline/build_feed_cloud.py`, `/Users/vega/Desktop/Vega Closet/web/pipeline/taste_model.py`, `/Users/vega/Desktop/Vega Closet/cloud/schema.sql`, `/Users/vega/Desktop/Vega Closet/cloud/run_conductor.sql`, `/Users/vega/Desktop/Vega Closet/web/.github/workflows/conductor.yml`.