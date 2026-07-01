#!/usr/bin/env python3
# Rebuild every user's feed from the shared catalog + their taste + their signals.
# Runs the SAME adj_score as the local build_feed.py (byte-for-byte: +0.30 loved-pin,
# NO brand penalty, style-tag +/-, soft price ceiling). Writes each user's feeds row
# and folds lasting patterns back into their taste. Config from env (Actions) or .env.
import os, sys, re, json, urllib.request, urllib.error
from collections import Counter
from datetime import date, datetime, timezone

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

_SKIP = {"new","gently used","in-size","in size","value","grail","#1 brand","loved brand",
         "your basics brand","grailed","ssense","the realreal","therealreal",
         "xs","s","m","l","xl","xxl","xxxl","os"}
def is_style(t):
    t = norm(t)
    if not t or t in _SKIP: return False
    if re.search(r"size|waist|^us \d|^eu \d|^it \d|^uk \d", t): return False
    return True

SECTIONS = [("outerwear","outerwear","leather, denim, shearling and tech, ranked"),
            ("bottoms","bottoms","36-38 only, leather and wide-leg lead"),
            ("tops","tops + knits","tees, hoodies, knits, shirts"),
            ("footwear","footwear","in-size only, us 13-14 / eu 47-48")]

def build_for_user(uid, taste, catalog):
    sigs = fetch_all("signals", "url,action,brand,category,reasons,price", f"&user_id=eq.{uid}")
    liked_ids, passed_ids = set(), set()
    liked_brands, passed_brands = Counter(), Counter()
    liked_tags, passed_tags = Counter(), Counter()
    passed_prices = []
    for x in sigs:
        if x["action"] == "liked":
            liked_ids.add(x["url"]); liked_brands[norm(x.get("brand"))] += 1
            for r in x.get("reasons") or []: liked_tags[norm(r)] += 1
        else:
            passed_ids.add(x["url"]); passed_brands[norm(x.get("brand"))] += 1
            for r in x.get("reasons") or []: passed_tags[norm(r)] += 1
            if isinstance(x.get("price"), (int, float)): passed_prices.append(x["price"])
    soft = None
    if len(passed_prices) >= 4:
        passed_prices.sort(); soft = passed_prices[len(passed_prices)//4]

    def adj(it):
        s = float(it.get("base_score") or 0)
        url = it["url"]; b = norm(it.get("brand"))
        if url in liked_ids: s += 0.30
        if b in liked_brands: s += min(0.03 * liked_brands[b], 0.09)
        for r in it.get("reasons") or []:
            rn = norm(r)
            if not is_style(rn): continue
            if liked_tags.get(rn):  s += min(0.05 * liked_tags[rn], 0.25)
            if passed_tags.get(rn): s -= min(0.05 * passed_tags[rn], 0.25)
        if soft and isinstance(it.get("price"), (int, float)) and it["price"] > soft * 1.5: s -= 0.06
        return round(s, 4)

    items = []
    for c in catalog:
        it = dict(c); url = it["url"]
        it["isArchived"] = url in passed_ids; it["isLiked"] = url in liked_ids
        it["score"] = adj(it); it["firstSeen"] = it.get("first_seen")
        items.append(it)
    latest = max((it.get("firstSeen","") for it in items if not it["isArchived"]), default="")
    for it in items:
        it["isNew"] = (not it["isArchived"]) and bool(latest) and it.get("firstSeen") == latest

    def ri(it):
        return {"id":it.get("id"),"platform":it.get("platform"),"brand":it.get("brand"),"title":it.get("title"),
                "category":it.get("category"),"price":it.get("price"),"size":it.get("size"),"condition":it.get("condition"),
                "image":it.get("image"),"url":it.get("url"),"reasons":it.get("reasons") or [],"score":it["score"],
                "sz":it.get("sz"),"isArchived":it["isArchived"],"isLiked":it["isLiked"],"isNew":it["isNew"]}
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
    note = ("%d new to review, %d liked, %d archived. one filter bar: show (feed / liked / archived / all) stacks with "
            "category, size, price and source. love a piece and it moves to liked; pass it to archived.") % (total, n_liked, n_arch)
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
    s["likedBrandTally"] = dict(liked_brands); s["passedBrandTally"] = dict(passed_brands)
    s["lovedStyleTags"] = dict((t,n) for t,n in liked_tags.most_common(60) if is_style(t))
    s["passedStyleTags"] = dict((t,n) for t,n in passed_tags.most_common(60) if is_style(t))
    if soft: s["softPriceCeilingFromPasses"] = soft
    taste.setdefault("brands", {})["loved"] = loved; taste.setdefault("meta", {})["lastUpdated"] = TODAY
    api("PATCH", f"/rest/v1/taste?user_id=eq.{uid}", {"payload": taste}, {"Prefer":"return=minimal"})
    print(f"  user {uid[:8]}: {total} to review, {n_liked} liked, {n_arch} archived" + (f" | promoted {promoted}" if promoted else ""))

def main():
    catalog = fetch_all("catalog", "url,id,platform,brand,title,category,price,size,condition,image,reasons,base_score,sz,first_seen,last_seen")
    users = fetch_all("taste", "user_id,payload")
    print(f"catalog {len(catalog)} items | {len(users)} user(s)")
    for u in users:
        build_for_user(u["user_id"], u.get("payload") or {}, catalog)
    print("REBUILD DONE")

if __name__ == "__main__":
    main()
