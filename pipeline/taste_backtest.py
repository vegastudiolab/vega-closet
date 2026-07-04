#!/usr/bin/env python3
# Backtest for two-stage taste scoring: can weights over shared attributes, FIT ON CHARLES'S
# OWN TAP HISTORY, predict held-out decisions better than the current vision score (AUC 0.61)?
# Temporal split: train on the older 70% of signals, test on the newest 30% — the honest setup,
# since production always predicts the future from the past. Pure stdlib (no sklearn).
#
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY
import os, json, math, random, urllib.request, urllib.error

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
SB_URL = os.environ["SUPABASE_URL"].rstrip("/"); SB_KEY = os.environ["SUPABASE_SECRET_KEY"]

def sb_get(path):
    req = urllib.request.Request(SB_URL + path, headers={"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())

def fetch_all(table, select, qs=""):
    rows, start = [], 0
    while True:
        part = sb_get(f"/rest/v1/{table}?select={select}{qs}&limit=1000&offset={start}")
        if not isinstance(part, list): break
        rows += part
        if len(part) < 1000: break
        start += 1000
    return rows

# ---- featurize attrs ----
def featurize(attrs, brand, price):
    f = {}
    def one(prefix, val):
        if val: f[f"{prefix}={val}"] = 1.0
    def many(prefix, vals):
        for v in (vals or []): f[f"{prefix}={v}"] = 1.0
    one("sil", attrs.get("silhouette"))
    many("mat", attrs.get("materials"))
    many("pal", attrs.get("palette"))
    many("con", attrs.get("construction"))
    many("mood", attrs.get("mood"))
    one("brand", (attrs.get("branding") or ""))
    one("era", attrs.get("era"))
    one("wt", attrs.get("weight"))
    f["statement"] = (attrs.get("statement") or 3) / 5.0
    f["formality"] = (attrs.get("formality") or 3) / 5.0
    if isinstance(price, (int, float)) and price > 0:
        f["logprice"] = min(math.log10(price) / 4.0, 1.0)
    one("bd", (brand or "").lower()[:24])            # brand identity is real taste signal
    f["bias"] = 1.0
    return f

def logistic_fit(X, y, epochs=220, lr=0.25, l2=1e-4):
    # X: list of dict features; y: 0/1. SGD with per-feature weights.
    w = {}
    n = len(X)
    idx = list(range(n))
    rnd = random.Random(7)
    for ep in range(epochs):
        rnd.shuffle(idx)
        for i in idx:
            z = sum(w.get(k, 0.0) * v for k, v in X[i].items())
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
            g = p - y[i]
            for k, v in X[i].items():
                w[k] = w.get(k, 0.0) - lr * (g * v + l2 * w.get(k, 0.0))
        lr *= 0.985
    return w

def predict(w, x):
    z = sum(w.get(k, 0.0) * v for k, v in x.items())
    return 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))

def auc(pairs):
    # pairs: list of (score, label)
    pos = sorted(s for s, l in pairs if l == 1)
    neg = sorted(s for s, l in pairs if l == 0)
    if not pos or not neg: return float("nan")
    import bisect
    wins = ties = 0
    for s in pos:
        lo = bisect.bisect_left(neg, s); hi = bisect.bisect_right(neg, s)
        wins += lo; ties += hi - lo
    return (wins + 0.5 * ties) / (len(pos) * len(neg))

def main():
    signals = fetch_all("signals", "url,action,created_at")
    catalog = fetch_all("catalog", "url,brand,price,base_score,attrs", "&attrs=not.is.null")
    cat = {c["url"]: c for c in catalog}
    rows = []
    for s in signals:
        c = cat.get(s["url"])
        if not c: continue
        label = 1 if s["action"] in ("liked", "carted") else 0
        rows.append((s.get("created_at") or "", label, c))
    rows.sort(key=lambda r: r[0])                      # temporal order
    n = len(rows)
    if n < 500:
        print(f"only {n} labeled+extracted rows — need more attrs before a trustworthy backtest"); return
    cut = int(n * 0.7)
    train, test = rows[:cut], rows[cut:]
    Xtr = [featurize(c["attrs"], c.get("brand"), c.get("price")) for _, _, c in train]
    ytr = [l for _, l, _ in train]
    print(f"rows: {n} ({sum(ytr)} positive in train) | train {len(train)} / test {len(test)}")
    w = logistic_fit(Xtr, ytr)

    model_pairs, vision_pairs = [], []
    for _, l, c in test:
        model_pairs.append((predict(w, featurize(c["attrs"], c.get("brand"), c.get("price"))), l))
        vision_pairs.append((float(c.get("base_score") or 0), l))
    a_model = auc(model_pairs); a_vision = auc(vision_pairs)
    base_rate = sum(l for _, l in model_pairs) / len(model_pairs)
    print(f"\n== HELD-OUT (newest 30% of taps) ==")
    print(f"two-stage model AUC : {a_model:.3f}")
    print(f"current vision  AUC : {a_vision:.3f}   (same test rows — fair fight)")
    print(f"test base like-rate : {base_rate:.1%}")
    # top-decile lift comparison
    for name, pairs in (("model", model_pairs), ("vision", vision_pairs)):
        ps = sorted(pairs, key=lambda p: -p[0])
        top = ps[:max(1, len(ps)//10)]
        lift = (sum(l for _, l in top) / len(top)) / max(0.001, base_rate)
        print(f"{name} top-decile lift: {lift:.2f}x")
    # most informative weights, for the eyeball test
    ws = sorted(w.items(), key=lambda kv: -abs(kv[1]))
    strong = [(k, round(v, 2)) for k, v in ws if k != "bias"][:18]
    print("\nstrongest learned weights:", strong)

if __name__ == "__main__":
    main()
