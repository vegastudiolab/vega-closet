#!/usr/bin/env python3
# Curate THE DECK — the onboarding calibration cards — from the attributed catalog.
# Objective: maximum coverage of the feature space taste_model.featurize() actually fits on,
# so every swipe teaches the model something new. Output: web/deck.json (static, pre-auth,
# CDN-cached by Pages). Regenerated weekly (deck.yml) so dead listings rotate out.
#
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY
import os, json, re, urllib.request
import taste_model

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
SB = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_SECRET_KEY"]
POOL_SIZE = 120
QUOTA_PER_VALUE = 3

def fetch_all(table, select, qs=""):
    rows, start = [], 0
    while True:
        req = urllib.request.Request(f"{SB}/rest/v1/{table}?select={select}{qs}&limit=1000&offset={start}",
                                     headers={"apikey": KEY, "Authorization": "Bearer " + KEY})
        with urllib.request.urlopen(req, timeout=60) as r:
            part = json.loads(r.read().decode())
        rows += part
        if len(part) < 1000: break
        start += 1000
    return rows

def legible(c):
    """Heuristic legibility: clean single pieces read fast; ambiguous ones waste a swipe."""
    a = c.get("attrs") or {}
    sil = a.get("silhouette")
    if isinstance(sil, list): sil = sil[0] if sil else None
    if not sil or sil == "na": return False
    if not (c.get("image") or "").startswith("http"): return False
    if not isinstance(c.get("price"), (int, float)) or c["price"] <= 0: return False
    t = (c.get("title") or "").lower()
    if len(t) < 8: return False
    if re.search(r"\b(lot of|bundle|x2|2 pack|read desc|damaged|flaw)\b", t): return False
    return True

def main():
    catalog = [c for c in fetch_all("catalog", "url,brand,title,price,category,image,attrs", "&attrs=not.is.null") if legible(c)]
    print(f"eligible after legibility: {len(catalog)}")

    # feature vocabulary each item covers (the same space the model fits on)
    def keys(c):
        f = taste_model.featurize(c["attrs"], c.get("brand"), c.get("price"), c.get("category"), 0.25)
        ks = {k for k in f if "=" in k}
        a = c["attrs"]
        try: ks.add("stmt_band=" + {1:"lo",2:"lo",3:"mid",4:"hi",5:"hi"}[int(a.get("statement") or 3)])
        except Exception: pass
        return ks

    feats = {c["url"]: keys(c) for c in catalog}
    need = {}
    for ks in feats.values():
        for k in ks: need[k] = QUOTA_PER_VALUE

    # greedy max-coverage: pick the item that fills the most still-open quota; cap 2 per brand,
    # keep category mix from collapsing into tops (the catalog is tops-heavy)
    pool, brand_ct, cat_ct = [], {}, {}
    cat_cap = {"tops": int(POOL_SIZE * 0.32), "outerwear": int(POOL_SIZE * 0.28),
               "bottoms": int(POOL_SIZE * 0.22), "footwear": int(POOL_SIZE * 0.12),
               "accessories": int(POOL_SIZE * 0.06)}
    remaining = list(catalog)
    while len(pool) < POOL_SIZE and remaining:
        best, best_gain = None, -1
        for c in remaining:
            b = (c.get("brand") or "").lower(); cat = c.get("category")
            if brand_ct.get(b, 0) >= 2 or cat_ct.get(cat, 0) >= cat_cap.get(cat, 0): continue
            gain = sum(1 for k in feats[c["url"]] if need.get(k, 0) > 0)
            if gain > best_gain: best, best_gain = c, gain
        if best is None: break
        pool.append(best); remaining.remove(best)
        brand_ct[(best.get("brand") or "").lower()] = brand_ct.get((best.get("brand") or "").lower(), 0) + 1
        cat_ct[best.get("category")] = cat_ct.get(best.get("category"), 0) + 1
        for k in feats[best["url"]]:
            if need.get(k, 0) > 0: need[k] -= 1

    covered = sum(1 for v in need.values() if v < QUOTA_PER_VALUE)
    print(f"pool: {len(pool)} items | feature values touched: {covered}/{len(need)}")

    # the card payload: what the client needs to render + compute reads/nucleus locally.
    # brand is INCLUDED (signals rows want it) but never shown on-card.
    def slim(a):
        def one(v): return (v[0] if isinstance(v, list) and v else v) or None
        return {"silhouette": one(a.get("silhouette")), "mood": (a.get("mood") or [])[:2],
                "palette": (a.get("palette") or [])[:2], "materials": (a.get("materials") or [])[:2],
                "branding": one(a.get("branding")), "weight": one(a.get("weight")),
                "statement": a.get("statement") or 3}
    cards = [{"url": c["url"], "image": c["image"], "title": c["title"], "brand": c.get("brand"),
              "price": c.get("price"), "category": c.get("category"), "attrs": slim(c["attrs"])}
             for c in pool]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "deck.json")
    json.dump({"built": True, "cards": cards}, open(out, "w"))
    print(f"wrote {out} ({os.path.getsize(out)//1024} KB)")

if __name__ == "__main__":
    main()
