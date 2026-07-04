#!/usr/bin/env python3
# Shared (user-agnostic) visual attribute extraction — stage 1 of two-stage taste scoring.
# Looks at each item's photo ONCE and records objective attributes into catalog.attrs (jsonb).
# No user rubric involved: these features are shared by every user; personal taste becomes
# weights over them. Run via GitHub Actions (ANTHROPIC_API_KEY secret) or locally with a key.
#
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY, ANTHROPIC_API_KEY
#        ONLY_TAPPED=1  -> just items with signals (backtest set); default all missing attrs
#        LIMIT=n        -> cap items this run (0 = all)
#        WORKERS=8
import os, json, urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor

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
SB_URL = os.environ["SUPABASE_URL"].rstrip("/")
SB_KEY = os.environ["SUPABASE_SECRET_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
ONLY_TAPPED = os.environ.get("ONLY_TAPPED", "").strip() in ("1", "true", "yes")
LIMIT = int(os.environ.get("LIMIT", "0"))
WORKERS = int(os.environ.get("WORKERS", "8"))

def http(method, url, body=None, headers=None, timeout=120):
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

def sb(method, path, body=None, extra=None):
    h = {"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY}
    if extra: h.update(extra)
    return http(method, SB_URL + path, body, h)

def fetch_all(table, select, qs=""):
    rows, start = [], 0
    while True:
        st, part = sb("GET", f"/rest/v1/{table}?select={select}{qs}", None,
                      {"Range-Unit": "items", "Range": f"{start}-{start+999}"})
        if st not in (200, 206) or not isinstance(part, list): break
        rows += part
        if len(part) < 1000: break
        start += 1000
    return rows

# ---- the shared schema: objective, taste-bearing, user-agnostic ----
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
            "statement":    {"type": "integer", "minimum": 1, "maximum": 5, "description": "1 basic staple .. 5 loud centerpiece"},
            "formality":    {"type": "integer", "minimum": 1, "maximum": 5, "description": "1 athletic/casual .. 5 formal"},
            "weight":       {"type": "string", "enum": ["light","mid","heavy"], "description": "fabric/visual weight (summer..winter)"},
            "tags":         {"type": "array", "items": {"type": "string"}, "maxItems": 4, "description": "free-form distinctive descriptors"}
        },
        "required": ["silhouette","materials","palette","construction","mood","branding","era","statement","formality","weight"]
    }
}

PROMPT = ("Look at this menswear item and record its objective visual attributes. "
          "Judge only what you can see — no taste opinions. Title for context: %s")

def extract(item):
    st, r = http("POST", "https://api.anthropic.com/v1/messages",
        {"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
         "tools": [ATTR_TOOL], "tool_choice": {"type": "tool", "name": "record_attributes"},
         "messages": [{"role": "user", "content": [
             {"type": "image", "source": {"type": "url", "url": item["image"]}},
             {"type": "text", "text": PROMPT % (item.get("title") or "")[:120]}]}]},
        {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}, timeout=90)
    if st != 200 or not isinstance(r, dict): return None
    for block in r.get("content", []):
        if block.get("type") == "tool_use":
            return block.get("input")
    return None

def main():
    todo = fetch_all("catalog", "url,title,image", "&attrs=is.null&image=like.http*")
    if ONLY_TAPPED:
        tapped = {s["url"] for s in fetch_all("signals", "url")}
        todo = [t for t in todo if t["url"] in tapped]
    if LIMIT: todo = todo[:LIMIT]
    print(f"extracting attributes for {len(todo)} items ({WORKERS} workers)")
    done = [0]
    def work(it):
        attrs = extract(it)
        if attrs is None:
            attrs = extract(it)                       # one retry — vision/image hiccups
        if attrs is not None:
            st, _ = sb("PATCH", "/rest/v1/catalog?url=eq." + urllib.parse.quote(it["url"], safe=""),
                       {"attrs": attrs}, {"Prefer": "return=minimal"})
            if st in (200, 204): done[0] += 1
            if done[0] % 200 == 0: print(f"  {done[0]} stored")
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        list(ex.map(work, todo))
    print(f"DONE — {done[0]}/{len(todo)} items got attributes")

if __name__ == "__main__":
    main()
