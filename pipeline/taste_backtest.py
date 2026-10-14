#!/usr/bin/env python3
# Held-out backtest of the feed ranking, PRODUCTION-FAITHFUL (audit 2026-10-14). This is the gate
# for any model change: it must beat the numbers printed here before it ships.
#
# Train on everything before CUTOFF (taps at full weight, feed clears at CLEAR_WEIGHT, prior
# blend, per-category models) and test on what came after: the user's likes vs every piece they
# passed OR cleared among items that entered the catalog after the cutoff (clears carry no
# timestamp, so first_seen is the honest split). Reports AUC, precision at the top, and the
# number that matters for a 300-card feed: how many of the held-out likes would have been shown.
#
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY   optional: BT_USER (uuid), BT_CUTOFF (YYYY-MM-DD), BT_FEED (300)
import os, sys, json, re, bisect, time, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taste_model as TM

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
URL = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SECRET_KEY"]
UID = os.environ.get("BT_USER", "72fc955c-3832-409e-ad43-622d2546e586")
CUTOFF = os.environ.get("BT_CUTOFF", "2026-08-20")
FEED = int(os.environ.get("BT_FEED", "300"))
CLEAR_WEIGHT, PERCAT_MIN, HEAD_VISION, VNUDGE = 0.5, 60, 300, 0.6     # keep in step with build_feed_cloud

def get(path):
    for t in range(3):
        try:
            r = urllib.request.Request(URL + path, headers={"apikey": KEY, "Authorization": "Bearer " + KEY})
            with urllib.request.urlopen(r, timeout=120) as resp: return json.loads(resp.read().decode())
        except Exception:
            if t == 2: raise
            time.sleep(3)

def fetch_all(path):
    rows, start = [], 0
    while True:
        part = get(f"{path}&limit=1000&offset={start}"); rows += part
        if len(part) < 1000: break
        start += 1000
    return rows

def norm(s): return (s or "").strip().lower()
def auc(pairs):
    pos = sorted(s for s, l in pairs if l == 1); neg = sorted(s for s, l in pairs if l == 0)
    if not pos or not neg: return float("nan")
    w = t = 0
    for s in pos:
        lo = bisect.bisect_left(neg, s); hi = bisect.bisect_right(neg, s); w += lo; t += hi - lo
    return (w + 0.5 * t) / (len(pos) * len(neg))
def prec_at(pairs, k):
    ps = sorted(pairs, key=lambda p: -p[0])[:k]; return sum(l for _, l in ps) / max(1, len(ps))

def main():
    t0 = time.time()
    sig = fetch_all(f"/rest/v1/signals?select=url,action,created_at,reasons&user_id=eq.{UID}&order=created_at.asc")
    taste = get(f"/rest/v1/taste?select=payload&user_id=eq.{UID}")[0]["payload"]
    gender = norm(taste.get("gender")) or "men"
    dismissed = set((taste.get("signals") or {}).get("dismissedUrls") or [])
    acted = {s["url"] for s in sig}
    urls = sorted(acted | dismissed); cat = {}
    for i in range(0, len(urls), 80):
        q = "/rest/v1/catalog?select=url,brand,price,category,gender,attrs,base_score,first_seen&url=in.(" + ",".join('"' + u + '"' for u in urls[i:i+80]) + ")"
        for r in get(q): cat[r["url"]] = r
    vs = {r["url"]: r.get("vfit") for r in fetch_all(f"/rest/v1/user_scores?select=url,vfit&user_id=eq.{UID}&order=url")}
    def ok(c): return bool(c and c.get("attrs")) and (c.get("gender") or "men") in (gender, "unisex") and (gender == "women" or c.get("category") not in ("dresses", "skirts"))
    train, test = [], []
    for s in sig:
        c = cat.get(s["url"])
        if not ok(c): continue
        lab = 1 if s["action"] in ("liked", "carted") else 0
        c2 = dict(c)
        if "deck" in (s.get("reasons") or []): c2["price"] = None; c2["brand"] = ""
        (train if s["created_at"] < CUTOFF else test).append((lab, c2, 1.0))
    for u in dismissed:
        if u in acted: continue
        c = cat.get(u)
        if not ok(c): continue
        (train if (c.get("first_seen") or "") < CUTOFF else test).append((0, c, CLEAR_WEIGHT if (c.get("first_seen") or "") < CUTOFF else 1.0))
    npos = sum(l for l, _, _ in test)
    print(f"user {UID[:8]} | cutoff {CUTOFF} | train {len(train)} rows | test {len(test)} rows, {npos} likes | loaded in {time.time()-t0:.0f}s")
    if npos < 20: print("fewer than 20 held-out likes — move the cutoff earlier"); return
    PRIOR = TM.load_prior()

    def fit(rows):
        w, pairs = TM.fit_user_weights([(l, c, wt) for l, c, wt in rows], prior=PRIOR)
        rate = TM.brand_rates(pairs) if pairs else (lambda b: 0.25)
        return lambda c: TM.predict(w, TM.featurize(c["attrs"], c.get("brand"), c.get("price"), c.get("category"), rate(c.get("brand"))))
    g = fit(train)
    s1 = {c["url"]: g(c) for _, c, _ in test}                           # global stage-1 (the old production)
    blend = dict(s1)
    for k in ("outerwear", "tops", "bottoms", "footwear", "dresses", "skirts"):
        rows_k = [t for t in train if t[1].get("category") == k]
        if len(rows_k) < PERCAT_MIN: continue
        gk = fit(rows_k)
        for _, c, _ in test:
            if c.get("category") == k: blend[c["url"]] = 0.5 * s1[c["url"]] + 0.5 * gk(c)
    head = sorted(blend, key=lambda u: -blend[u])[:HEAD_VISION]
    prod = dict(blend)
    for u in head:
        if vs.get(u) is not None: prod[u] = blend[u] + VNUDGE * (vs[u] - 0.5)

    def report(name, sc):
        pr = [(sc[c["url"]], l) for l, c, _ in test]
        rank = sorted(pr, key=lambda x: -x[0]); pos = [i for i, (s, l) in enumerate(rank) if l == 1]
        bycat = {k: round(auc([(sc[c["url"]], l) for l, c, _ in test if c.get("category") == k]), 3) for k in ("outerwear", "tops", "bottoms", "footwear")}
        caught = sum(l for _, l in rank[:FEED])
        print(f"{name:44s} AUC {auc(pr):.3f} | P@50 {prec_at(pr, 50):4.0%} | P@100 {prec_at(pr, 100):4.0%} | likes in a {FEED}-card feed {caught}/{npos} | median like rank {sorted(pos)[len(pos)//2]} | {bycat}")
    print()
    report("stage-1 global only (pre-audit production)", s1)
    report("+ per-category blend", blend)
    report("+ vision on the head  (CURRENT PRODUCTION)", prod)
    vis = {u: (vs[u] if vs.get(u) is not None else 0.5) for u in s1}
    report("vision verdicts alone (cached, 0.5 where none)", vis)
    print(f"\ndone in {time.time()-t0:.0f}s — a change ships only if it beats CURRENT PRODUCTION on likes-in-feed without losing AUC.")

if __name__ == "__main__":
    main()
