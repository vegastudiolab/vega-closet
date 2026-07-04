#!/usr/bin/env python3
# TRM two-stage taste scoring — the shared brain.
# Stage 1 (cheap, scales): items carry objective visual attributes (extracted once, shared by
# every user); a user's taste is a set of logistic weights over those attributes, FIT FROM
# THEIR OWN love/cart/pass history. Ranking = arithmetic, no model calls.
# Stage 2 (quality, bounded): the top slice a user will actually see is re-ranked by vision
# against their personal rubric (visualBrief) — same judgment quality as the old system,
# fixed small cost per rebuild regardless of catalog or user count.
# Imported by conductor.py (extraction + scan-cap ranking) and build_feed_cloud.py (fit + rank).
import json, math, random, urllib.request, urllib.error

# ---- stage-1 attribute schema (objective, user-agnostic) ----
ATTR_TOOL = {
    "name": "record_attributes",
    "description": "Record the objective visual attributes of this garment/item.",
    "input_schema": {
        "type": "object",
        "properties": {
            "silhouette":   {"type": "string", "enum": ["slim","straight","relaxed","boxy","oversized","wide","flared","longline","cropped","draped","na"]},
            "materials":    {"type": "array", "items": {"type": "string", "enum": ["leather","suede","denim","raw-denim","nylon","tech-fabric","wool","knit","cotton","jersey","fleece","canvas","silk","satin","mesh","shearling","fur","corduroy","velvet","coated","rubber","other"]}, "maxItems": 4},
            "palette":      {"type": "array", "items": {"type": "string", "enum": ["black","white","cream","grey","charcoal","navy","olive","brown","tan","beige","red","burgundy","green","blue","yellow","orange","pink","purple","multicolor","pastel"]}, "maxItems": 3},
            "construction": {"type": "array", "items": {"type": "string", "enum": ["distressed","deconstructed","asymmetric","paneled","quilted","padded","pleated","raw-hem","patchwork","embroidered","graphic-print","washed","faded","waxed","zippered","buckled","strapped","cargo-pockets","drawstring","plain"]}, "maxItems": 5},
            "mood":         {"type": "array", "items": {"type": "string", "enum": ["avant-garde","minimal","streetwear","workwear","techwear","military","vintage","americana","luxury","formal","athletic","punk","grunge","romantic","futuristic"]}, "maxItems": 3},
            "branding":     {"type": "string", "enum": ["none","subtle","logo-forward","all-over"]},
            "era":          {"type": "string", "enum": ["contemporary","y2k","90s","80s","older","archival-designer"]},
            "statement":    {"type": "integer", "minimum": 1, "maximum": 5},
            "formality":    {"type": "integer", "minimum": 1, "maximum": 5},
            "weight":       {"type": "string", "enum": ["light","mid","heavy"]},
            "tags":         {"type": "array", "items": {"type": "string"}, "maxItems": 4}
        },
        "required": ["silhouette","materials","palette","construction","mood","branding","era","statement","formality","weight"]
    }
}

