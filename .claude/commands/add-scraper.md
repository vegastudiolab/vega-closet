---
name: add-scraper
description: >
  Add a new website source to the Vega Closet daily conductor pipeline.
  Use this whenever Charles wants to pull in a new menswear marketplace
  (e.g. "add Farfetch", "add END Clothing", "add StockX"). Guides you through
  API discovery, writing the scraper function, wiring category/size maps,
  and testing end-to-end via a GitHub Actions dispatch.
---

# Adding a New Source to Vega Closet

## The pattern

Every source in `web/pipeline/conductor.py` follows the same four-step shape:

1. **Discover the API** — find the hidden JSON endpoint the site's own app uses (no scraping HTML)
2. **Write `scrape_<source>(brands, loved)`** — returns a list of raw item dicts
3. **Wire into the conductor loop** — add to `SOURCES` env check + the top-up `min_new` loop
4. **Map categories and sizes** — normalize to the shared schema so filters work in the app

---

## Step 1: API Discovery

Before writing any code, find the real API. Run this in the browser DevTools Network tab on the site's search or new-arrivals page:

- Filter by `fetch` / `XHR`
- Look for JSON responses with item arrays — usually named `products`, `items`, `hits`, or `edges`
- Copy the request as cURL (right-click → Copy as cURL)

**Common patterns found so far:**

| Site | API type | Notes |
|------|----------|-------|
| Grailed | Algolia (`MNRWEFSS2Q`) | Direct server-to-server, no proxy |
| The RealReal | GraphQL (`api.therealreal.com/graphql`) | Behind PerimeterX → must use Firecrawl `proxy:stealth` |
| SSENSE | Next.js RSC / HTML | Use Firecrawl scrape; images from `og:image` meta tag |

If the endpoint is behind a bot-wall (Cloudflare, PerimeterX, Kasada):
- Try direct first (`http()` helper)
- Fall back to Firecrawl `proxy:stealth` (already wired in the file)
- Last resort: Firecrawl `crawl` with `waitFor:2000`

---

## Step 2: Write the Scraper Function

Add it to `web/pipeline/conductor.py`. Use this template:

```python
# ── NEW SOURCE ──────────────────────────────────────────
NEWSOURCE_CATS = {
    # site's internal category → shared schema value
    # shared values: "outerwear" | "tops" | "bottoms" | "footwear" | "accessories"
    "jackets": "outerwear",
    "shirts":  "tops",
    "pants":   "bottoms",
    "shoes":   "footwear",
    "belts":   "accessories",
}

def scrape_newsource(brands, loved):
    out = []
    for brand in brands:
        st, r = http("GET", f"https://api.example.com/search?brand={brand}&sort=newest")
        if st != 200:
            continue
        for item in (r.get("products") or []):
            size = item.get("size", "")
            if not in_size(size):        # shared size filter — reuse this
                continue
            cat_raw = item.get("category", "")
            cat = NEWSOURCE_CATS.get(cat_raw, "other")
            out.append({
                "url":       item["url"],           # MUST be unique and stable
                "title":     item["title"],
                "brand":     item.get("designer", brand),
                "price":     float(item.get("price", 0)),
                "image":     item.get("image_url", ""),
                "size":      size,
                "condition": item.get("condition", ""),
                "category":  cat,
                "platform":  "newsource",
                "isNew":     True,
            })
    print(f"  newsource: {len(out)} raw listings")
    return out
```

**Key rules:**
- `url` is the primary key — it must be the canonical product URL (not a search hit ID). IDs collide across sources.
- Run all items through `in_size()` — it already knows Charles's size range.
- Map every category to the shared schema values. Unknown categories → `"other"`.
- `platform` value must be a short slug (used in filter chips in the app).

---

## Step 3: Wire Into the Conductor Loop

Find the main loop in `conductor.py` (around line 355) and add two lines:

```python
# ── scrape phase ──
if "grailed"    in SOURCES: found += scrape_grailed(todays, loved)
if "therealreal" in SOURCES: found += scrape_trr(loved, loved_raw)
if "ssense"     in SOURCES: found += scrape_ssense(loved)
if "newsource"  in SOURCES: found += scrape_newsource(todays, loved)   # ← add this
```

And in the `min_new` top-up loop (searches for `# top-up` comment):

```python
if total_new < MIN_NEW and "newsource" in SOURCES:
    more, mu = filter_new(scrape_newsource(batch, loved), existing | seen, loved)
    ...
```

---

## Step 4: Add the Platform Label in the App

In `web/index.html`, find this line (~line 198):

```javascript
const platLabel = { grailed:"grailed", therealreal:"the realreal", ssense:"ssense" };
```

Add the new source:

```javascript
const platLabel = { grailed:"grailed", therealreal:"the realreal", ssense:"ssense", newsource:"new source name" };
```

And add it to the `PLATS` filter array (~line 326):

```javascript
const PLATS = [["all","all"],["grailed","grailed"],["the realreal","therealreal"],["ssense","ssense"],["new source","newsource"]];
```

---

## Step 5: Test the Integration

1. Commit and push the changes to `main`
2. Go to the GitHub Actions tab for `vegastudiolab/vega-closet`
3. Click `vega-closet conductor` → `Run workflow`
4. Set `sources` to just the new source (e.g. `newsource`)
5. Set `min_new` to `10` for a quick test
6. Watch the logs for:
   - `  newsource: N raw listings` — confirms API is responding
   - `M NEW in-size items after filtering` — confirms size filter works
   - `CONDUCTOR DONE — new items: M` — confirms DB write succeeded

Or trigger via the RPC (scan now button in the app) after updating `run_conductor.sql` to include the new source in the sources list.

---

## Checklist

- [ ] API endpoint discovered and returning JSON
- [ ] `scrape_newsource()` written and returning items with correct schema
- [ ] `in_size()` filter applied
- [ ] Categories mapped to shared schema values
- [ ] `platform:` slug set on every item
- [ ] Wired into conductor loop (both main scrape and min_new top-up)
- [ ] `platLabel` and `PLATS` updated in `index.html`
- [ ] Test run dispatched and logs checked
- [ ] Women's / non-menswear items excluded (add gender facet if the API supports it)
