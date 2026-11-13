#!/usr/bin/env python3
# Image fingerprints for the image-similarity ranker (2026-11-13).
# For every ACTIVE user (a tap in the last 14 days) — or just REBUILD_USER on a manual scan — embed:
#   1) their rated history (likes, carts, passes, the most recent CLEARS) = the training set
#   2) their live candidate pool: in their gender + sizes, not sold, first seen in the last POOL_DAYS
# with CLIP ViT-B-32 (laion2b), L2-normalized, stored float16/base64 in public.clip_emb. Training rows
# first, then the pool newest-first, capped at EMBED_MAX per run. Already-embedded urls are skipped,
# so after the first backfill a night is just the day's new pieces.
#
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY   optional: EMBED_MAX (4000), POOL_DAYS (45), CLEARS_MAX (6000), REBUILD_USER, DRY=1
import os, sys, io, json, time, base64, urllib.request, urllib.parse, concurrent.futures as cf
from datetime import datetime, timezone, timedelta, date
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import conductor as C
import taste_model

EMBED_MAX = int(os.environ.get("EMBED_MAX", "4000"))
POOL_DAYS = int(os.environ.get("POOL_DAYS", "45"))
CLEARS_MAX = int(os.environ.get("CLEARS_MAX", "6000"))
ONLY = os.environ.get("REBUILD_USER", "").strip()
DRY = bool(os.environ.get("DRY", "").strip())
T0 = time.time()
def log(*a): print(f"[{time.time()-T0:5.0f}s]", *a, flush=True)

def paged(path):
    out, start = [], 0
    while True:
        st, part = C.sb("GET", f"{path}&limit=1000&offset={start}")
        if st != 200 or not isinstance(part, list): raise SystemExit(f"fetch failed {st} {str(part)[:160]}")
        out += part
        if len(part) < 1000: return out
        start += 1000

def by_urls(select, urls, table="catalog"):
    out = []
    for i in range(0, len(urls), 80):
        q = f"/rest/v1/{table}?select={select}&url=in.(" + ",".join(urllib.parse.quote(u, safe="") for u in urls[i:i+80]) + ")"
        st, part = C.sb("GET", q)
        if st == 200 and isinstance(part, list): out += part
    return out

def main():
    st, taste_rows = C.sb("GET", "/rest/v1/taste?select=user_id,payload")
    since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    st, recent = C.sb("GET", f"/rest/v1/signals?select=user_id&created_at=gte.{urllib.parse.quote(since)}&limit=20000")
    active = {r["user_id"] for r in recent} if isinstance(recent, list) else set()
    users = [t for t in taste_rows if (t["user_id"] == ONLY if ONLY else t["user_id"] in active)]
    log(f"users: {[u['user_id'][:8] for u in users]}")
    have = {r["url"] for r in paged("/rest/v1/clip_emb?select=url&order=url")}
    log(f"already embedded: {len(have)}")
    train, pool = [], []
    cutoff = (date.today() - timedelta(days=POOL_DAYS)).isoformat()
    recent_cat = None
    for t in users:
        uid, p = t["user_id"], (t.get("payload") or {})
        sig = paged(f"/rest/v1/signals?select=url&user_id=eq.{uid}&order=url")
        clears = ((p.get("signals") or {}).get("dismissedUrls") or [])[-CLEARS_MAX:]
        train += [r["url"] for r in sig] + list(clears)
        # live pool: this user's gender + sizes, recent, not sold
        if recent_cat is None:
            recent_cat = paged(f"/rest/v1/catalog?select=url,gender,category,size,brand,reasons,first_seen&first_seen=gte.{cutoff}&order=url")
        reach = C.active_reach([t], days=36500) or []
        acted = {r["url"] for r in sig} | set((p.get("signals") or {}).get("dismissedUrls") or [])
        mine = [r for r in recent_cat if r["url"] not in acted and "sold" not in (r.get("reasons") or []) and any(f(r) for f in reach)]
        mine.sort(key=lambda r: r.get("first_seen") or "", reverse=True)
        pool += [r["url"] for r in mine]
        log(f"  {uid[:8]}: training {len(sig)} taps + {len(clears)} clears | live pool {len(mine)}")
    todo = list(dict.fromkeys([u for u in train + pool if u not in have]))[:EMBED_MAX]
    log(f"to embed this run: {len(todo)} (cap {EMBED_MAX})")
    if DRY or not todo: return taste_model.write_run_record(C.SB_URL, C.SB_SECRET, "embed", {"embedded": 0, "todo": len(todo), "duration_s": round(time.time()-T0)}) if not DRY else None
    img = {r["url"]: r.get("image") for r in by_urls("url,image", todo)}

    import torch, open_clip
    from PIL import Image
    torch.set_num_threads(max(1, os.cpu_count() or 2))
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
    model = model.to(dev).eval()
    log(f"model loaded on {dev}")

    def fetch(u):
        src = img.get(u)
        if not src or not str(src).startswith("http"): return u, None
        try:
            rq = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0 (TRM image fingerprint)"})
            with urllib.request.urlopen(rq, timeout=20) as r: raw = r.read()
            im = Image.open(io.BytesIO(raw)).convert("RGB"); im.thumbnail((320, 320))
            return u, pre(im)
        except Exception:
            return u, None

    done = fail = 0
    BATCH = 64
    with cf.ThreadPoolExecutor(max_workers=24) as ex:
        for i in range(0, len(todo), 512):
            chunk = todo[i:i+512]
            tens = [(u, x) for u, x in ex.map(fetch, chunk) if x is not None]
            fail += len(chunk) - len(tens)
            rows = []
            with torch.no_grad():
                for j in range(0, len(tens), BATCH):
                    part = tens[j:j+BATCH]
                    f = model.encode_image(torch.stack([x for _, x in part]).to(dev))
                    f = (f / f.norm(dim=-1, keepdim=True)).float().cpu().numpy().astype("float16")
                    rows += [{"url": u, "e": base64.b64encode(v.tobytes()).decode()} for (u, _), v in zip(part, f)]
            for k in range(0, len(rows), 250):
                st, res = C.sb("POST", "/rest/v1/clip_emb?on_conflict=url", rows[k:k+250], {"Prefer": "resolution=merge-duplicates,return=minimal"})
                if st not in (200, 201, 204): log(f"  upsert failed {st} {str(res)[:160]}"); continue
                done += len(rows[k:k+250])
            log(f"  embedded {done} / {len(todo)} ({fail} images unreachable)")
    taste_model.write_run_record(C.SB_URL, C.SB_SECRET, "embed", {"embedded": done, "failed": fail, "todo": len(todo), "duration_s": round(time.time()-T0)})
    log("EMBED DONE")

if __name__ == "__main__":
    main()
