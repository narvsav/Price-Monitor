#!/usr/bin/env python3
"""
Daily UK price collector.

Reads products.json, asks PriceAPI (priceapi.com) for Google Shopping UK
offers on each product, and appends today's results to data/price_history.json
plus a full snapshot in data/latest.json for the dashboard.

Confirmed against PriceAPI's own docs (readme.priceapi.com) as of the build
date: base URL, auth via `token`, and the create-job / poll-status / download
workflow are all verified. What is NOT verified: the exact field names inside
an individual offer object (e.g. whether a retailer name comes back as
"shop_name", "merchant" or something else) - PriceAPI's public docs didn't
expose a full sample response body. This script tries several common field
names defensively and also writes the raw response to data/debug_last_raw.json
so you can inspect it after your first real run and adjust extract_offers()
if needed.

Environment variables required:
    PRICEAPI_TOKEN   - your PriceAPI API token (set as a GitHub Actions secret)

Free-tier note: PriceAPI is a PAID service with a free trial, not a free API.
Check https://www.priceapi.com pricing before running this against your full
product list on a schedule.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = "https://priceapi.metoda.com/v2"
SOURCE = "google-shopping"   # PriceAPI source: see readme.priceapi.com "Available sources"
COUNTRY = "gb"               # confirmed: gb = Great Britain
TOPIC = "offers"             # returns a list of merchant offers for the matched product

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.json"
HISTORY_FILE = ROOT / "data" / "price_history.json"
LATEST_FILE = ROOT / "data" / "latest.json"
DEBUG_FILE = ROOT / "data" / "debug_last_raw.json"

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 180
PAUSE_BETWEEN_PRODUCTS_SECONDS = 3


def get_token():
    token = os.environ.get("PRICEAPI_TOKEN")
    if not token:
        sys.exit("ERROR: PRICEAPI_TOKEN environment variable is not set.")
    return token


def create_job(token, search_term):
    resp = requests.post(
        f"{BASE_URL}/jobs",
        data={
            "token": token,
            "source": SOURCE,
            "country": COUNTRY,
            "topic": TOPIC,
            "key": "term",
            "values": search_term,
            "max_age": 1440,   # accept cached data up to 24h old - keeps cost down
            "max_pages": 1,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["job_id"]


def wait_for_job(token, job_id):
    elapsed = 0
    while elapsed < POLL_TIMEOUT_SECONDS:
        r = requests.get(f"{BASE_URL}/jobs/{job_id}", params={"token": token}, timeout=30)
        r.raise_for_status()
        status = r.json().get("status")
        if status == "finished":
            return True
        if status in ("failed", "cancelled"):
            print(f"  job {job_id} ended with status={status}", file=sys.stderr)
            return False
        time.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
    print(f"  job {job_id} timed out after {POLL_TIMEOUT_SECONDS}s", file=sys.stderr)
    return False


def download_results(token, job_id):
    r = requests.get(
        f"{BASE_URL}/jobs/{job_id}/download",
        params={"token": token, "format": "json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def extract_offers(raw):
    """
    Best-effort parse of a PriceAPI 'offers' job result into a flat list of
    {retailer, price, in_stock, url}. Field names are not verified against a
    real sample response (see module docstring) - adjust if debug_last_raw.json
    shows different keys.
    """
    offers = []
    results = raw.get("results", []) if isinstance(raw, dict) else []
    for result in results:
        content = result.get("content", {}) if isinstance(result, dict) else {}
        raw_offers = content.get("offers") or content.get("items") or []
        for o in raw_offers:
            if not isinstance(o, dict):
                continue
            retailer = o.get("shop_name") or o.get("merchant") or o.get("source") or o.get("seller")
            price = o.get("price") or o.get("price_value")
            availability = str(o.get("availability") or o.get("stock") or "").lower()
            in_stock = availability not in ("", "out of stock", "unavailable", "sold out", "0")
            url = o.get("url") or o.get("offer_url")
            if retailer is None or price is None:
                continue
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            offers.append({
                "retailer": retailer,
                "price": price,
                "in_stock": in_stock,
                "url": url,
            })
    return offers


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"  warning: {path} was unreadable, starting fresh", file=sys.stderr)
    return default


def main():
    token = get_token()
    products = json.loads(PRODUCTS_FILE.read_text())

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = load_json(HISTORY_FILE, [])
    latest = {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    last_raw_saved = False

    for product in products:
        pid = product["id"]
        print(f"Collecting: {product['component']} ({product['category']})")
        try:
            job_id = create_job(token, product["search_term"])
            if not wait_for_job(token, job_id):
                print(f"  skipping {pid}: job did not finish", file=sys.stderr)
                continue
            raw = download_results(token, job_id)

            if not last_raw_saved:
                DEBUG_FILE.write_text(json.dumps(raw, indent=2))
                last_raw_saved = True

            offers = extract_offers(raw)
        except requests.RequestException as e:
            print(f"  skipping {pid}: request failed - {e}", file=sys.stderr)
            continue

        in_stock_offers = [o for o in offers if o["in_stock"]]
        lowest_price = min((o["price"] for o in in_stock_offers), default=None)
        cheapest = min(in_stock_offers, key=lambda o: o["price"], default=None)

        history.append({
            "date": today,
            "product_id": pid,
            "category": product["category"],
            "component": product["component"],
            "lowest_price": lowest_price,
            "retailers_in_stock": len(in_stock_offers),
        })

        latest[pid] = {
            "category": product["category"],
            "component": product["component"],
            "your_price": product.get("your_price"),
            "lowest_price": lowest_price,
            "cheapest_retailer": cheapest["retailer"] if cheapest else None,
            "retailers_in_stock": len(in_stock_offers),
            "offers": offers,
            "updated": datetime.now(timezone.utc).isoformat(),
        }

        time.sleep(PAUSE_BETWEEN_PRODUCTS_SECONDS)

    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    LATEST_FILE.write_text(json.dumps(latest, indent=2))
    print(f"Done. {len(history)} total history rows logged, {len(latest)} products updated today.")


if __name__ == "__main__":
    main()
