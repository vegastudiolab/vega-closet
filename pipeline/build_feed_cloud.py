#!/usr/bin/env python3
# Rebuild every user's feed from the shared catalog + their taste + their signals.
# Runs the SAME adj_score as the local build_feed.py (byte-for-byte: +0.30 loved-pin,
# NO brand penalty, style-tag +/-, soft price ceiling). Writes each user's feeds row
# and folds lasting patterns back into their taste. Config from env (Actions) or .env.
import os, sys, re, json, urllib.request, urllib.error, time as _time
from collections import Counter
from datetime import date, datetime, timezone, timedelta
import taste_model

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
STAGE2_TOP = 60          # personal vision re-ranks only the slice that reaches the top of the feed
STAGE2_TOP_YOUNG = 90    # young models (<MATURE_TAPS) lean harder on the rubric: wider slice, stronger nudge
STAGE2_MAX_FRESH = 400   # ACTIVE users: whole in-wall pool gets judged, this many fresh verdicts per rebuild (~$1.30 cap)
ACTIVE_DAYS = 14         # tapped within this window -> active (dormant users keep the small slice)
MATURE_TAPS = 300        # above this, the user's own fitted weights carry; below, prior + vision carry more
CLEAR_WEIGHT = 0.5       # a feed clear trains as half a pass (some clears are about size, not style)
STEAL_RATIO = 0.6        # priced at or under this share of the brand+category median = a steal
PRIOR = taste_model.load_prior()

def _loadenv():
    if os.environ.get("SUPABASE_URL"): return
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, ".env"), os.path.join(here, "..", "..", "cloud", ".env")):
        if os.path.exists(p):
            for line in open(p):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1); os.environ.setdefault(k, v)
            return
_loadenv()
URL = os.environ["SUPABASE_URL"].rstrip("/"); SECRET = os.environ["SUPABASE_SECRET_KEY"]
TODAY = date.today().isoformat()
NOW = datetime.now(timezone.utc).isoformat()