def _http(method, url, body=None, headers=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw[:1] in ("[", "{") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try: return e.code, json.loads(raw)
        except Exception: return e.code, raw
    except Exception as e:
        return 0, str(e)

def extract_attrs(anthropic_key, image_url, title="", image_b64=None, media_type="image/jpeg"):
    """One shared vision look per item (catalog url OR user-uploaded base64). Returns attrs or None."""
    if not (anthropic_key and (image_url or image_b64)): return None
    source = ({"type": "base64", "media_type": media_type, "data": image_b64} if image_b64
              else {"type": "url", "url": image_url})
    st, r = _http("POST", "https://api.anthropic.com/v1/messages",
        {"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
         "tools": [ATTR_TOOL], "tool_choice": {"type": "tool", "name": "record_attributes"},
         "messages": [{"role": "user", "content": [
             {"type": "image", "source": source},
             {"type": "text", "text": "Look at this menswear item and record its objective visual attributes. "
                                      "Judge only what you can see — no taste opinions. Title for context: " + (title or "")[:120]}]}]},
        {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"})
    if st != 200 or not isinstance(r, dict): return None
    for block in r.get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input")
    return None

def make_brief_addendum(anthropic_key, uploads_by_bucket):
    """Turn a user's archive photos (already attribute-extracted) into rubric prose. Returns str or None.
    APPENDED to any existing visualBrief — never replaces a curated rubric."""
    if not (anthropic_key and any(uploads_by_bucket.values())): return None
    sem = {"receipts": "he BOUGHT these (money = strongest signal: fit, fabric, formality baseline)",
           "rotation": "he OWNS and wears these now (the wardrobe being built around)",
           "grails":   "he hunted these (proven obsession)",
           "dreams":   "he aspires to these (direction of travel, statement ceiling — NOT buy-now taste)"}
    lines = []
    for b, items in uploads_by_bucket.items():
        if items:
            lines.append(sem.get(b, b).upper() + ":\n" + "\n".join("- " + json.dumps(a) for a in items[:20]))
    st, r = _http("POST", "https://api.anthropic.com/v1/messages",
        {"model": "claude-sonnet-5", "max_tokens": 700,
         "messages": [{"role": "user", "content":
            "You maintain a menswear taste rubric used to score secondhand listings 0-1 by vision. "
            "From this user's ARCHIVE (attribute summaries of their own photos, grouped by how they relate "
            "to the pieces), write a compact addendum: LOVED SIGNATURE (silhouettes/materials/palette/moods "
            "with weights implied by bucket semantics), and ASPIRATION vs BUY-NOW distinction. Plain text, "
            "<=180 words, no preamble.\n\n" + "\n\n".join(lines)}]},
        {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"})
    if st != 200 or not isinstance(r, dict): return None
    txt = "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text").strip()
    return txt or None

# ---- stage-2: personal vision judgment on the visible slice ----
VFIT_TOOL = {"name": "score", "description": "Score how well this piece fits the taste rubric.",
             "input_schema": {"type": "object", "properties": {
                 "visualFit": {"type": "number", "minimum": 0, "maximum": 1},
                 "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5}},
                 "required": ["visualFit"]}}

def vision_fit(anthropic_key, image_url, brief):
    """Personal re-rank: judge one item against ONE user's rubric. Returns (vfit, tags) or None."""
    if not (anthropic_key and brief and image_url): return None
    st, r = _http("POST", "https://api.anthropic.com/v1/messages",
        {"model": "claude-haiku-4-5-20251001", "max_tokens": 300,
         "tools": [VFIT_TOOL], "tool_choice": {"type": "tool", "name": "score"},
         "messages": [{"role": "user", "content": [
             {"type": "image", "source": {"type": "url", "url": image_url}},
             {"type": "text", "text": "Taste rubric:\n" + brief[:4000] + "\n\nScore this piece 0-1 against the rubric and tag why."}]}]},
        {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"})
    if st != 200 or not isinstance(r, dict): return None
    for block in r.get("content", []):
        if block.get("type") == "tool_use":
            inp = block.get("input") or {}
            vf = inp.get("visualFit")
            if isinstance(vf, (int, float)): return (float(vf), inp.get("tags") or [])
    return None

# ---- featurization + logistic model (pure stdlib; identical in fit and serve) ----
def _norm_one(val):
    # tolerate schema drift: single-value fields sometimes arrive as 1-element lists
    if isinstance(val, list): val = val[0] if val else None
    return val if isinstance(val, str) else None

def featurize(attrs, brand, price, category, brand_rate):
    f = {}
    def one(prefix, val):
        v = _norm_one(val)
        if v: f[f"{prefix}={v}"] = 1.0
    def many(prefix, vals):
        if isinstance(vals, str): vals = [vals]
        for v in (vals or []):
            if isinstance(v, str): f[f"{prefix}={v}"] = 1.0
    one("sil", attrs.get("silhouette"))
    many("mat", attrs.get("materials"))
    many("pal", attrs.get("palette"))
    many("con", attrs.get("construction"))
    many("mood", attrs.get("mood"))
    one("brand", attrs.get("branding"))
    one("era", attrs.get("era"))
    one("wt", attrs.get("weight"))
    one("cat", category)
    try: f["statement"] = (float(_norm_one(attrs.get("statement")) or attrs.get("statement") or 3)) / 5.0
    except Exception: f["statement"] = 0.6
    try: f["formality"] = (float(_norm_one(attrs.get("formality")) or attrs.get("formality") or 3)) / 5.0
    except Exception: f["formality"] = 0.6
    if isinstance(price, (int, float)) and price > 0:
        f["logprice"] = min(math.log10(price) / 4.0, 1.0)
    f["brand_rate"] = brand_rate
    f["bias"] = 1.0
    return f

def prune_rare(X, min_df=8):
    from collections import Counter
    df = Counter(k for x in X for k in x if "=" in k)
    keep = {k for k, n in df.items() if n >= min_df}
    return [{k: v for k, v in x.items() if "=" not in k or k in keep} for x in X]

def logistic_fit(X, y, epochs=120, lr=0.15, l2=1.2e-3, seed=7):
    w = {}
    idx = list(range(len(X)))
    rnd = random.Random(seed)
    for _ in range(epochs):
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

def brand_rates(sig_rows, prior_taps=8):
    """Smoothed per-brand like-rate from a user's history. sig_rows: (label, brand) tuples."""
    bstat = {}
    for l, b in sig_rows:
        st = bstat.setdefault((b or "").lower(), [0, 0]); st[0] += l; st[1] += 1
    base = (sum(l for l, _ in sig_rows) / len(sig_rows)) if sig_rows else 0.25
    def rate(brand):
        l_, n_ = bstat.get((brand or "").lower(), (0, 0))
        return (l_ + prior_taps * base) / (n_ + prior_taps)
    return rate

def load_prior():
    """The house prior: de-personalized attribute weights (shipped in the repo). Lets a brand-new
    user rank sanely from tap #0; their own data takes over smoothly as history grows."""
    import os
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prior_weights.json")
    try:
        return json.load(open(p))
    except Exception:
        return {}

def blend(w_user, w_prior, n, half=150):
    """w = lam*user + (1-lam)*prior, lam = n/(n+half). Continuous autonomy: lam=0 with no history
    (pure prior), ~0.5 after onboarding, ->1 as taps accumulate. No mode switches, no cliffs."""
    lam = n / (n + half) if n > 0 else 0.0
    keys = set(w_user) | set(w_prior)
    return {k: lam * w_user.get(k, 0.0) + (1 - lam) * w_prior.get(k, 0.0) for k in keys}

def fit_user_weights(labeled, prior=None):
    """labeled: list of (label01, catalog_row_with_attrs). Returns (weights, brate_fn_source_pairs).
    With a prior, the returned weights are prior-anchored — usable at ANY history size."""
    pairs = [(l, (c.get("brand") or "")) for l, c in labeled]
    rate = brand_rates(pairs)
    w_user = {}
    if len(labeled) >= 40:                                   # below the fit floor, the prior carries alone
        X = [featurize(c["attrs"], c.get("brand"), c.get("price"), c.get("category"), rate(c.get("brand"))) for _, c in labeled]
        X = prune_rare(X)
        y = [l for l, _ in labeled]
        w_user = logistic_fit(X, y)
    if prior:
        return blend(w_user, prior, len(labeled)), pairs
    return w_user, pairs
