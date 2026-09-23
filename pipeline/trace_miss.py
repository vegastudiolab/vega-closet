#!/usr/bin/env python3
# "Why isn't this in my feed?" — trace one or more listing URLs through every gate the feed
# builder applies, in the order it applies them, and name the first one that drops the piece.
#
#   python3 pipeline/trace_miss.py URL [URL ...]      (env: SUPABASE_URL, SUPABASE_SECRET_KEY)
#   optional: USER_ID=<uuid> to trace for another user (default: Charles)
import os, sys, re, json, urllib.request, urllib.parse
from collections import Counter

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
U = os.environ["SUPABASE_URL"].rstrip("/"); K = os.environ["SUPABASE_SECRET_KEY"]
UID = os.environ.get("USER_ID", "72fc955c-3832-409e-ad43-622d2546e586")
H = {"apikey": K, "Authorization": "Bearer " + K}
norm = lambda s: (s or "").strip().lower()

def get(path, rng=None):
    h = dict(H)
    if rng: h.update({"Range-Unit": "items", "Range": rng})
    return json.load(urllib.request.urlopen(urllib.request.Request(U + path, headers=h)))

def fetch_all(table, sel, qs=""):
    rows, start = [], 0
    while True:
        part = get(f"/rest/v1/{table}?select={sel}{qs}", f"{start}-{start+999}")
        rows += part
        if len(part) < 1000: break
        start += 1000
    return rows

def _rx(tokens):
    toks = sorted({norm(t) for t in tokens if t}, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in toks) + r")\b") if toks else None

