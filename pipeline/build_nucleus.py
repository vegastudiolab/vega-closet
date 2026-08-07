#!/usr/bin/env python3
# NUCLEUS — the admin snapshot. Computes high-level TRM health from the live tables and writes
# ONE json into the admin's private wardrobe storage path ({uid}/nucleus/nucleus.json), where the
# existing wardrobe_select_own RLS policy makes it readable only by that account. No new tables.
# Runs after every scan + rebuild (conductor.yml / attr-extract.yml); keeps a rolling daily history.
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY, ADMIN_UID (defaults to Charles)
import os, sys, json, urllib.request, urllib.parse
from datetime import date, datetime, timezone, timedelta
from collections import Counter, defaultdict

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
URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SECRET_KEY"]
ADMIN_UID = os.environ.get("ADMIN_UID", "72fc955c-3832-409e-ad43-622d2546e586")
NOW = datetime.now(timezone.utc)
TODAY = NOW.date().isoformat()

def http(method, path, body=None, extra=None, raw=False):
    h = {"apikey": KEY, "Authorization": "Bearer " + KEY}
    if body is not None and not raw: h["Content-Type"] = "application/json"
    if extra: h.update(extra)
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URL + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out = r.read()
            return r.status, (json.loads(out) if out[:1] in (b"[", b"{") else out)
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def fetch_all(table, select, qs=""):
    rows, start = [], 0
    while True:
        st, part = http("GET", f"/rest/v1/{table}?select={select}{qs}", None,
                        {"Range-Unit": "items", "Range": f"{start}-{start+999}"})
        if st not in (200, 206) or not isinstance(part, list):
            print(f"fetch fail {table} {st}"); sys.exit(1)
        rows += part
        if len(part) < 1000: break
        start += 1000
    return rows

def norm(s): return (s or "").strip().lower()

