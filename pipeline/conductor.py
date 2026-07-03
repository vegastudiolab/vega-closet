#!/usr/bin/env python3
# Vega Closet cloud conductor. Runs in GitHub Actions (daily cron + on-demand dispatch).
# Reads the loved brands from Supabase, scrapes the three sources over plain HTTPS
# (Apify + Firecrawl REST, no MCP), applies Charles's size / no-blazer / no-accessory
# rules, and INSERTS only genuinely-new pieces into catalog. build_feed_cloud.py then
# rebuilds every user's feed. Secrets come from env (GitHub Actions secrets).
#
#   env: SUPABASE_URL, SUPABASE_SECRET_KEY, APIFY_TOKEN, FIRECRAWL_KEY
#        SOURCES (default "grailed,therealreal,ssense"), MAX_PER_BRAND (40),
#        BRANDS_PER_RUN (24)  -> rotates through the loved list to keep runs cheap
import os, sys, json, time, re, random, urllib.request, urllib.error, urllib.parse
from datetime import date

# ---------- config (env, with local .env fallback) ----------
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
SB_URL   = os.environ["SUPABASE_URL"].rstrip("/")
SB_SECRET= os.environ["SUPABASE_SECRET_KEY"]
APIFY    = os.environ.get("APIFY_TOKEN", "")
FIRE     = os.environ.get("FIRECRAWL_KEY", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BRIGHTDATA     = os.environ.get("BRIGHTDATA_TOKEN", "")   # optional TRR fallback (Bright Data Web Unlocker) if Firecrawl-stealth gets blocked
SOURCES  = [s.strip() for s in os.environ.get("SOURCES", "grailed,therealreal,ssense").split(",") if s.strip()]
MAX_PER_BRAND = int(os.environ.get("MAX_PER_BRAND", "40"))
BRANDS_PER_RUN= int(os.environ.get("BRANDS_PER_RUN", "24"))
MIN_NEW  = int(os.environ.get("MIN_NEW", "0"))   # keep pulling fresh grailed brands until at least this many new (0 = off)
QUERY    = os.environ.get("QUERY", "").strip()  # optional keyword/style/color search term (passes to Algolia)
CATEGORY = os.environ.get("CATEGORY", "").strip().lower()  # optional single category: outerwear|tops|bottoms|footwear|accessories
NO_VISION = os.environ.get("NO_VISION", "").strip().lower() in ("1", "true", "yes")  # raw scan: skip taste rating, tag items "unrated"
TODAY = date.today().isoformat()

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
    h = {"apikey": SB_SECRET, "Authorization": "Bearer " + SB_SECRET}
    if extra: h.update(extra)
    return http(method, SB_URL + path, body, h)

def norm(s): return (s or "").strip().lower()

# ---------- filters (mirror ingest.py + the scraper size rules) ----------
CATS = ("outerwear", "bottoms", "tops", "footwear")
def infer_cat(title):
    t = norm(title)
    if re.search(r"jacket|bomber|coat|parka|puffer|trench|vest|cardigan|anorak|blazer|overshirt|shearling|hoodie.*zip", t): return "outerwear"
    if re.search(r"pant|jean|trouser|cargo|short|jogger|sweatpant|denim", t): return "bottoms"
    if re.search(r"tee|t-shirt|shirt|hoodie|sweater|knit|polo|tank|longsleeve|long sleeve|turtleneck|sweatshirt|jumper", t): return "tops"
    if re.search(r"boot|sneaker|shoe|loafer|sandal|mule|derby|trainer", t): return "footwear"
    if re.search(r"hat|cap|bag|wallet|belt|sunglass|jewel|scarf|glove|sock|phone case|keychain", t): return None
    return "tops"

_BLZ  = re.compile(r"blazer|sport ?coat|suit jacket|\bsuit\b|tuxedo|dinner jacket|two[- ]?button|double[- ]?breasted|single[- ]?breasted|peak lapel|notch lapel|pinstripe", re.I)
_KEEP = re.compile(r"padded|deconstruct|distress|rivet|asymmetric|cargo|tech|nylon|leather|denim|fleece|hood|work|bomber|track|puffer|anorak|coach|moto|biker|quilt", re.I)
def is_blazer(title):
    return bool(_BLZ.search(title or "") and not _KEEP.search(title or ""))

def in_size(cat, s, brand=""):
    s = norm(s)
    if not s: return False
    if cat == "footwear":
        # US 14-15 / EU 47-48. US 13 is out (taste-training only, never fit) — EXCEPT Balenciaga,
        # which runs oversized: a 46 fits, so accept 46 (and its US-normalized 13) for that brand only.
        if "balenciaga" in norm(brand):
            return bool(re.search(r"\b(13|13\.5|14|14\.5|15|46|47|48)\b", s))
        return bool(re.search(r"\b(14|14\.5|15|47|48)\b", s))
    if cat == "bottoms":
        if re.search(r"\b3[45]\b", s): return False                 # never waist 34/35
        return bool(re.search(r"\b(36|37|38|54|56)\b", s) or re.search(r"\b(l|xl|xxl)\b", s))
    return bool(re.search(r"\b(l|xl|xxl)\b", s) or re.search(r"\b(52|54|56)\b", s))   # tops / outerwear

_TAGS = ["leather","wide-leg","wide leg","bootcut","flared","cargo","denim","oversized","boxy",
         "longline","draped","shearling","fur","nylon","tech","padded","quilted","distressed",
         "graphic","knit","hoodie","bomber","trench","parka","moto","biker","satin","mesh","patchwork","rivet"]
def tags_from(title):
    t = norm(title); out = [w for w in _TAGS if w in t]
    for c in ("black","white","cream","grey","gray","olive","brown","beige","navy"):
        if c in t: out.append(c)
    return out[:6]

def base_score(brand, tags, loved):
    s = 0.5
    if norm(brand) in loved: s += 0.2
    s += min(0.03 * len(tags), 0.15)
    return round(min(s, 0.9), 3)

def brand_matches(text, loved_raw):
    """word-boundary match so 'erl' != 'Berluti'. returns the canonical loved brand or None."""
    t = norm(text)
    for b in loved_raw:
        nb = norm(b)
        if len(nb) <= 4:
            if re.search(r"\b" + re.escape(nb) + r"\b", t): return b
        elif nb in t: return b
    return None

# ---------- source scrapers ----------
def apify_run(actor, inp, memory=1024, wait=280):
    st, run = http("POST", f"https://api.apify.com/v2/acts/{actor}/runs?token={APIFY}&memory={memory}", inp)
    if st in (402, 429):
        print(f"  APIFY CAP/LIMIT ({st}) — stopping this source, keeping what we have"); return None
    if st not in (200, 201) or not isinstance(run, dict):
        print(f"  apify start failed {st}: {str(run)[:160]}"); return []
    rid = run["data"]["id"]; dsid = run["data"]["defaultDatasetId"]
    t0 = time.time()
    while time.time() - t0 < wait:
        st, r = http("GET", f"https://api.apify.com/v2/actor-runs/{rid}?token={APIFY}")
        stt = r["data"]["status"] if st == 200 and isinstance(r, dict) else "?"
        if stt in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"): break
        time.sleep(6)
    st, items = http("GET", f"https://api.apify.com/v2/datasets/{dsid}/items?token={APIFY}&clean=true&format=json")
    return items if isinstance(items, list) else []

# Grailed via ITS OWN Algolia search API — direct, server-side filtered by designer+size+condition, no proxy, effectively free.
GRAILED_APP  = "MNRWEFSS2Q"
GRAILED_KEY  = os.environ.get("GRAILED_KEY", "c89dbaddf15fe70e1941a109bf7c2a3d")   # public search key; self-heals on 403
GRAILED_SIZES = ["size:l","size:xl","size:xxl","size:36","size:37","size:38","size:13","size:14","size:14.5","size:15"]  # size:13 stays for balenciaga (eu46); in_size() refines

def _grailed_refresh_key():
    global GRAILED_KEY
    if not FIRE: return
    st, r = http("POST", "https://api.firecrawl.dev/v2/scrape",
                 {"url":"https://www.grailed.com/shop","formats":["rawHtml"],"proxy":"stealth"},
                 {"Authorization":"Bearer "+FIRE}, timeout=180)
    raw = ((r.get("data") or {}).get("rawHtml") or "") if isinstance(r, dict) else ""
    m = re.search(r'public_search_key["\s:]{1,6}([0-9a-f]{32})', raw)
    if m: GRAILED_KEY = m.group(1); print("  grailed search key refreshed")

def grailed_algolia(facet, page=0, hpp=100, retried=False, query=""):
    q = urllib.parse.quote(query or QUERY)
    body = {"params": "query=%s&hitsPerPage=%d&page=%d&facetFilters=%s" % (q, hpp, page, urllib.parse.quote(json.dumps(facet)))}
    st, r = http("POST", "https://%s-dsn.algolia.net/1/indexes/Listing_by_date_added_production/query" % GRAILED_APP.lower(),
                 body, {"x-algolia-application-id": GRAILED_APP, "x-algolia-api-key": GRAILED_KEY})
    if st in (401, 403) and not retried:                          # key rotated -> re-harvest via Firecrawl + retry once
        _grailed_refresh_key(); return grailed_algolia(facet, page, hpp, retried=True)
    return r if isinstance(r, dict) else {}

def scrape_grailed(brands, loved):
    facet = [["designers.name:" + b for b in brands], GRAILED_SIZES, ["condition:is_new", "condition:is_gently_used"], ["department:menswear"]]
    if CATEGORY: facet.append(["category:" + CATEGORY])           # grailed's category facet uses our exact five names
    out = []
    for page in range(3):                                         # newest ~300 across his brands in his sizes
        r = grailed_algolia(facet, page=page, hpp=100)
        hits = r.get("hits") or []
        for h in hits:
            if h.get("sold"): continue
            cond = {"is_new":"new","is_gently_used":"gently used"}.get(norm(h.get("condition")))
            if not cond: continue
            iid = str(h.get("id") or h.get("objectID") or "")
            dn = h.get("designer_names") or ((h.get("designers") or [{}])[0] or {}).get("name") or ""
            title = h.get("title", "") or ""
            out.append({"url":"https://www.grailed.com/listings/"+iid, "id":iid, "platform":"grailed",
                        "brand":dn, "title":title, "category":infer_cat(title), "price":h.get("price"),
                        "size":h.get("size",""), "condition":cond,
                        "image":((h.get("cover_photo") or {}).get("url") or "").split("?")[0]})
        if len(hits) < 100: break
    print(f"  grailed: {len(out)} listings across {len(brands)} brand(s), via Algolia (no Apify)")
    return out

# The RealReal via ITS OWN GraphQL API, through Firecrawl stealth (clears TRR's PerimeterX wall), Bright Data fallback.
# Filters SERVER-SIDE by designer + size, so we fetch only his brands in his sizes instead of scraping whole
# categories and discarding ~95%. This is what took TRR off the (maxed) Apify plan — ~30-50x less volume.
TRR_QUERY = ("query P($first:Int,$after:String,$where:ProductFilters,$sortBy:SortBy,$currency:Currencies){"
             "products(first:$first,after:$after,where:$where,sortBy:$sortBy,currency:$currency){"
             "totalCount pageInfo{endCursor hasNextPage} edges{node{id sku name url "
             "brandUnion{...on Designer{name} ...on Artist{name}} price{final{usdCents}} images{url} "
             "attributes{type values} condition}}}}")
TRR_TAXCAT = {"men/clothing/outerwear":"outerwear", "men/clothing/sweaters-sweatshirts":"tops",
              "men/clothing/shirts":"tops", "men/clothing/pants":"bottoms", "men/clothing/jeans":"bottoms",
              "men/shoes":"footwear"}

def _trr_json(raw):
    i = (raw or "").find('{"data"')
    if i < 0: return None
    try: return json.loads(raw[i:raw.rfind('}')+1])
    except Exception:
        try: return json.loads(raw[i:])
        except Exception: return None

def trr_graphql(variables):
    url = "https://api.therealreal.com/graphql?query=" + urllib.parse.quote(TRR_QUERY) + "&variables=" + urllib.parse.quote(json.dumps(variables))
    st, r = http("POST", "https://api.firecrawl.dev/v2/scrape",                     # Firecrawl stealth clears TRR's PerimeterX wall
                 {"url": url, "formats": ["rawHtml"], "proxy": "stealth"},
                 {"Authorization": "Bearer " + FIRE}, timeout=180)
    data = (r.get("data") or {}) if isinstance(r, dict) else {}
    sc = (data.get("metadata") or {}).get("statusCode")
    payload = _trr_json(data.get("rawHtml") or "") if sc == 200 else None
    if payload is None and BRIGHTDATA:                                              # fallback: Bright Data Web Unlocker (only if configured)
        st2, r2 = http("POST", "https://api.brightdata.com/request",
                       {"zone": os.environ.get("BRIGHTDATA_ZONE", "web_unlocker1"), "url": url, "format": "raw"},
                       {"Authorization": "Bearer " + BRIGHTDATA}, timeout=180)
        payload = _trr_json(r2 if isinstance(r2, str) else json.dumps(r2))
    return ((payload or {}).get("data") or {}).get("products") if payload else None

def scrape_trr(loved, loved_raw):
    slugs = [s for s in (re.sub(r"[^a-z0-9]+", "-", norm(b)).strip("-") for b in loved_raw) if s]  # loved brands -> TRR designer slugs
    out = []
    taxcat = {t: c for t, c in TRR_TAXCAT.items() if not CATEGORY or c == CATEGORY}   # category scan -> only matching taxons
    for taxon, cat in taxcat.items():
        buckets = {"taxonsPermalink": [taxon], "designerSlug": slugs}
        if cat in ("outerwear", "tops"): buckets["clothingSize"] = ["27", "28", "29"]   # L/XL/XXL server-side; bottoms/shoes filtered client-side by in_size()
        after = None
        for _page in range(2):                                     # newest 2 pages/category = the fresh delta
            v = {"first": 120, "currency": "USD", "sortBy": "NEWEST", "where": {"buckets": buckets}}
            if after: v["after"] = after
            pr = trr_graphql(v)
            if not pr:
                print(f"  trr {taxon}: blocked/empty"); break
            for e in pr.get("edges", []):
                n = e.get("node") or {}
                brand = (n.get("brandUnion") or {}).get("name") or ""
                sizeparts = [str(x) for a in (n.get("attributes") or []) if a.get("type") in ("CLOTHING_SIZE","SHOE_SIZE","MENS_WAIST") for x in (a.get("values") or [])]
                price = None
                try: price = (((n.get("price") or {}).get("final") or {}).get("usdCents") or 0) / 100 or None
                except Exception: pass
                imgs = n.get("images") or []
                out.append({"url": (n.get("url") or "").split("?")[0], "id": str(n.get("id") or n.get("sku") or ""),
                            "platform": "therealreal", "brand": brand_matches(brand, loved_raw) or brand,
                            "title": n.get("name", ""), "category": cat, "price": price, "size": " ".join(sizeparts),
                            "condition": n.get("condition") or "gently used",
                            "image": ((imgs[0].get("url", "") if imgs and isinstance(imgs[0], dict) else "") or "").split("?")[0]})
            pi = pr.get("pageInfo") or {}
            after = pi.get("endCursor")
            if not pi.get("hasNextPage"): break
        print(f"  trr {taxon}: {len(out)} cumulative")
    return out

def firecrawl_scrape(url, schema, proxy="auto", wait=9000, return_meta=False):
    st, r = http("POST", "https://api.firecrawl.dev/v2/scrape",
                 {"url":url,"formats":[{"type":"json","schema":schema}],"waitFor":wait,"proxy":proxy},
                 {"Authorization":"Bearer "+FIRE}, timeout=180)
    if st != 200 or not isinstance(r, dict): return None
    data = r.get("data") or {}
    return data if return_meta else data.get("json")   # return_meta -> full data (json + metadata og:image)

def scrape_ssense(brands, loved_raw, per_brand=6):
    # SSENSE needs the stealth proxy + a proper JSON Schema, or the extraction comes back empty.
    LIST = {"type":"object","properties":{"products":{"type":"array","items":{"type":"object","properties":{
        "name":{"type":"string"},"price":{"type":"number"},"url":{"type":"string"},"image":{"type":"string"}}}}}}
    SIZE = {"type":"object","properties":{"name":{"type":"string"},
        "sizesAvailable":{"type":"array","items":{"type":"string"}},"image":{"type":"string"}}}
    out = []
    for b in brands[:8]:                                          # light pass to control credits
        slug = re.sub(r"[^a-z0-9]+","-",norm(b)).strip("-")
        data = firecrawl_scrape(f"https://www.ssense.com/en-us/men/designers/{slug}", LIST, proxy="stealth", wait=12000)
        prods = (data or {}).get("products") or []
        for p in prods[:per_brand]:
            title = p.get("name",""); cat = infer_cat(title)
            if cat not in CATS: continue
            if CATEGORY and cat != CATEGORY: continue             # category scan: skip before the pricey product-page scrape
            purl = (p.get("url") or "").split("?")[0]
            if not purl: continue
            szdata = firecrawl_scrape(purl, SIZE, proxy="stealth", wait=9000, return_meta=True) or {}   # json (sizes) + metadata (og:image)
            sizes = " ".join((szdata.get("json") or {}).get("sizesAvailable") or [])
            if not in_size(cat, sizes, b): continue
            meta = szdata.get("metadata") or {}
            og = meta.get("ogImage") or meta.get("og:image") or ""
            if isinstance(og, list): og = og[0] if og else ""
            def _valid(u):                                                                # only a real, full ssense image (not a truncated/placeholder url)
                u = (u or "").split("?")[0]
                return u if (u.startswith("http") and ("res.cloudinary.com/ssenseweb/image/upload/" in u or re.search(r"ssensemedia\.com/images/w_\d", u))) else None
            img = _valid(og) or _valid((szdata.get("json") or {}).get("image")) or _valid(p.get("image"))
            if not img: continue                                                          # no usable image -> skip the item entirely
            m = re.search(r"(\d+)$", purl)
            out.append({"url":purl,"id":(m.group(1) if m else purl),"platform":"ssense","brand":b,
                        "title":title,"category":cat,"price":p.get("price"),"size":sizes,
                        "condition":"new","image":img})
    return out

# ---------- rank a new piece by his EYE (Claude vision vs the stored taste rubric) ----------
def vision_score(image_url, brief):
    if not (ANTHROPIC_KEY and brief and image_url): return None
    prompt = ("You are ranking one menswear piece for a single collector. His visual taste rubric:\n"
              + (brief.get("brief","")) + "\n\nLOVED signals: " + "; ".join(brief.get("lovedSignals", []))
              + "\nPASSED signals: " + "; ".join(brief.get("passedSignals", []))
              + "\n\nLook at the photo and score how strongly THIS piece matches his taste. "
                "0.90-1.00 quintessentially him; 0.65-0.85 clearly his lane; 0.40-0.60 plausible; "
                "0.15-0.35 leans passed; 0.00-0.15 not him. Also give 3-6 short visual tags. Call the score tool.")
    body = {"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
            "tools": [{"name": "score", "description": "Record taste-fit score and visual tags.",
                       "input_schema": {"type": "object", "properties": {
                           "visualFit": {"type": "number"}, "tags": {"type": "array", "items": {"type": "string"}}},
                           "required": ["visualFit", "tags"]}}],
            "tool_choice": {"type": "tool", "name": "score"},
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "url", "url": image_url}},
                {"type": "text", "text": prompt}]}]}
    st, r = http("POST", "https://api.anthropic.com/v1/messages", body,
                 {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"}, timeout=60)
    if st != 200 or not isinstance(r, dict): return None
    for block in r.get("content", []):
        if block.get("type") == "tool_use":
            inp = block.get("input") or {}
            vf = inp.get("visualFit")
            if isinstance(vf, (int, float)): return (float(vf), inp.get("tags") or [])
    return None

# ---------- main ----------
def fetch_all(table, select):
    rows=[]; start=0; step=1000
    while True:
        st,part = sb("GET", f"/rest/v1/{table}?select={select}", None, {"Range-Unit":"items","Range":f"{start}-{start+step-1}"})
        if st not in (200,206): print("fetch fail", table, st); break
        rows += part
        if len(part) < step: break
        start += step
    return rows

def filter_new(found, known, loved):
    rows = []; newurls = set()
    for it in found:
        url = it.get("url")
        if not url or not it.get("brand") or not str(it.get("image","")).startswith("http"): continue  # reject data:/placeholder images
        if url in known or url in newurls: continue                # only genuinely new
        cat = it["category"]
        if cat not in CATS: continue
        if not in_size(cat, it.get("size"), it.get("brand")): continue
        if cat == "bottoms" and re.search(r"\b3[45]\b", norm(it.get("size"))): continue
        if is_blazer(it.get("title")): continue
        newurls.add(url)
        tags = tags_from(it["title"])
        rows.append({**it, "reasons": tags, "base_score": base_score(it["brand"], tags, loved),
                     "sz": size_bucket(it), "first_seen": TODAY, "last_seen": TODAY})
    return rows, newurls

def main():
    # loved brands from Charles's taste row (first taste row = him for now)
    st, tastes = sb("GET", "/rest/v1/taste?select=payload&limit=1")
    payload = (tastes or [{}])[0].get("payload", {}) if tastes else {}
    loved_raw = (payload.get("brands", {}) or {}).get("loved", [])
    loved = set(norm(b) for b in loved_raw)
    brief = payload.get("visualBrief")   # the learned visual taste rubric (owned + liked vs passed)
    if not loved_raw:
        print("no loved brands found — aborting"); return
    # brands: explicit override (on-demand / testing) OR a rotating slice to bound daily cost
    override = os.environ.get("BRANDS", "").strip()
    if override:
        todays = [b.strip() for b in override.split(",") if b.strip()]
    else:
        n = min(BRANDS_PER_RUN, len(loved_raw))
        todays = random.sample(loved_raw, n)          # random slice each run so repeat scans hit fresh brands
    print(f"loved brands: {len(loved_raw)} | this run scrapes {len(todays)}: {todays[:6]}{'...' if len(todays)>6 else ''}")
    print(f"sources: {SOURCES} | max/brand: {MAX_PER_BRAND}")

    existing = set(r["url"] for r in fetch_all("catalog", "url"))
    print(f"catalog has {len(existing)} urls")

    # deep-gated brands (computed by build_feed_cloud from his live signals): the paid sources
    # sample them once a week instead of daily; grailed is free so it keeps them at full cadence
    import zlib
    deep = set((payload.get("signals", {}) or {}).get("brandLanes", {}).get("deepGatedBrands") or [])
    doy = date.today().timetuple().tm_yday
    def weekly_slot(b): return zlib.crc32(norm(b).encode()) % 7 == doy % 7
    paid_raw   = [b for b in loved_raw if norm(b) not in deep or weekly_slot(b)]
    paid_today = [b for b in todays    if norm(b) not in deep or weekly_slot(b)]
    if len(paid_raw) < len(loved_raw):
        skipped = sorted(set(norm(b) for b in loved_raw) - set(norm(b) for b in paid_raw))
        print(f"paid sources skip {len(skipped)} deep-gated brand(s) today (weekly slot): {skipped}")

    found = []
    if "grailed" in SOURCES:              found += scrape_grailed(todays, loved)      # Grailed Algolia (no Apify)
    if "therealreal" in SOURCES and FIRE: found += scrape_trr(loved, paid_raw)        # TRR GraphQL via Firecrawl (no Apify)
    if "ssense" in SOURCES and FIRE:      found += scrape_ssense(paid_today, loved_raw)
    print(f"scraped {len(found)} raw items")

    rows, seen = filter_new(found, existing, loved)

    # ---- top up: keep pulling FRESH grailed brands until we reach the minimum ----
    if MIN_NEW and "grailed" in SOURCES and len(rows) < MIN_NEW:
        pool = [b for b in loved_raw if b not in todays]
        random.shuffle(pool)
        while len(rows) < MIN_NEW and pool:
            batch, pool = pool[:8], pool[8:]
            more, mu = filter_new(scrape_grailed(batch, loved), existing | seen, loved)
            rows += more; seen |= mu
            print(f"  top-up +{len(more)} -> {len(rows)}/{MIN_NEW} new")
    print(f"{len(rows)} NEW in-size items after filtering")

    # ---- rank each new piece by his EYE, automatically (vision vs the stored rubric) ----
    if NO_VISION and rows:
        # raw scan: no taste rating. The "unrated" tag also exempts these from the feed's taste lanes,
        # so a deliberate search for a gated brand can never come back empty.
        for r in rows:
            r["reasons"] = (r.get("reasons") or []) + ["unrated"]
        print(f"raw scan — {len(rows)} new items kept unrated (no vision pass)")
    elif ANTHROPIC_KEY and brief and rows:
        from concurrent.futures import ThreadPoolExecutor
        def _av(r):
            res = vision_score(r.get("image"), brief)
            if res:
                vf, tags = res
                r["base_score"] = round(min(0.97, max(0.05, 0.15 + 0.80 * vf)), 3)
                if tags: r["reasons"] = tags
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(_av, rows))
        print(f"vision-scored {len(rows)} new items against your visual taste rubric")
    elif rows and not ANTHROPIC_KEY:
        print("no ANTHROPIC_API_KEY set — new items keep the title-based base_score")

    for i in range(0, len(rows), 500):
        part = rows[i:i+500]
        st, res = sb("POST", "/rest/v1/catalog?on_conflict=url", part, {"Prefer":"resolution=ignore-duplicates,return=minimal"})
        if st not in (200,201,204): print("insert fail", st, str(res)[:200]); break
        print(f"  inserted {min(i+500,len(rows))}/{len(rows)}")
    print("CONDUCTOR DONE — new items:", len(rows))

def size_bucket(it):
    s = norm(it.get("size")); cat = it.get("category","")
    if cat == "footwear":
        if "15" in s: return "us15"
        if "14" in s: return "us14"
        if "46" in s or "47" in s or "48" in s: return "eu46-48"
        if "13" in s: return "eu46-48"          # balenciaga eu46 normalized to us13
        return "other"
    if cat == "bottoms":
        if "38" in s: return "w38"
        if "36" in s or "37" in s: return "w36"
        if "xxl" in s or "56" in s: return "XXL"
        if "xl" in s or "54" in s: return "XL"
        if "l" in s or "52" in s: return "L"
        return "other"
    if "xxl" in s or "2xl" in s or "56" in s: return "XXL"
    if "xl" in s or "54" in s: return "XL"
    if "l" in s or "52" in s: return "L"
    return "other"

if __name__ == "__main__":
    main()
