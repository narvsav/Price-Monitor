# Component Price Desk

A small self-hosted system: a Python collector pulls UK offers once a day via
GitHub Actions, logs them to JSON in this repo, and a dashboard (`index.html`,
hosted free on GitHub Pages) reads that JSON directly to show current prices,
cheapest retailer, stock, margin, 7-day change, and PRICE UP / PRICE DOWN /
LOW STOCK flags, plus two charts.

## Before you start — read this

- **PriceAPI is not free.** It has a free trial, then paid credits. Check
  current pricing at priceapi.com before running this on a schedule against
  your full product list. If you'd rather stay fully free, see "Free
  alternative" below.
- **"Mercury Dynamics"** (mentioned as an alternative data source) could not
  be verified — no matching product, docs, or company was found in search. Do
  not build against it without a working link.
- **PriceAPI's sources are broad marketplaces** (Google Shopping, Amazon,
  eBay, Idealo, Pricerunner, etc.), not direct connections to Scan,
  Overclockers UK, CCL or Box specifically. This collector queries Google
  Shopping UK (`country: gb`), so which UK retailers actually show up depends
  on who lists there for each product — it's not guaranteed to include your
  four preferred retailers.
- **The exact shape of an individual offer (retailer name, price, stock
  field names) is not verified** against a real PriceAPI response — their
  public docs didn't expose a full sample. `collector.py` guesses at common
  field names and also saves the raw first response to
  `data/debug_last_raw.json` on every run, specifically so you can check it
  after your first real run and fix `extract_offers()` in `collector.py` if
  the field names differ.

## Setup

1. **Create a PriceAPI account** at priceapi.com and get your API token.
2. **Create a new GitHub repository** and push all these files to it
   (`git init`, `git add .`, `git commit`, `git remote add origin ...`,
   `git push`).
3. **Add your token as a secret**: repo Settings → Secrets and variables →
   Actions → New repository secret → name it `PRICEAPI_TOKEN`, paste your
   token.
4. **Enable GitHub Pages**: repo Settings → Pages → Source → Deploy from a
   branch → Branch: `main`, folder: `/ (root)`. Save. GitHub will give you a
   URL like `https://yourname.github.io/your-repo/` — that's your live
   dashboard.
5. **Run the collector once manually** to seed data before waiting for the
   schedule: repo → Actions tab → "Daily Price Collector" → Run workflow.
   Watch the run log for errors.
6. **Check `data/debug_last_raw.json`** after that first run (pull the repo
   or view it on GitHub) to confirm `extract_offers()` in `collector.py` is
   reading the right fields. Adjust and re-run if prices come back empty.
7. From here, it runs on its own daily at 07:30 UTC. You can still trigger it
   manually anytime from the Actions tab.

## Editing your product list

Open `products.json`. Each entry:

```json
{ "id": "gpu-5070", "category": "GPU", "component": "RTX 5070",
  "search_term": "NVIDIA RTX 5070 graphics card", "your_price": 599.00 }
```

- `id` — unique, no spaces, used to match history rows to this product.
- `search_term` — what gets searched on Google Shopping UK. More specific
  terms match better but may return fewer offers.
- `your_price` — what you plan to charge; used to calculate "Your Margin" on
  the dashboard. Not fetched from anywhere — you set it.

## Settings (alert thresholds)

Near the top of the `<script>` block in `index.html`:

```js
const PRICE_ALERT_THRESHOLD = 0.05;   // 5%
const MIN_RETAILERS_IN_STOCK = 3;
```

Edit and re-push to change these.

## Free version (no PriceAPI cost)

`collector_free.py` is a drop-in alternative to `collector.py` that costs
nothing — no PriceAPI account, no token. It fetches each retailer's product
page directly and reads the price out of the raw HTML with an XPath, the
same technique as the earlier Google Sheets `IMPORTXML` version, just run
from GitHub Actions instead of Apps Script. Its output files are in the
exact same format as the paid collector's, so `index.html` works unchanged
with either one.

**Trade-off vs the paid version:** you supply the exact product URL per
retailer yourself (no search), and it only sees retailers whose pages don't
block scrapers or hide the price behind JavaScript.

### Setup

1. **Use only one collector workflow.** In `.github/workflows/`, keep either
   `daily.yml` (paid, PriceAPI) or `daily-free.yml` (free, scraping)
   enabled — not both. Delete or disable whichever you're not using (repo →
   Actions tab → select the workflow → "..." menu → Disable workflow).
2. **Fill in `products.json`.** Each product already has a `urls` object with
   one blank slot per retailer:
   ```json
   "urls": { "Scan": "", "Overclockers UK": "", "CCL": "", "Box": "" }
   ```
   Paste the exact product page URL for whichever retailers you want tracked
   for that product. Leave a retailer blank to skip it for that product.
3. **Check `retailers.json`.** This is where each retailer's price-finding
   method is set once and reused for every product:
   - **Scan** — already set and confirmed working (`//*[@itemprop='price']/@content`).
   - **Overclockers UK** — already set and confirmed working (reads price
     out of the page's JSON-LD block).
   - **CCL** — a generic placeholder, not verified. Open one real CCL
     product page, right-click the price → Inspect → Copy XPath, and paste
     it into `price_xpath` for CCL.
   - **Box** — during testing for this project, Box's product pages could
     not be fetched at all (the request was blocked). Test this yourself
     before relying on it; it may simply not work with this method for Box
     specifically.
4. **Push to GitHub**, add repo secret if using the paid workflow, enable
   GitHub Pages (see steps 2 and 4 above either way), and run the enabled
   workflow manually once from the Actions tab to seed data.

### Finding an XPath (once per retailer, not per product)

Right-click the price on a real product page → **Inspect** → in DevTools,
right-click the highlighted line → **Copy** → **Copy XPath**. Paste it into
that retailer's `price_xpath` in `retailers.json`. If it returns nothing,
try **View page source** (not Inspect) and search for the price digits — if
they're not in the raw source at all, that retailer loads price via
JavaScript and this method won't work for it.

For stock status, do the same on the availability text (e.g. "In stock") and
paste into `stock_xpath`. It's optional — leaving it blank means that
retailer's stock is treated as unknown (counted as in-stock rather than
excluded).

## Files

- `products.json` — your component list, with both `search_term` (paid path)
  and `urls` (free path) so it works with whichever collector you use.
- `collector.py` — paid path: calls PriceAPI, writes `data/`.
- `collector_free.py` — free path: fetches retailer pages directly and reads
  price/stock via XPath, writes `data/` in the same format.
- `retailers.json` — free path only: one price/stock XPath per retailer,
  reused across every product from that retailer.
- `.github/workflows/daily.yml` — paid collector schedule (07:30 UTC) + manual trigger.
- `.github/workflows/daily-free.yml` — free collector schedule (07:30 UTC) +
  manual trigger. Enable only one of these two workflows.
- `index.html` — the dashboard, reads `products.json` and `data/*.json`
  directly (same-origin fetch, works on GitHub Pages).
- `data/price_history.json` — one row per product per day, appended forever.
- `data/latest.json` — today's full snapshot per product, including every
  offer found (used for "cheapest retailer" etc).
- `data/debug_last_raw.json` — raw API response from the first product in
  the most recent run, for debugging field names.
