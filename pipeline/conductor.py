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
import os, sys, json, time, re, urllib.request, urllib.error
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
SOURCES  = [s.strip() for s in os.environ.get("SOURCES", "grailed,therealreal,ssense").split(",") if s.strip()]
MAX_PER_BRAND = int(os.environ.get("MAX_PER_BRAND", "40"))
BRANDS_PER_RUN= int(os.environ.get("BRANDS_PER_RUN", "24"))
TODAY = date.today().isoformat()

def http(method, url, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw[:1] in "[{" else raw)
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

def in_size(cat, s):
    s = norm(s)
    if not s: return False
    if cat == "footwear":
        return bool(re.search(r"\b(13|13\.5|14|47|48)\b", s))
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

def scrape_grailed(brands, loved):
    out = []
    for b in brands:
        items = apify_run("crawlergang~grailed-scraper", {"searchQuery": b, "maxItems": MAX_PER_BRAND})
        if items is None: break                                   # hit the cap
        kept0 = len(out)
        for it in items or []:
            title = it.get("title", "") or ""
            gcat = norm(it.get("category"))
            cat = {"outerwear":"outerwear","tops":"tops","bottoms":"bottoms","footwear":"footwear","tailoring":"outerwear"}.get(gcat) or infer_cat(title)
            if cat not in CATS: continue
            cond = {"is_new":"new","is_gently_used":"gently used"}.get(norm(it.get("condition")))
            if not cond: continue                                  # drop is_used / is_worn (condition is a string)
            url = (it.get("sourceUrl") or "").split("?")[0]
            img = (it.get("photoUrls") or [None])[0]
            brand = (it.get("designerNames") or [b])[0]
            out.append({"url":url,"id":str(it.get("listingId") or ""),"platform":"grailed","brand":brand,
                        "title":title,"category":cat,"price":it.get("price"),"size":it.get("size",""),"condition":cond,
                        "image":(img or "").split("?")[0]})
        print(f"  grailed '{b}': {len(items or [])} raw -> {len(out)-kept0} kept")
    return out

def scrape_trr(loved, loved_raw):
    urls = ["/shop/men/clothing/outerwear","/shop/men/clothing/sweaters-sweatshirts",
            "/shop/men/clothing/pants","/shop/men/clothing/jeans","/shop/men/clothing/shirts","/shop/men/shoes"]
    out = []
    for path in urls:
        items = apify_run("piotrv1001~the-realreal-listings-scraper",
                          {"startUrls":[{"url":"https://www.therealreal.com"+path}],"maxItems":300,
                           "proxyConfiguration":{"useApifyProxy":True,"apifyProxyGroups":["RESIDENTIAL"],"apifyProxyCountryCode":"US"}},
                          memory=2048)
        if items is None: break
        for it in items or []:
            if norm(it.get("gender")) not in ("men", "male", "mens"): continue   # drop women's leakage
            brand = it.get("designer") or it.get("brand") or ""
            canon = brand_matches(brand, loved_raw)
            if not canon: continue                                # TRR: keep only loved brands
            title = it.get("name") or it.get("title") or ""
            cat = infer_cat(title)
            if cat not in CATS: continue
            sizeparts = []                                        # size lives in typed attributes, not a 'size' field
            for a in (it.get("attributes") or []):
                if a.get("type") in ("CLOTHING_SIZE", "SHOE_SIZE", "MENS_WAIST"):
                    sizeparts += [str(v) for v in (a.get("values") or [])]
            size = " ".join(sizeparts)
            price = None
            try: price = it.get("price",{}).get("final",{}).get("usdCents",0)/100 or None
            except Exception: pass
            url = (it.get("url") or it.get("sourceUrl") or "").split("?")[0]
            img = (it.get("images") or [None])[0]
            out.append({"url":url,"id":str(it.get("id") or ""),"platform":"therealreal","brand":canon,
                        "title":title,"category":cat,"price":price,"size":size,
                        "condition":it.get("condition") or "gently used","image":(img or "").split("?")[0]})
    return out

def firecrawl_scrape(url, schema, proxy="auto", wait=9000):
    st, r = http("POST", "https://api.firecrawl.dev/v2/scrape",
                 {"url":url,"formats":[{"type":"json","schema":schema}],"waitFor":wait,"proxy":proxy},
                 {"Authorization":"Bearer "+FIRE}, timeout=180)
    if st != 200 or not isinstance(r, dict): return None
    return (r.get("data") or {}).get("json")

