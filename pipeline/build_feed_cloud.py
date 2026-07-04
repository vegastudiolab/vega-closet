#!/usr/bin/env python3
# Rebuild every user's feed from the shared catalog + their taste + their signals.
# Runs the SAME adj_score as the local build_feed.py (byte-for-byte: +0.30 loved-pin,
# NO brand penalty, style-tag +/-, soft price ceiling). Writes each user's feeds row
# and folds lasting patterns back into their taste. Config from env (Actions) or .env.
import os, sys, re, json, urllib.request, urllib.error
from collections import Counter
from datetime import date, datetime, timezone
import taste_model

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
STAGE2_TOP = 60          # personal vision re-ranks only the slice that reaches the top of the feed
STAGE2_TOP_YOUNG = 90    # young models (<MATURE_TAPS) lean harder on the rubric: wider slice, stronger nudge
MATURE_TAPS = 300        # above this, the user's own fitted weights carry; below, prior + vision carry more
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

def fetch_all(table, select, extra_qs=""):
    rows = []; start = 0; step = 1000
    while True:
        st, part = api("GET", f"/rest/v1/{table}?select={select}{extra_qs}", None,
                       {"Range-Unit": "items", "Range": f"{start}-{start+step-1}"})
        if st not in (200, 206): print("fetch fail", table, st, str(part)[:200]); sys.exit(1)
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
    "bottoms":    ("bottoms","36-38 only, leather and wide-leg lead"),
    "tops":       ("tops + knits","tees, hoodies, knits, shirts"),
    "footwear":   ("footwear","in-size only, us 14-15 / eu 47-48 (+ balenciaga 46)"),
    "accessories":("accessories","belts in your waist, plus one-size pieces — from accessories scans"),
}
# seasonal section order: summer/spring lead with tops, light layers still welcome;
# fall/winter put outerwear back on top
_SEASON = {12:"winter",1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
           6:"summer",7:"summer",8:"summer",9:"fall",10:"fall",11:"fall"}[date.today().month]
_ORDER = {
    "summer": ["tops","bottoms","footwear","outerwear","accessories"],
    "spring": ["tops","outerwear","bottoms","footwear","accessories"],
    "fall":   ["outerwear","tops","bottoms","footwear","accessories"],
    "winter": ["outerwear","tops","bottoms","footwear","accessories"],
}[_SEASON]
SECTIONS = [(k,) + _ALL_SECTIONS[k] for k in _ORDER]
_HEAVY = re.compile(r"shearling|puffer|parka|\bdown\b|\bfur\b|overcoat|heavy ?wool|fleece|quilted", re.I)
_LIGHT = re.compile(r"\blight|windbreaker|coach|track|nylon|denim jacket|shirt jacket|overshirt|\bvest\b|mesh|linen|short ?sleeve|\btank\b|\btee\b", re.I)

GATE_MIN_TAPS  = 15    # a brand-x-category combo needs this many decisions before it can be gated
GATE_MAX_RATE  = 0.10  # smoothed like-rate below this -> gated lane
GATE_VISION    = 0.70  # gated items need at least this vision score to reach the feed
GEM_OVERRIDE   = 0.85  # anything scoring this high always shows, from any brand
PRIOR_TAPS     = 8     # bayesian smoothing anchor so small samples don't over-trigger

