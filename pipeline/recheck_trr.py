#!/usr/bin/env python3
# Nightly re-check of The RealReal listings that are still LIVE in someone's feed. TRR's GraphQL has no
# bulk filter, but root product(slug:) answers availability and aliases batch 30 lookups per call
# (probe v4, 2026-09-23) — so ~1,500 live pieces cost ~50 Firecrawl calls. SOLD (or vanished) ->
# reasons += "sold" (the feed builder hides it); AVAILABLE -> catalog.last_seen = today, which is
# what "least recently verified" sorts on next time. Heads of feeds (top 150) are checked first.
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY, FIRECRAWL_KEY; RECHECK_MAX (default 1500); DRY=1 selects only
import os, sys, re, json, urllib.parse, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C                    # env loading, http/sb, Firecrawl gate + counters, _trr_json, mark_sold
import taste_model

RECHECK_MAX = int(os.environ.get("RECHECK_MAX", "3000"))   # ~100 Firecrawl calls a night; scans use ~100 a day
BATCH = 30
DRY = os.environ.get("DRY", "") == "1"
TODAY = C.TODAY

def gql(query):
    url = "https://api.therealreal.com/graphql?query=" + urllib.parse.quote(query)
    C._count(C.FIRECRAWL_CALLS)
    with C.FIRECRAWL_SEM:
        st, r = C.http("POST", "https://api.firecrawl.dev/v2/scrape", {"url": url, "formats": ["rawHtml"], "proxy": "stealth"},
                       {"Authorization": "Bearer " + C.FIRE}, timeout=180)
    data = (r.get("data") or {}) if isinstance(r, dict) else {}
    if (data.get("metadata") or {}).get("statusCode") != 200: return None
    return C._trr_json(re.sub(r"<[^>]+>", "", data.get("rawHtml") or ""))

def main():
    t0 = time.time()
    # 1) every TRR listing live in any feed, with its best (lowest) position across users
    pos = {}
    st, feeds = C.sb("GET", "/rest/v1/feeds?select=user_id&order=user_id")
    for row in (feeds if isinstance(feeds, list) else []):
        st1, one = C.sb("GET", f"/rest/v1/feeds?select=payload&user_id=eq.{row['user_id']}")
        if st1 != 200 or not one: continue
        for sec in (one[0].get("payload") or {}).get("sections") or []:
            live = [i for i in sec.get("items") or [] if not i.get("isArchived") and not i.get("isLiked")]
            for idx, it in enumerate(live):
                if it.get("platform") == "therealreal" and it.get("url"):
                    pos[it["url"]] = min(pos.get(it["url"], 10**9), idx)
    # 2) catalog state for those urls: skip ones already marked sold; sort heads first, then least recently verified
    urls = list(pos); rows = {}
    for i in range(0, len(urls), 100):
        chunk = urls[i:i+100]
        st, part = C.sb("GET", "/rest/v1/catalog?select=url,last_seen,reasons&url=in.(" + ",".join(urllib.parse.quote(u, safe="") for u in chunk) + ")")
        for r in (part if isinstance(part, list) else []): rows[r["url"]] = r
    cands = [u for u in urls if u in rows and "sold" not in (rows[u].get("reasons") or [])]
    # budget split: feed heads (top 150 anywhere) get 60%, rotating by least-recently-verified so a
    # head verified last night yields to one that wasn't; the deep pool gets the rest the same way.
    seen = lambda u: rows[u].get("last_seen") or ""
    heads = sorted([u for u in cands if pos[u] < 150], key=lambda u: (seen(u), pos[u]))
    deep  = sorted([u for u in cands if pos[u] >= 150], key=seen)
    n_head = min(len(heads), int(RECHECK_MAX * 0.6)) if deep else min(len(heads), RECHECK_MAX)
    todo = heads[:n_head] + deep[:RECHECK_MAX - n_head]
    print(f"recheck: {len(urls)} TRR listings live across feeds, {len(cands)} not yet marked sold, checking {len(todo)} "
          f"({sum(1 for u in todo if pos[u] < 150)} in feed heads) in {-(-len(todo) // BATCH)} calls")
    if DRY or not todo:
        print("dry run — no calls made" if DRY else "nothing to check"); return
    # 3) aliased batches
    sold, avail, unresolved, failed = [], [], [], 0
    def check(batch):
        slugs = [(u, u.rstrip("/").rsplit("/", 1)[-1]) for u in batch]
        q = "query{" + " ".join(f'p{i}: product(slug:"{sl}"){{availability}}' for i, (u, sl) in enumerate(slugs)) + "}"
        j = gql(q)
        if not j or not isinstance(j.get("data"), dict): return None
        out = []
        for i, (u, sl) in enumerate(slugs):
            v = j["data"].get(f"p{i}")
            out.append((u, (v or {}).get("availability") if isinstance(v, dict) else None))
        return out
    batches = [todo[i:i+BATCH] for i in range(0, len(todo), BATCH)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(check, batches):
            if res is None: failed += 1; continue
            for u, a in res:
                if a == "AVAILABLE": avail.append(u)
                elif a is None: unresolved.append(u)      # slug no longer resolves: listing removed -> treat as gone
                else: sold.append(u)                        # SOLD (or any other non-available state)
    # 4) write back
    if avail and not DRY:
        for i in range(0, len(avail), 50):
            chunk = avail[i:i+50]
            C.sb("PATCH", "/rest/v1/catalog?url=in.(" + ",".join(urllib.parse.quote(u, safe="") for u in chunk) + ")",
                 {"last_seen": TODAY}, {"Prefer": "return=minimal", "Content-Type": "application/json"})
    n_marked = C.mark_sold(sorted(set(sold + unresolved))) if (sold or unresolved) else 0
    print(f"recheck done: {len(avail)} available, {len(sold)} sold, {len(unresolved)} vanished -> {n_marked} newly marked sold; "
          f"{failed} failed batch(es); {C.FIRECRAWL_CALLS[0]} firecrawl calls; {round(time.time() - t0)}s")
    taste_model.write_run_record(C.SB_URL, C.SB_SECRET, "recheck",
        {"checked": len(todo), "available": len(avail), "sold": len(sold), "vanished": len(unresolved),
         "failed_batches": failed, "firecrawl_calls": C.FIRECRAWL_CALLS[0], "duration_s": round(time.time() - t0)})

if __name__ == "__main__":
    main()