def scrape_ssense(brands, loved_raw, per_brand=6):
    # SSENSE needs the stealth proxy + a proper JSON Schema, or the extraction comes back empty.
    LIST = {"type":"object","properties":{"products":{"type":"array","items":{"type":"object","properties":{
        "name":{"type":"string"},"price":{"type":"number"},"url":{"type":"string"},"image":{"type":"string"}}}}}}
    SIZE = {"type":"object","properties":{"name":{"type":"string"},
        "sizesAvailable":{"type":"array","items":{"type":"string"}}}}
    out = []
    for b in brands[:8]:                                          # light pass to control credits
        slug = re.sub(r"[^a-z0-9]+","-",norm(b)).strip("-")
        data = firecrawl_scrape(f"https://www.ssense.com/en-us/men/designers/{slug}", LIST, proxy="stealth", wait=12000)
        prods = (data or {}).get("products") or []
        for p in prods[:per_brand]:
            title = p.get("name",""); cat = infer_cat(title)
            if cat not in CATS: continue
            purl = (p.get("url") or "").split("?")[0]
            if not purl: continue
            szdata = firecrawl_scrape(purl, SIZE, proxy="stealth", wait=9000) or {}       # sizes are on the product page
            sizes = " ".join(szdata.get("sizesAvailable") or [])
            if not in_size(cat, sizes): continue
            m = re.search(r"(\d+)$", purl)
            out.append({"url":purl,"id":(m.group(1) if m else purl),"platform":"ssense","brand":b,
                        "title":title,"category":cat,"price":p.get("price"),"size":sizes,
                        "condition":"new","image":(p.get("image") or "").split("?")[0]})
    return out

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

def main():
    # loved brands from Charles's taste row (first taste row = him for now)
    st, tastes = sb("GET", "/rest/v1/taste?select=payload&limit=1")
    loved_raw = ((tastes or [{}])[0].get("payload", {}).get("brands", {}) or {}).get("loved", []) if tastes else []
    loved = set(norm(b) for b in loved_raw)
    if not loved_raw:
        print("no loved brands found — aborting"); return
    # brands: explicit override (on-demand / testing) OR a rotating slice to bound daily cost
    override = os.environ.get("BRANDS", "").strip()
    if override:
        todays = [b.strip() for b in override.split(",") if b.strip()]
    else:
        n = min(BRANDS_PER_RUN, len(loved_raw))
        off = (date.today().toordinal() * n) % len(loved_raw)
        todays = [loved_raw[(off + i) % len(loved_raw)] for i in range(n)]
    print(f"loved brands: {len(loved_raw)} | this run scrapes {len(todays)}: {todays[:6]}{'...' if len(todays)>6 else ''}")
    print(f"sources: {SOURCES} | max/brand: {MAX_PER_BRAND}")

    existing = set(r["url"] for r in fetch_all("catalog", "url"))
    print(f"catalog has {len(existing)} urls")

    found = []
    if "grailed" in SOURCES and APIFY:    found += scrape_grailed(todays, loved)
    if "therealreal" in SOURCES and APIFY: found += scrape_trr(loved, loved_raw)
    if "ssense" in SOURCES and FIRE:      found += scrape_ssense(todays, loved_raw)
    print(f"scraped {len(found)} raw items")

    rows = []; seen = set()
    for it in found:
        url = it.get("url")
        if not url or not it.get("image") or not it.get("brand"): continue
        if url in existing or url in seen: continue                # only genuinely new
        cat = it["category"]
        if cat not in CATS: continue
        if not in_size(cat, it.get("size")): continue
        if cat == "bottoms" and re.search(r"\b3[45]\b", norm(it.get("size"))): continue
        if is_blazer(it.get("title")): continue
        seen.add(url)
        tags = tags_from(it["title"])
        rows.append({**it, "reasons":tags, "base_score":base_score(it["brand"], tags, loved),
                     "sz":size_bucket(it), "first_seen":TODAY, "last_seen":TODAY})
    print(f"{len(rows)} NEW in-size items after filtering")

    for i in range(0, len(rows), 500):
        part = rows[i:i+500]
        st, res = sb("POST", "/rest/v1/catalog?on_conflict=url", part, {"Prefer":"resolution=ignore-duplicates,return=minimal"})
        if st not in (200,201,204): print("insert fail", st, str(res)[:200]); break
        print(f"  inserted {min(i+500,len(rows))}/{len(rows)}")
    print("CONDUCTOR DONE — new items:", len(rows))

def size_bucket(it):
    s = norm(it.get("size")); cat = it.get("category","")
    if cat == "footwear":
        if "14" in s: return "us14"
        if "13" in s: return "us13"
        if "47" in s or "48" in s or "49" in s: return "eu47-49"
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