def api(method, path, body=None, extra=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"apikey": SECRET, "Authorization": "Bearer " + SECRET, "Content-Type": "application/json"}
    if extra: h.update(extra)
    r = urllib.request.Request(URL + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read().decode(); return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw

def api_raw(method, path):
    """Binary GET (storage downloads)."""
    r = urllib.request.Request(URL + path, headers={"apikey": SECRET, "Authorization": "Bearer " + SECRET}, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None

_PK = {"catalog": "url", "signals": "url", "user_scores": "url", "taste": "user_id", "feeds": "user_id"}  # stable paging: Range windows without ORDER BY overlap/skip rows as the heap changes (2026-09-23 incident)
def fetch_all(table, select, extra_qs=""):
    rows = []; start = 0; step = 1000; tries = 0
    while True:
        st, part = api("GET", f"/rest/v1/{table}?select={select}{extra_qs}&order={_PK.get(table, 'url')}", None,
                       {"Range-Unit": "items", "Range": f"{start}-{start+step-1}"})
        if st not in (200, 206) or not isinstance(part, list):
            if tries < 3 and (st == 0 or st >= 500):          # transient (Supabase 52x outage 2026-09-23 killed a run mid-way)
                tries += 1; print(f"  fetch {table} {st} — retry {tries}"); _time.sleep(5 * 2 ** tries); continue
            print("fetch fail", table, st, str(part)[:200]); sys.exit(1)
        tries = 0
        rows += part
        if len(part) < step: break
        start += step
    return rows

def norm(s): return (s or "").strip().lower()

_SKIP = {"new","gently used","in-size","in size","value","grail","#1 brand","loved brand","deck","unrated",
         "your basics brand","grailed","ssense","the realreal","therealreal",
         "xs","s","m","l","xl","xxl","xxxl","os"}
def is_style(t):
    t = norm(t)
    if not t or t in _SKIP: return False
    if re.search(r"size|waist|^us \d|^eu \d|^it \d|^uk \d", t): return False
    return True

_ALL_SECTIONS = {
    "outerwear":  ("outerwear","leather, denim, shearling and tech, ranked"),
    "bottoms":    ("bottoms","in-size only, leather and wide-leg lead"),
    "tops":       ("tops + knits","tees, hoodies, knits, shirts"),
    "dresses":    ("dresses","in-size only, ranked to your eye"),
    "skirts":     ("skirts","in-size only, ranked to your eye"),
    "footwear":   ("footwear","in-size only, ranked to your eye"),
    "accessories":("accessories","belts in your size, plus one-size pieces — from accessories scans"),
}
# seasonal section order: summer/spring lead with tops, light layers still welcome;
# fall/winter put outerwear back on top
_SEASON = {12:"winter",1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
           6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall"}[date.today().month]
_ORDER = {
    "summer": ["tops","bottoms","dresses","skirts","footwear","outerwear","accessories"],
    "spring": ["tops","dresses","outerwear","bottoms","skirts","footwear","accessories"],
    "fall":   ["outerwear","tops","bottoms","dresses","skirts","footwear","accessories"],
    "winter": ["outerwear","tops","bottoms","dresses","skirts","footwear","accessories"],
}[_SEASON]
def sections_for(gender):
    # dresses/skirts only for women; men keep the original set
    keys = _ORDER if gender == "women" else [k for k in _ORDER if k not in ("dresses","skirts")]
    return [(k,) + _ALL_SECTIONS[k] for k in keys]
_HEAVY = re.compile(r"shearling|puffer|parka|\bdown\b|\bfur\b|overcoat|heavy ?wool|fleece|quilted", re.I)
_LIGHT = re.compile(r"\blight|windbreaker|coach|track|nylon|denim jacket|shirt jacket|overshirt|\bvest\b|mesh|linen|short ?sleeve|\btank\b|\btee\b", re.I)

GATE_MIN_TAPS  = 15    # a brand-x-category combo needs this many decisions before it can be gated
GATE_MAX_RATE  = 0.10  # smoothed like-rate below this -> gated lane
GATE_VISION    = 0.70  # gated items need at least this vision score to reach the feed
GEM_OVERRIDE   = 0.85  # anything scoring this high always shows, from any brand
PRIOR_TAPS     = 8     # bayesian smoothing anchor so small samples don't over-trigger

def build_for_user(uid, taste, catalog):
    sigs = fetch_all("signals", "url,action,brand,category,reasons,price,created_at", f"&user_id=eq.{uid}")
    liked_ids, passed_ids, carted_ids = set(), set(), set()
    liked_brands, passed_brands = Counter(), Counter()
    liked_tags, passed_tags = Counter(), Counter()
    passed_prices = []
    bc_stat = {}                                  # (brand, category) -> [liked, taps]
    for x in sigs:
        key = (norm(x.get("brand")), norm(x.get("category")))
        st = bc_stat.setdefault(key, [0, 0]); st[1] += 1
        if x["action"] == "liked":
            st[0] += 1
            liked_ids.add(x["url"]); liked_brands[norm(x.get("brand"))] += 1
            for r in x.get("reasons") or []: liked_tags[norm(r)] += 1
        elif x["action"] == "carted":
            # cart = real purchase candidate: strongest positive signal (double a love's weight)
            st[0] += 1
            carted_ids.add(x["url"]); liked_brands[norm(x.get("brand"))] += 2
            for r in x.get("reasons") or []: liked_tags[norm(r)] += 2
        else:
            passed_ids.add(x["url"]); passed_brands[norm(x.get("brand"))] += 1
            for r in x.get("reasons") or []: passed_tags[norm(r)] += 1
            if isinstance(x.get("price"), (int, float)): passed_prices.append(x["price"])
    soft = None
    if len(passed_prices) >= 4:
        passed_prices.sort(); soft = passed_prices[len(passed_prices)//4]
    dismissed = set((taste.get("signals", {}) or {}).get("dismissedUrls") or [])   # feed clears

    # ---- taste lanes: gate brand-x-category combos the signals say he passes on, never whole brands.
    # Recomputed from live signals every build, so a gated combo reopens by itself once he loves from it.
    base_rate = len(liked_ids) / max(1, len(sigs)) if sigs else 0.25
    def smoothed(l, n): return (l + PRIOR_TAPS * base_rate) / (n + PRIOR_TAPS)
    gated = {k for k, (l, n) in bc_stat.items() if n >= GATE_MIN_TAPS and smoothed(l, n) < GATE_MAX_RATE}
    # brand-overall deep gate -> the conductor samples these weekly on the PAID sources (grailed stays daily/free)
    b_stat = {}
    for (b, _), (l, n) in bc_stat.items():
        st = b_stat.setdefault(b, [0, 0]); st[0] += l; st[1] += n
    deep_gated = sorted(b for b, (l, n) in b_stat.items() if b and n >= GATE_MIN_TAPS and smoothed(l, n) < GATE_MAX_RATE)

    # ---- the archive: digest any new wardrobe uploads RIGHT NOW, so they shape this very build.
    # Photos -> attributes -> synthetic positive taps (receipts/rotation 3x, grails 2x, looks 1x)
    # "looks" = full outfits, multi-piece: weak in the item-level fit (noisy), strong in the brief.
    # -> and a rubric addendum for stage-2. No overnight wait: the next scan is the trigger. ----
    UPLOAD_W = {"receipts": 3, "rotation": 3, "grails": 2, "looks": 1}
    up_store = (taste.setdefault("uploads", {"digested": []}))
    seen_paths = {u["path"] for u in up_store["digested"]}
    new_uploads = []
    if ANTHROPIC_KEY:
        try:
            st_l, listing = api("POST", "/storage/v1/object/list/wardrobe",
                                {"prefix": uid, "limit": 500, "sortBy": {"column": "created_at", "order": "asc"}})
            names = []
            if st_l == 200 and isinstance(listing, list):
                # top level lists the bucket folders; walk each
                for folder in ("receipts", "grails", "looks", "rotation"):
                    st_f, objs = api("POST", "/storage/v1/object/list/wardrobe",
                                     {"prefix": f"{uid}/{folder}", "limit": 200})
                    if st_f == 200 and isinstance(objs, list):
                        names += [f"{uid}/{folder}/" + o["name"] for o in objs if o.get("name") and not o["name"].startswith(".")]
            todo = [n for n in names if n not in seen_paths][:60]    # cost cap per rebuild
            for path in todo:
                st_d, raw = api_raw("GET", "/storage/v1/object/wardrobe/" + urllib.parse.quote(path))
                if st_d != 200 or not raw: continue
                import base64
                attrs = taste_model.extract_attrs(ANTHROPIC_KEY, None, image_b64=base64.b64encode(raw).decode())
                if attrs is None: continue
                bucket = path.split("/")[1] if "/" in path else "rotation"
                new_uploads.append({"path": path, "bucket": bucket, "attrs": attrs})
            if new_uploads:
                up_store["digested"] += new_uploads
                print(f"  archive: digested {len(new_uploads)} new upload(s) into the model")
                by_bucket = {}
                for u in up_store["digested"]:
                    by_bucket.setdefault(u["bucket"], []).append(u["attrs"])
                addendum = taste_model.make_brief_addendum(ANTHROPIC_KEY, by_bucket)
                if addendum:
                    vb0 = taste.get("visualBrief")
                    if isinstance(vb0, dict):
                        base_txt = vb0.get("brief") or ""
                        vb0["brief"] = (base_txt.split("\n\n[ ARCHIVE ]")[0] + "\n\n[ ARCHIVE ]\n" + addendum).strip()
                        vb0["archiveAt"] = NOW
                    else:
                        taste["visualBrief"] = {"brief": (str(vb0).split("\n\n[ ARCHIVE ]")[0] + "\n\n[ ARCHIVE ]\n" + addendum).strip()
                                                if vb0 else "[ ARCHIVE ]\n" + addendum, "archiveAt": NOW}
        except Exception as e:
            print("  archive digest failed (continuing):", e)

    # ---- brief from taps: users who never upload still deserve stage-2 vision quality.
    # If no rubric exists (or their loves doubled since we last wrote one), synthesize it from
    # the attributes of what they LOVED/CARTED — the taps are the closet they haven't photographed.
    cat_by_url = {c["url"]: c for c in catalog}
    if ANTHROPIC_KEY:
        try:
            vb_cur = taste.get("visualBrief")
            has_brief = bool((vb_cur.get("brief") if isinstance(vb_cur, dict) else vb_cur) or "")
            loved_attrs = [cat_by_url[x["url"]]["attrs"] for x in sigs
                           if x["action"] in ("liked", "carted") and cat_by_url.get(x["url"], {}).get("attrs")]
            carted_attrs = [cat_by_url[x["url"]]["attrs"] for x in sigs
                            if x["action"] == "carted" and cat_by_url.get(x["url"], {}).get("attrs")]
            synth_at = (taste.get("meta") or {}).get("briefFromTapsN", 0)
            if len(loved_attrs) >= 10 and (not has_brief or (synth_at and len(loved_attrs) >= 2 * synth_at)):
                tap_brief = taste_model.make_brief_addendum(ANTHROPIC_KEY, {
                    "grails": carted_attrs[:20],                       # carted = strongest tapped intent
                    "receipts": [], "rotation": [],
                    "looks": loved_attrs[-40:],                        # newest loves = current direction
                })
                if tap_brief:
                    if isinstance(vb_cur, dict) and has_brief:
                        vb_cur["brief"] = (vb_cur.get("brief","").split("\n\n[ FROM YOUR TAPS ]")[0]
                                           + "\n\n[ FROM YOUR TAPS ]\n" + tap_brief).strip()
                    else:
                        taste["visualBrief"] = {"brief": "[ FROM YOUR TAPS ]\n" + tap_brief, "source": "taps"}
                    (taste.setdefault("meta", {}))["briefFromTapsN"] = len(loved_attrs)
                    print(f"  brief synthesized from {len(loved_attrs)} loved taps — stage-2 unlocked")
        except Exception as e:
            print("  brief-from-taps failed (continuing):", e)

    # ---- stage 1: fit THIS user's taste weights over the shared item attributes ----
    ugender = norm(taste.get("gender")) or "men"
    labeled = []
    for x in sigs:
        c0 = cat_by_url.get(x["url"])
        if c0 and c0.get("attrs"):
            # signal hygiene: a pass on a wrong-gender / wrong-department item was about the ITEM,
            # not the style — it must not teach the style model (audit 2026-09-03: 4% of passes)
            if x["action"] not in ("liked", "carted") and (
                (norm(c0.get("gender")) or "men") != ugender
                or (ugender == "men" and c0.get("category") in ("dresses", "skirts"))):
                continue
            if "deck" in (x.get("reasons") or []):
                # onboarding deck hid brand + price — those features would learn noise
                c0 = dict(c0); c0["price"] = None; c0["brand"] = ""
            labeled.append((1 if x["action"] in ("liked", "carted") else 0, c0))
    n_taps = len(labeled)
    # feed clears train as dislikes at CLEAR_WEIGHT (Charles 2026-09-22: "consider everything
    # disliked", tempered by "some clears are about size"). Style weights only — never brand
    # rates, so clearing a whole feed of Prada can't read as a penalty on Prada.
    acted = liked_ids | carted_ids | passed_ids
    n_clears = 0
    for u in dismissed:
        if u in acted: continue
        c0 = cat_by_url.get(u)
        if not (c0 and c0.get("attrs")): continue
        if (norm(c0.get("gender")) or "men") != ugender or (ugender == "men" and c0.get("category") in ("dresses", "skirts")):
            continue
        labeled.append((0, c0, CLEAR_WEIGHT)); n_clears += 1
    # archive uploads join the fit as weighted positives (what you own outweighs what you tap)
    for u in up_store["digested"]:
        row = {"attrs": u["attrs"], "brand": "", "price": None, "category": None, "url": "upload:" + u["path"]}
        for _ in range(UPLOAD_W.get(u["bucket"], 1)):
            labeled.append((1, row))
    # prior-anchored fit: works at ANY history size — pure house-prior at 0 taps, personal as they grow
    weights, pairs = taste_model.fit_user_weights(labeled, prior=PRIOR)
    brate = taste_model.brand_rates(pairs) if pairs else (lambda b: 0.25)
    young = n_taps < MATURE_TAPS
    n_eff = n_taps + CLEAR_WEIGHT * n_clears + (len(labeled) - n_taps - n_clears)
    lam = n_eff / (n_eff + 150)
    print(f"  stage-1 weights: {n_taps} taps + {n_clears} clears@{CLEAR_WEIGHT}, lambda {lam:.2f} personal ({'young' if young else 'mature'} model, {len(weights)} features)")

    def stage1(it):
        a = it.get("attrs")
        if not (weights and a): return None
        return taste_model.predict(weights, taste_model.featurize(a, it.get("brand"), it.get("price"), it.get("category"), brate(it.get("brand"))))

    def adj(it):
        s1 = it.get("_s1")
        s = s1 if s1 is not None else float(it.get("base_score") or 0)
        url = it["url"]; b = norm(it.get("brand"))
        if url in carted_ids: s += 0.45
        elif url in liked_ids: s += 0.30
        if b in liked_brands: s += min(0.03 * liked_brands[b], 0.09)
        for r in it.get("reasons") or []:
            rn = norm(r)
            if not is_style(rn): continue
            if liked_tags.get(rn):  s += min(0.05 * liked_tags[rn], 0.25)
            if passed_tags.get(rn): s -= min(0.05 * passed_tags[rn], 0.25)
        if soft and isinstance(it.get("price"), (int, float)) and it["price"] > soft * 1.5: s -= 0.06
        # no seasonal nudge: Charles shops every season year-round (2026-09-03); taste only.
        return round(s, 4)

    # THE hard promise: nothing that doesn't fit THIS user ever reaches their feed. Sizes are data
    # (taste.payload.sizes) — the shared catalog holds the union of everyone's sizes; this filter
    # cuts it down to one body. Users without sizes yet (mid-onboarding) see everything.
    ugender = norm(taste.get("gender")) or "men"          # a men user never sees women's pieces, and vice-versa
    usz = (taste.get("sizes") or {})
    def _rx(tokens):
        toks = sorted({norm(t) for t in tokens if t}, key=len, reverse=True)
        return re.compile(r"\b(" + "|".join(re.escape(t) for t in toks) + r")\b") if toks else None
    rx_tops, rx_waist = _rx(usz.get("tops") or []), _rx(usz.get("waist") or [])
    rx_outer = _rx(usz.get("outerwear") or usz.get("tops") or [])   # jackets size separately; blank = same as tops
    rx_shoes = _rx(usz.get("shoes") or [])
    rx_dress = _rx(usz.get("dresses") or usz.get("tops") or [])   # women: dress sizes; falls back to tops
    exc = [(norm(e.get("brand","")), e.get("category",""), _rx(e.get("add") or []))
           for e in (usz.get("exceptions") or [])]
    def fits_user(it):
        if not usz: return True                            # no sizes on file -> no per-user cut
        cat = it.get("category"); s = norm(it.get("size") or "")
        if cat == "accessories":
            if not s or "one size" in s or s in ("os", "o/s"): return True
            belt = _rx((usz.get("waist") or []) + ["90", "95"])
            return bool(belt and belt.search(s))
        if not s: return False
        for b, c, rx in exc:                               # brand quirks (balenciaga 46 etc.)
            if rx and c == cat and b in norm(it.get("brand") or "") and rx.search(s): return True
        if cat == "footwear": return bool(rx_shoes and rx_shoes.search(s))
        if cat in ("bottoms", "skirts"): return bool(rx_waist and rx_waist.search(s))
        if cat == "dresses":  return bool((rx_dress and rx_dress.search(s)) or (rx_tops and rx_tops.search(s)))
        if cat == "outerwear": return bool(rx_outer and rx_outer.search(s))
        return bool(rx_tops and rx_tops.search(s))         # tops

    # cleared listings stay hidden (set above; they also train at CLEAR_WEIGHT). Beyond the exact
    # listing: the same piece in the same size — cleared or passed — never re-surfaces through
    # another listing. A different size or color is a different listing and still comes through.
    def _fam(row):
        return (norm(row.get("brand")), norm(row.get("title")), norm(row.get("sz")) or norm(row.get("size")))
    blocked_fam = set()
    for u in dismissed | passed_ids:
        r0 = cat_by_url.get(u)
        if r0: blocked_fam.add(_fam(r0))
    n_family = 0

    items = []
    n_size_retired = 0
    for c in catalog:
        it = dict(c); url = it["url"]
        it["isCarted"] = url in carted_ids
        it["isArchived"] = url in passed_ids
        it["isLiked"] = url in liked_ids or it["isCarted"]      # carted counts as acted/loved
        if not it["isArchived"] and not it["isLiked"] and url in dismissed:
            continue
        if not it["isArchived"] and not it["isLiked"] and _fam(it) in blocked_fam:
            n_family += 1
            continue
        ig = norm(it.get("gender")) or "men"
        if not it["isArchived"] and not it["isLiked"] and ig != "unisex" and ig != ugender:
            continue                                       # wrong gender for this user — never surface it
        if not it["isArchived"] and not it["isLiked"] and not fits_user(it):
            n_size_retired += 1
            continue
        it["_s1"] = stage1(it)
        it["score"] = adj(it); it["firstSeen"] = it.get("first_seen")
        items.append(it)

    # gated lane: un-acted items from a gated combo/brand must rank in the user's TOP band to
    # surface (stage-1 percentile — logistic scores aren't on the old 0-1 vision scale). Legacy
    # items without attributes keep the old absolute vision gate. Unrated raw finds never gate.
    #
    # brand scope: the catalog is the UNION of every user's loved brands, so it holds brands THIS
    # user never chose (audit 2026-08-04: another user's Amiri/Casablanca surfacing for Charles).
    # Un-acted items from brands outside the user's own wall (loved list + brands they've
    # liked/carted) only surface as GEMS — top-5% of their fitted taste model — honoring
    # "don't miss a gem, judge by taste not brand" without flooding the feed with strangers.
    # Users with no loved list yet (mid-onboarding) keep the full browse. Raw scans stay exempt.
    my_brands = {norm(b) for b in ((taste.get("brands") or {}).get("loved") or []) if b}
    my_brands |= {norm(x.get("brand")) for x in sigs if x["action"] in ("liked", "carted") and x.get("brand")}
    def brand_mine(b):
        nb = norm(b)
        if not nb: return False
        if nb in my_brands: return True
        return any(len(m) > 4 and (m in nb or nb in m) for m in my_brands)  # Rick Owens ~ Rick Owens DRKSHDW
    n_gated_out = 0
    n_foreign_out = 0
    s1_pool = sorted(it["_s1"] for it in items if it["_s1"] is not None and not it["isArchived"] and not it["isLiked"])
    gate_thr = s1_pool[int(0.88 * (len(s1_pool) - 1))] if s1_pool else None
    gem_thr = s1_pool[int(0.95 * (len(s1_pool) - 1))] if s1_pool else None
    kept = []
    for it in items:
        if not it["isArchived"] and not it["isLiked"] and "unrated" not in (it.get("reasons") or []):
            b, cc = norm(it.get("brand")), norm(it.get("category"))
            if my_brands and not brand_mine(it.get("brand")):
                v = it["_s1"]
                if not (v is not None and gem_thr is not None and v >= gem_thr):
                    n_foreign_out += 1
                    continue
            if (b, cc) in gated or b in deep_gated:
                v = it["_s1"]
                passes = (v >= gate_thr) if (v is not None and gate_thr is not None) else (float(it.get("base_score") or 0) >= min(GATE_VISION, GEM_OVERRIDE))
                if not passes:
                    n_gated_out += 1
                    continue
        kept.append(it)
    items = kept

    # ---- stage 2: personal vision re-rank of the visible slice. Same judgment quality as the
    # old per-item pass, but bounded: only the top N un-acted items, and each (user,item) verdict
    # is cached in user_scores so rebuilds never re-judge. This is what keeps quality vision-grade
    # while the per-user cost stays flat as the catalog and user count grow. ----
    vb = taste.get("visualBrief")
    brief = vb if isinstance(vb, str) else (vb.get("brief") if isinstance(vb, dict) and isinstance(vb.get("brief"), str) else (json.dumps(vb)[:4000] if vb else None))
    n_stage2 = 0
    if ANTHROPIC_KEY and brief:
        cache = {r["url"]: (r.get("vfit"), r.get("tags") or []) for r in
                 fetch_all("user_scores", "url,vfit,tags", f"&user_id=eq.{uid}")}
        unacted = [it for it in items if not it["isArchived"] and not it["isLiked"] and "unrated" not in (it.get("reasons") or [])]
        unacted.sort(key=lambda x: -x["score"])
        # active users: the WHOLE in-wall pool gets judged (holdout AUC: vision 0.72 vs stage-1 0.65;
        # verdicts cache forever) — bounded per rebuild by STAGE2_MAX_FRESH, best stage-1 first.
        # dormant users keep the small slice so cost stays flat across the user base.
        since = (datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)).isoformat()
        active_user = any((x.get("created_at") or "") >= since for x in sigs)
        fresh_pool = [it for it in unacted if it["url"] not in cache]
        todo = fresh_pool[:STAGE2_MAX_FRESH] if active_user else fresh_pool[:(STAGE2_TOP_YOUNG if young else STAGE2_TOP)]
        todo_urls = {it["url"] for it in todo}
        top = [it for it in unacted if it["url"] in cache or it["url"] in todo_urls]   # every cached verdict applies, free
        fresh = {}
        if todo:
            from concurrent.futures import ThreadPoolExecutor
            def _vs(it):
                res = taste_model.vision_fit(ANTHROPIC_KEY, it.get("image"), brief)
                if res is None:
                    res = taste_model.vision_fit(ANTHROPIC_KEY, it.get("image"), brief)  # one retry
                if res is not None: fresh[it["url"]] = res
            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(_vs, todo))
            if fresh:
                rows_up = [{"user_id": uid, "url": u, "vfit": round(v, 3), "tags": t} for u, (v, t) in fresh.items()]
                api("POST", "/rest/v1/user_scores?on_conflict=user_id,url", rows_up,
                    {"Prefer": "resolution=merge-duplicates,return=minimal"})
        vnudge = 0.6                                # vision leads once it has looked (backtest: 0.72 vs 0.65)
        for it in top:
            got = fresh.get(it["url"]) or cache.get(it["url"])
            if not got or got[0] is None: continue
            vf, vtags = float(got[0]), got[1]
            it["score"] = round(it["score"] + vnudge * (vf - 0.5), 4)  # vision nudges the visible ranking
            if vtags: it["reasons"] = vtags[:5]                        # per-user why-tags (payload copy only)
            n_stage2 += 1
    for it in items: it.pop("_s1", None)

    latest = max((it.get("firstSeen","") for it in items if not it["isArchived"]), default="")
    for it in items:
        it["isNew"] = (not it["isArchived"]) and bool(latest) and it.get("firstSeen") == latest

    def ri(it):
        return {"id":it.get("id"),"platform":it.get("platform"),"brand":it.get("brand"),"title":it.get("title"),
                "category":it.get("category"),"price":it.get("price"),"size":it.get("size"),"condition":it.get("condition"),
                "image":it.get("image"),"url":it.get("url"),"reasons":it.get("reasons") or [],"score":it["score"],
                "sz":it.get("sz"),"isArchived":it["isArchived"],"isLiked":it["isLiked"],"isCarted":it.get("isCarted",False),"isNew":it["isNew"],
                "similar":it.get("similar",0),"pick":bool(it.get("pick")),
                "steal":bool(it.get("steal")),"stealPct":it.get("steal_pct",0)}
    # ---- assembly (audit 2026-09-03): dedupe -> taste-first order with diversity -> picks ----
    # 1) collapse same brand+title+category listings: 18% of the feed was the same piece in other
    #    sizes/conditions. Best-scored survives and carries `similar` = how many it stands for.
    n_dupes = 0
    groups = {}
    for it in items:
        if it["isArchived"] or it["isLiked"]: continue
        groups.setdefault((norm(it.get("brand")), norm(it.get("title")), it.get("category")), []).append(it)
    drop = set()
    for g in groups.values():
        if len(g) < 2: continue
        g.sort(key=lambda x: -x["score"])
        g[0]["similar"] = len(g) - 1
        for d in g[1:]: drop.add(d["url"]); n_dupes += 1
    items = [it for it in items if it["url"] not in drop]
    # 1b) steals (Charles 2026-09-22): priced at or under STEAL_RATIO of what this brand+category
    #     usually lists for across the catalog (a 46k-item price index — no retail prices scraped
    #     yet), AND in the user's top half by taste. Same gender only; brand medians need >= 5 items.
    pidx = {}
    for r in catalog:
        pr = r.get("price")
        if isinstance(pr, (int, float)) and pr > 0 and (norm(r.get("gender")) or "men") == ugender:
            pidx.setdefault((norm(r.get("brand")), r.get("category")), []).append(pr)
    med = {k: sorted(v)[len(v) // 2] for k, v in pidx.items() if len(v) >= 5}
    live_un = [it for it in items if not it["isArchived"] and not it["isLiked"]]
    sc_sorted = sorted(it["score"] for it in live_un)
    sc_p50 = sc_sorted[len(sc_sorted) // 2] if sc_sorted else 0
    n_steals = 0
    for it in live_un:
        pr = it.get("price")
        if not (isinstance(pr, (int, float)) and pr > 0): continue
        ref = med.get((norm(it.get("brand")), it.get("category")))
        if ref and pr <= STEAL_RATIO * ref and it["score"] >= sc_p50:
            it["steal"] = True; it["steal_pct"] = int(round((1 - pr / ref) * 100)); n_steals += 1
    # 2) order by TASTE, not arrival (was: newest day first, score only broke ties). Greedy
    #    diversity: every repeat of a brand / look-cluster / source already placed costs a little,
    #    so the head of the feed spans the whole taste instead of one black-boxy-nylon cluster.
    def _cluster(it):
        a = it.get("attrs") or {}
        pal, mood = a.get("palette"), a.get("mood")
        return (a.get("silhouette"), pal[0] if isinstance(pal, list) and pal else pal,
                mood[0] if isinstance(mood, list) and mood else mood)
    def diversify(lst, head=150):
        pool = sorted(lst, key=lambda x: -x["score"]); out = []
        seen_b, seen_c, seen_s, seen_p = Counter(), Counter(), Counter(), Counter()
        while pool and len(out) < head:
            best, bi = None, -1
            for i, it in enumerate(pool):
                eff = (it["score"] - 0.05 * seen_b[norm(it.get("brand"))]
                       - 0.03 * seen_c[_cluster(it)] - 0.01 * seen_s[it.get("platform")]
                       - 0.015 * seen_p[_cluster(it)[1]])          # palette repeats: head drifted to 70% black vs 51% of likes
                if best is None or eff > best: best, bi = eff, i
            it = pool.pop(bi); out.append(it)
            seen_b[norm(it.get("brand"))] += 1; seen_c[_cluster(it)] += 1
            seen_s[it.get("platform")] += 1; seen_p[_cluster(it)[1]] += 1
        return out + pool
    ordered = {}
    for key, _t, _s in sections_for(ugender):
        ordered[key] = diversify([i for i in items if i.get("category") == key and not i["isArchived"] and not i["isLiked"]])
    # 3) picks: 40 cards spread across categories — each section gets a share proportional to its
    #    pool (floor 5 when it has items), filled from its diversified head. Scores aren't comparable
    #    across categories (outerwear runs hot), so a global top-40 was 70% jackets.
    PICKS = 40
    sizes = {k: len(v) for k, v in ordered.items() if v}
    tot_live = sum(sizes.values()) or 1
    quota = {k: max(5, round(PICKS * n / tot_live)) for k, n in sizes.items()}
    while sum(quota.values()) > PICKS:                     # trim the biggest section until it fits
        k = max(quota, key=quota.get); quota[k] -= 1
    for k, q in quota.items():
        for it in ordered[k][:min(q, sizes[k])]: it["pick"] = True
    secout = []
    for key, title, sub in sections_for(ugender):
        cat = [it for it in items if it.get("category") == key]
        lik = sorted([i for i in cat if i["isLiked"] and not i["isArchived"]], key=lambda x: -x["score"])
        arc = sorted([i for i in cat if i["isArchived"]], key=lambda x: -x.get("score",0))
        secout.append({"key":key,"title":title,"subtitle":sub,"items":[ri(i) for i in ordered[key]+lik+arc]})
    active = [it for it in items if not it["isArchived"]]
    total = sum(1 for it in active if not it["isLiked"])
    n_liked = sum(1 for it in active if it["isLiked"]); n_arch = sum(1 for it in items if it["isArchived"])
    plat = Counter(it.get("platform","?") for it in active if not it["isLiked"])
    note = ("%d new to review, %d liked, %d archived. %s rotation: %s lead. one filter bar: show (feed / liked / archived / all) "
            "stacks with category, size, price and source. love a piece and it moves to liked; pass it to archived.") % (
            total, n_liked, n_arch, _SEASON, _ORDER[0])
    feed = {"date":TODAY,"runId":TODAY+"-cloud","scanned":len(catalog),
            "platforms":{"grailed":plat.get("grailed",0),"therealreal":plat.get("therealreal",0),"ssense":plat.get("ssense",0)},
            "note":note,"sections":secout}
    api("POST", "/rest/v1/feeds?on_conflict=user_id", [{"user_id":uid,"payload":feed,"built_at":NOW}], {"Prefer":"resolution=merge-duplicates,return=minimal"})

    # taste write-back: promote brands liked >=3x (canonicalize), refresh tallies, never cool a brand
    loved = taste.get("brands", {}).get("loved", []); lset = {norm(x) for x in loved}; promoted = []
    for b, n in liked_brands.items():
        if n >= 3 and b and b not in lset:
            canon = next((it["brand"] for it in catalog if norm(it["brand"]) == b), b)
            loved.append(canon); lset.add(b); promoted.append(canon)
    s = taste.setdefault("signals", {}); s.pop("cooledBrands", None)
    # publish stage-1 weights so the conductor can rank scan results the same way
    if weights:
        s["tasteWeights"] = {k: round(v, 4) for k, v in weights.items()}
        s["brandRates"] = {b: round(brate(b), 3) for b in {norm(c0.get("brand")) for c0 in catalog if c0.get("brand")}}
    s["brandLanes"] = {
        "gatedCombos": sorted(f"{b}|{c}" for b, c in gated),
        "deepGatedBrands": deep_gated,          # conductor: weekly slot on paid sources, grailed unaffected
        "gateVision": GATE_VISION, "computedAt": NOW,
    }
    s["likedBrandTally"] = dict(liked_brands); s["passedBrandTally"] = dict(passed_brands)
    s["lovedStyleTags"] = dict((t,n) for t,n in liked_tags.most_common(60) if is_style(t))
    s["passedStyleTags"] = dict((t,n) for t,n in passed_tags.most_common(60) if is_style(t))
    if soft: s["softPriceCeilingFromPasses"] = soft
    taste.setdefault("brands", {})["loved"] = loved; taste.setdefault("meta", {})["lastUpdated"] = TODAY
    api("PATCH", f"/rest/v1/taste?user_id=eq.{uid}", {"payload": taste}, {"Prefer":"return=minimal"})
    print(f"  user {uid[:8]}: {total} to review, {n_liked} liked, {n_arch} archived | "
          f"stage-2 vision on {n_stage2} top items | {n_gated_out} gated out across {len(gated)} combos, "
          f"deep-gated: {deep_gated} | {n_foreign_out} outside-brand-wall (gems kept) | {n_size_retired} size-retired | {n_dupes} dupes collapsed | {n_family} same-piece hidden | {n_steals} steals"
          + (f" | promoted {promoted}" if promoted else ""))

def main():
    import time as _time
    t0 = _time.time()
    catalog = fetch_all("catalog", "url,id,platform,brand,title,category,price,size,condition,image,reasons,base_score,sz,first_seen,last_seen,attrs,gender")
    users = fetch_all("taste", "user_id,payload")
    print(f"catalog {len(catalog)} items | {len(users)} user(s)")
    for u in users:
        build_for_user(u["user_id"], u.get("payload") or {}, catalog)
    print("REBUILD DONE")
    taste_model.write_run_record(URL, SECRET, "rebuild",
                                 {"users": len(users), "catalog": len(catalog),
                                  "duration_s": round(_time.time() - t0)})

if __name__ == "__main__":
    main()