def main():
    taste = fetch_all("taste", "user_id,payload,updated_at")
    sigs  = fetch_all("signals", "user_id,url,action,brand,created_at,reasons")
    cat   = fetch_all("catalog", "url,brand,gender,category,platform,first_seen")
    # feed payloads are MB-scale jsonb — fetching them in one range 500s; go row by row
    feeds = []
    for row in fetch_all("feeds", "user_id,built_at"):
        st1, one = http("GET", f"/rest/v1/feeds?select=user_id,payload,built_at&user_id=eq.{row['user_id']}")
        if st1 == 200 and isinstance(one, list) and one: feeds.append(one[0])
    d1, d7 = (NOW - timedelta(days=1)).isoformat(), (NOW - timedelta(days=7)).isoformat()
    day7  = (NOW.date() - timedelta(days=7)).isoformat()

    # ---- pulse ----
    by_user = defaultdict(list)
    for s in sigs: by_user[s["user_id"]].append(s)
    first_tap = {u: min(x["created_at"] for x in xs) for u, xs in by_user.items()}
    acted7 = [s for s in sigs if s["created_at"] >= d7]
    pulse = {
        "users_total": len(taste),
        "users_tapped": len(by_user),
        "active_7d": len({s["user_id"] for s in acted7}),
        "active_1d": len({s["user_id"] for s in sigs if s["created_at"] >= d1}),
        "new_activations_7d": sum(1 for t in first_tap.values() if t >= d7),
        "taps_total": len(sigs), "taps_7d": len(acted7),
        "taps_1d": sum(1 for s in sigs if s["created_at"] >= d1),
        "likes_7d": sum(1 for s in acted7 if s["action"] == "liked"),
        "carts_7d": sum(1 for s in acted7 if s["action"] == "carted"),
        "like_rate_7d": round(sum(1 for s in acted7 if s["action"] in ("liked", "carted")) / len(acted7), 3) if acted7 else None,
        "catalog_total": len(cat),
        "catalog_new_1d": sum(1 for c in cat if c.get("first_seen") == TODAY),
        "catalog_new_7d": sum(1 for c in cat if (c.get("first_seen") or "") >= day7),
    }

    # ---- per-user feed health (live counts from the rendered payloads) ----
    users = []
    for f in feeds:
        p = f.get("payload") or {}
        items = [i for s in p.get("sections") or [] for i in s.get("items") or []]
        live  = sum(1 for i in items if not i.get("isArchived") and not i.get("isLiked"))
        t = next((x for x in taste if x["user_id"] == f["user_id"]), None)
        tp = (t or {}).get("payload") or {}
        users.append({
            "uid": f["user_id"][:8],
            "gender": norm(tp.get("gender")) or "—",
            "sizes": bool(tp.get("sizes")),
            "brands": len((tp.get("brands") or {}).get("loved") or []),
            "taps": len(by_user.get(f["user_id"], [])),
            "live": live,
            "liked": sum(1 for i in items if i.get("isLiked")),
            "dismissed": len(((tp.get("signals") or {}).get("dismissedUrls")) or []),
            "built_at": f.get("built_at"),
        })
    users.sort(key=lambda u: u["live"])
    pulse["feed_live_median"] = sorted(u["live"] for u in users)[len(users)//2] if users else 0
    pulse["feeds_starved"] = sum(1 for u in users if u["live"] < 25)

    # ---- onboarding funnel ----
    def tp(t): return t.get("payload") or {}
    deck_users = {s["user_id"] for s in sigs if "deck" in (s.get("reasons") or [])}
    funnel = {
        "signed_up": len(taste),
        "gender_set": sum(1 for t in taste if tp(t).get("gender")),
        "sizes_set": sum(1 for t in taste if tp(t).get("sizes")),
        "deck_done": len(deck_users),
        "brief_built": sum(1 for t in taste if (tp(t).get("visualBrief") or {})),
        "tapped_10": sum(1 for u in by_user.values() if len(u) >= 10),
        "tapped_100": sum(1 for u in by_user.values() if len(u) >= 100),
    }

    # ---- brands: supply x demand across the whole product ----
    walls = Counter()
    for t in taste:
        for b in (tp(t).get("brands") or {}).get("loved") or []: walls[norm(b)] += 1
    supply, fresh = Counter(), Counter()
    display = {}
    for c in cat:
        b = norm(c.get("brand"))
        if not b: continue
        display.setdefault(b, c["brand"]); supply[b] += 1
        if (c.get("first_seen") or "") >= day7: fresh[b] += 1
    demand = defaultdict(lambda: [0, 0, 0])           # brand -> [likes+carts, passes, taps]
    for s in sigs:
        b = norm(s.get("brand"))
        if not b: continue
        d = demand[b]; d[2] += 1
        if s["action"] in ("liked", "carted"): d[0] += 1
        else: d[1] += 1
    names = sorted(set(supply) | set(demand), key=lambda b: -(supply[b] + demand[b][2]))
    brands = []
    for b in names[:120]:
        lk, ps, tp_ = demand[b]
        brands.append({"brand": display.get(b, b), "items": supply[b], "new_7d": fresh[b],
                       "likes": lk, "passes": ps,
                       "like_rate": round(lk / tp_, 3) if tp_ else None,
                       "walls": walls.get(b, 0)})

    # ---- catalog composition ----
    comp = {
        "by_platform": dict(Counter(c.get("platform") or "?" for c in cat)),
        "by_gender": dict(Counter(c.get("gender") or "?" for c in cat)),
        "by_category": dict(Counter(c.get("category") or "?" for c in cat)),
        "added_by_day": dict(sorted(Counter(c.get("first_seen") for c in cat
                                            if (c.get("first_seen") or "") >= (NOW.date() - timedelta(days=14)).isoformat()).items())),
    }
    # attrs coverage via counted range probes (content-range header carries the exact total)
    def counted(path):
        req = urllib.request.Request(URL + path, headers={"apikey": KEY, "Authorization": "Bearer " + KEY,
                                                          "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                cr = r.headers.get("Content-Range", "")
                return int(cr.split("/")[-1]) if "/" in cr else None
        except Exception:
            return None
    comp["attrs_missing"] = counted("/rest/v1/catalog?select=url&attrs=is.null")
    comp["attrs_missing_men"] = counted("/rest/v1/catalog?select=url&attrs=is.null&gender=eq.men")

    payload = {"built_at": NOW.isoformat(), "pulse": pulse, "users": users,
               "funnel": funnel, "brands": brands, "catalog": comp}

    # ---- rolling daily history (merge with previous snapshot, replace today's row) ----
    obj = f"/storage/v1/object/wardrobe/{ADMIN_UID}/nucleus/nucleus.json"
    stp, prev = http("GET", obj)
    history = (prev.get("history") if stp == 200 and isinstance(prev, dict) else None) or []
    history = [h for h in history if h.get("d") != TODAY]
    history.append({"d": TODAY, "users": pulse["users_total"], "active7": pulse["active_7d"],
                    "taps7": pulse["taps_7d"], "cat": pulse["catalog_total"],
                    "new7": pulse["catalog_new_7d"], "median_live": pulse["feed_live_median"],
                    "starved": pulse["feeds_starved"]})
    payload["history"] = history[-180:]

    body = json.dumps(payload).encode()
    stu, r = http("POST", obj, body, {"Content-Type": "application/json", "x-upsert": "true"}, raw=True)
    print(f"nucleus: {len(body)//1024}KB -> {obj.split('/wardrobe/')[1]} (HTTP {stu})")
    print(f"  pulse: {json.dumps(pulse)}")
    if stu not in (200, 201): sys.exit(1)

if __name__ == "__main__":
    main()