def main(urls):
    taste = get(f"/rest/v1/taste?select=payload&user_id=eq.{UID}")[0]["payload"]
    sigs = fetch_all("signals", "url,action,brand,category,created_at", f"&user_id=eq.{UID}")
    feed = get(f"/rest/v1/feeds?select=payload,built_at&user_id=eq.{UID}")[0]
    ugender = norm(taste.get("gender")) or "men"
    usz = taste.get("sizes") or {}
    loved = {norm(b) for b in ((taste.get("brands") or {}).get("loved") or [])}
    loved |= {norm(x["brand"]) for x in sigs if x["action"] in ("liked", "carted") and x.get("brand")}
    dismissed = set((taste.get("signals") or {}).get("dismissedUrls") or [])
    by_url = {x["url"]: x for x in sigs}
    passed = {x["url"] for x in sigs if x["action"] == "passed"}
    # lanes, as the builder computes them
    bc, base = {}, sum(1 for x in sigs if x["action"] in ("liked", "carted")) / max(1, len(sigs))
    for x in sigs:
        st = bc.setdefault((norm(x.get("brand")), norm(x.get("category"))), [0, 0]); st[1] += 1
        if x["action"] in ("liked", "carted"): st[0] += 1
    sm = lambda l, n: (l + 8 * base) / (n + 8)
    gated = {k for k, (l, n) in bc.items() if n >= 15 and sm(l, n) < 0.10}
    bstat = {}
    for (b, _), (l, n) in bc.items():
        st = bstat.setdefault(b, [0, 0]); st[0] += l; st[1] += n
    deep = {b for b, (l, n) in bstat.items() if b and n >= 15 and sm(l, n) < 0.10}
    rx = {"tops": _rx(usz.get("tops") or []), "outerwear": _rx(usz.get("outerwear") or usz.get("tops") or []),
          "waist": _rx(usz.get("waist") or []), "shoes": _rx(usz.get("shoes") or [])}
    feed_items = [(s["key"], i, it) for s in feed["payload"]["sections"] for i, it in enumerate(s["items"])]
    feed_by_url = {it["url"]: (k, i, it) for k, i, it in feed_items}
    print(f"user {UID[:8]} · {ugender} · wall {len(loved)} brands · feed built {feed['built_at'][:16]}\n")

    for raw in urls:
        url = raw.split("?")[0].strip()
        print("=" * 78); print(url)
        rows = get(f"/rest/v1/catalog?select=url,brand,title,category,gender,size,sz,price,first_seen,attrs&url=eq.{urllib.parse.quote(url, safe='')}")
        if not rows:
            plat = "grailed" if "grailed.com" in url else "therealreal" if "therealreal.com" in url else "ssense" if "ssense.com" in url else "?"
            print(f"  NOT IN CATALOG — never stored. Either never scraped, or scraped and rejected before insert")
            print(f"  (wrong category, off-size for every user, blazer rule, or no usable image).")
            print(f"  source: {plat}.", {"grailed": "grailed scans 24 of the wall's brands per day (each brand every ~3-4 days) — a piece that sells fast can be missed entirely; is the brand on your wall?",
                                          "therealreal": "TRR pulls only the newest 2 pages per category per run — deep listings never surface.",
                                          "ssense": "ssense pulls 6 pieces per brand per day."}.get(plat, ""))
            continue
        r = rows[0]
        print(f"  in catalog since {r['first_seen']} · {r['brand']} · {r['title'][:60]} · {r['category']} · {r['gender']} · size '{r['size']}' (bucket {r['sz']}) · ${r['price']} · attrs {'yes' if r.get('attrs') else 'NO (unranked by your model)'}")
        # gates, builder order
        if url in by_url:
            x = by_url[url]; print(f"  -> you already acted on it: {x['action'].upper()} on {x['created_at'][:10]}"); continue
        if url in dismissed:
            print("  -> you CLEARED it (dismissed). Cleared stays cleared."); continue
        g = norm(r.get("gender")) or "men"
        if g not in ("unisex", ugender):
            print(f"  -> DROPPED by gender gate: tagged {g}, you're {ugender}"); continue
        cat, s = r["category"], norm(r.get("size"))
        key = "shoes" if cat == "footwear" else "waist" if cat in ("bottoms", "skirts") else "outerwear" if cat == "outerwear" else "tops"
        ok = bool(s and rx[key] and rx[key].search(s))
        if not ok and cat == "footwear":
            for e in usz.get("exceptions") or []:
                if e.get("category") == cat and norm(e.get("brand")) in norm(r.get("brand")) and _rx(e.get("add") or []).search(s): ok = True
        if not ok:
            print(f"  -> DROPPED by size gate: size '{r['size']}' vs your {key} sizes {usz.get('outerwear' if key=='outerwear' and usz.get('outerwear') else key)}"); continue
        fam = (norm(r["brand"]), norm(r["title"]), norm(r.get("sz")) or s)
        blockers = [u for u in dismissed | passed if u != url]
        fam_hit = None
        if blockers:
            chunk = get(f"/rest/v1/catalog?select=url,brand,title,sz,size&brand=eq.{urllib.parse.quote(r['brand'], safe='')}&title=eq.{urllib.parse.quote(r['title'], safe='')}")
            for c in chunk:
                if c["url"] != url and c["url"] in (dismissed | passed) and (norm(c.get("sz")) or norm(c.get("size"))) == fam[2]:
                    fam_hit = c["url"]; break
        if fam_hit:
            print(f"  -> HIDDEN as the same piece in the same size as one you cleared/passed: {fam_hit}"); continue
        b = norm(r["brand"])
        on_wall = b in loved or any(len(m) > 4 and (m in b or b in m) for m in loved)
        if not on_wall:
            print(f"  -> OUTSIDE your brand wall ({r['brand']}): only surfaces as a top-5% gem for your model. Add the house in HOUSES to see it normally."); continue
        if (b, norm(cat)) in gated or b in deep:
            l, n = bc.get((b, norm(cat)), bstat.get(b, [0, 0]))
            print(f"  -> GATED lane: {r['brand']} x {cat} like-rate {sm(l,n):.0%} over {n} taps — needs a top-12% stage-1 score to surface."); continue
        if url in feed_by_url:
            k, i, it = feed_by_url[url]
            flags = [f for f, on in (("new", it.get("isNew")), ("pick", it.get("pick")), ("steal", it.get("steal"))) if on]
            print(f"  -> IT IS IN YOUR FEED: {k} section, position #{i+1}, score {it.get('score')}{' · ' + ', '.join(flags) if flags else ''}{' · +%d more listed' % it['similar'] if it.get('similar') else ''}")
            continue
        twin = next((it for k, i, it in feed_items if norm(it.get('brand')) == b and norm(it.get('title')) == norm(r['title']) and it.get('category') == cat), None)
        if twin:
            print(f"  -> passes every gate; COLLAPSED under the same piece's best listing: {twin['url']} (shown with '+{twin.get('similar',0)} more listed')"); continue
        if r["first_seen"] > feed["built_at"][:10]:
            print(f"  -> passes every gate; landed {r['first_seen']}, AFTER the feed was last built ({feed['built_at'][:10]}). Next rebuild picks it up."); continue
        print("  -> passes every gate I can replay; if it's not in the feed the cause is in the vision/diversity ordering — flag it and I'll dig.")

if __name__ == "__main__":
    if len(sys.argv) < 2: print(__doc__ or "usage: trace_miss.py URL [URL ...]"); sys.exit(1)
    main(sys.argv[1:])