def build_for_user(uid, taste, catalog):
    sigs = fetch_all("signals", "url,action,brand,category,reasons,price", f"&user_id=eq.{uid}")
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

    # ---- stage 1: fit THIS user's taste weights over the shared item attributes ----
    cat_by_url = {c["url"]: c for c in catalog}
    labeled = []
    for x in sigs:
        c0 = cat_by_url.get(x["url"])
        if c0 and c0.get("attrs"):
            if "deck" in (x.get("reasons") or []):
                # onboarding deck hid brand + price — those features would learn noise
                c0 = dict(c0); c0["price"] = None; c0["brand"] = ""
            labeled.append((1 if x["action"] in ("liked", "carted") else 0, c0))
    # prior-anchored fit: works at ANY history size — pure house-prior at 0 taps, personal as they grow
    weights, pairs = taste_model.fit_user_weights(labeled, prior=PRIOR)
    brate = taste_model.brand_rates(pairs) if pairs else (lambda b: 0.25)
    young = len(labeled) < MATURE_TAPS
    lam = len(labeled) / (len(labeled) + 150)
    print(f"  stage-1 weights: {len(labeled)} taps, lambda {lam:.2f} personal ({'young' if young else 'mature'} model, {len(weights)} features)")

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
        # seasonal nudge (summer/spring): heavy winterwear steps back, LA-weight layers step up.
        # small on purpose — a grail shearling still surfaces, it just doesn't dominate July.
        if _SEASON in ("summer", "spring"):
            t = (it.get("title") or "") + " " + " ".join(it.get("reasons") or [])
            if _HEAVY.search(t): s -= 0.08
            elif _LIGHT.search(t): s += 0.04
        return round(s, 4)

    # THE hard promise: nothing that doesn't fit THIS user ever reaches their feed. Sizes are data
    # (taste.payload.sizes) — the shared catalog holds the union of everyone's sizes; this filter
    # cuts it down to one body. Users without sizes yet (mid-onboarding) see everything.
    usz = (taste.get("sizes") or {})
    def _rx(tokens):
        toks = sorted({norm(t) for t in tokens if t}, key=len, reverse=True)
        return re.compile(r"\b(" + "|".join(re.escape(t) for t in toks) + r")\b") if toks else None
    rx_tops, rx_waist = _rx(usz.get("tops") or []), _rx(usz.get("waist") or [])
    rx_shoes = _rx(usz.get("shoes") or [])
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
        if cat == "bottoms":  return bool(rx_waist and rx_waist.search(s))
        return bool(rx_tops and rx_tops.search(s))         # tops / outerwear

    # urls Charles cleared without judging (bad scan batches etc.) — hidden from the feed,
    # NEVER fed into taste learning (a dismissal is "not now", not "not my style")
    dismissed = set((taste.get("signals", {}) or {}).get("dismissedUrls") or [])

    items = []
    n_size_retired = 0
    for c in catalog:
        it = dict(c); url = it["url"]
        it["isCarted"] = url in carted_ids
        it["isArchived"] = url in passed_ids
        it["isLiked"] = url in liked_ids or it["isCarted"]      # carted counts as acted/loved
        if not it["isArchived"] and not it["isLiked"] and url in dismissed:
            continue
        if not it["isArchived"] and not it["isLiked"] and not fits_user(it):
            n_size_retired += 1
            continue
        it["_s1"] = stage1(it)
        it["score"] = adj(it); it["firstSeen"] = it.get("first_seen")
        items.append(it)

    # gated lane: un-acted items from a gated combo/brand must rank in the user's TOP band to
    # surface (stage-1 percentile — logistic scores aren't on the old 0-1 vision scale). Legacy
    # items without attributes keep the old absolute vision gate. Unrated raw finds never gate.
    n_gated_out = 0
    s1_pool = sorted(it["_s1"] for it in items if it["_s1"] is not None and not it["isArchived"] and not it["isLiked"])
    gate_thr = s1_pool[int(0.88 * (len(s1_pool) - 1))] if s1_pool else None
    kept = []
    for it in items:
        if not it["isArchived"] and not it["isLiked"] and "unrated" not in (it.get("reasons") or []):
            b, cc = norm(it.get("brand")), norm(it.get("category"))
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
        top = unacted[:STAGE2_TOP_YOUNG if young else STAGE2_TOP]
        todo = [it for it in top if it["url"] not in cache]
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
        vnudge = 0.6 if young else 0.35            # the rubric carries a young model's ranking
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
                "sz":it.get("sz"),"isArchived":it["isArchived"],"isLiked":it["isLiked"],"isCarted":it.get("isCarted",False),"isNew":it["isNew"]}
    secout = []
    for key, title, sub in SECTIONS:
        cat = [it for it in items if it.get("category") == key]
        act = sorted([i for i in cat if not i["isArchived"]], key=lambda x: (x.get("firstSeen",""), x.get("score",0)), reverse=True)
        arc = sorted([i for i in cat if i["isArchived"]], key=lambda x: -x.get("score",0))
        secout.append({"key":key,"title":title,"subtitle":sub,"items":[ri(i) for i in act+arc]})
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
          f"deep-gated: {deep_gated} | {n_size_retired} size-retired"
          + (f" | promoted {promoted}" if promoted else ""))

def main():
    catalog = fetch_all("catalog", "url,id,platform,brand,title,category,price,size,condition,image,reasons,base_score,sz,first_seen,last_seen,attrs")
    users = fetch_all("taste", "user_id,payload")
    print(f"catalog {len(catalog)} items | {len(users)} user(s)")
    for u in users:
        build_for_user(u["user_id"], u.get("payload") or {}, catalog)
    print("REBUILD DONE")

if __name__ == "__main__":
    main()
