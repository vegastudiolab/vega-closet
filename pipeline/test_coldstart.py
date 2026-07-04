#!/usr/bin/env python3
# Acceptance test for cold-start personalization: two synthetic users with OPPOSITE onboarding
# swipes must get visibly different stage-1 rankings after ~50 taps. If their top-50s mostly
# overlap, onboarding is theater and this exits non-zero.
#
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY  (reads catalog attrs; writes nothing)
import os, json, urllib.request
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

def profile_match(attrs, loves):
    """How strongly an item matches a synthetic persona's love-list."""
    bag = set()
    for k in ("materials", "palette", "mood", "construction"):
        v = attrs.get(k) or []
        bag |= set(v if isinstance(v, list) else [v])
    for k in ("silhouette", "branding", "weight"):
        v = attrs.get(k)
        if isinstance(v, list): v = v[0] if v else None
        if v: bag.add(v)
    return len(bag & loves)

def synth_user(catalog, loves, hates, n=50):
    """Pick real catalog items the persona would love/pass — like deck swipes."""
    scored = [(profile_match(c["attrs"], loves), profile_match(c["attrs"], hates), c) for c in catalog]
    pos = sorted([t for t in scored if t[0] >= 2 and t[1] == 0], key=lambda t: -t[0])[:n // 2]
    neg = sorted([t for t in scored if t[1] >= 2 and t[0] == 0], key=lambda t: -t[1])[:n // 2]
    return [(1, c) for _, _, c in pos] + [(0, c) for _, _, c in neg]

def rank(catalog, weights, brate):
    scored = []
    for c in catalog:
        x = taste_model.featurize(c["attrs"], c.get("brand"), c.get("price"), c.get("category"), brate(c.get("brand")))
        scored.append((taste_model.predict(weights, x), c))
    scored.sort(key=lambda t: -t[0])
    return scored

def main():
    catalog = [c for c in fetch_all("catalog", "url,brand,price,category,attrs", "&attrs=not.is.null") if c.get("attrs")]
    print(f"catalog with attrs: {len(catalog)}")
    prior = taste_model.load_prior()
    assert prior, "prior_weights.json missing"

    A_loves = {"boxy", "oversized", "wide", "avant-garde", "punk", "leather", "black", "distressed", "asymmetric", "heavy"}
    A_hates = {"slim", "formal", "luxury", "logo-forward", "pastel", "athletic"}
    B_loves = {"slim", "straight", "formal", "luxury", "minimal", "navy", "white", "wool", "plain", "light"}
    B_hates = {"oversized", "punk", "grunge", "distressed", "graphic-print", "all-over"}

    results = {}
    for name, loves, hates in (("A avant", A_loves, A_hates), ("B tailored", B_loves, B_hates)):
        labeled = synth_user(catalog, loves, hates)
        w, pairs = taste_model.fit_user_weights(labeled, prior=prior)
        brate = taste_model.brand_rates(pairs)
        ranked = rank(catalog, w, brate)
        results[name] = [c["url"] for _, c in ranked[:50]]
        print(f"\n{name}: fitted on {len(labeled)} synthetic taps; top 5:")
        for s, c in ranked[:5]:
            a = c["attrs"]
            print(f"   {s:.2f}  {str(a.get('silhouette'))[:10]:<11} {'/'.join((a.get('mood') or [])[:2]):<22} {c['brand'][:18]}")

    overlap = len(set(results["A avant"]) & set(results["B tailored"]))
    print(f"\ntop-50 overlap between opposite personas: {overlap}/50")
    if overlap > 15:
        print("FAIL — opposite tastes get near-identical feeds; personalization is not real")
        raise SystemExit(1)
    print("PASS — cold-start personalization is real")

if __name__ == "__main__":
    main()
