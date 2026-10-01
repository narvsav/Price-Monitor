#!/usr/bin/env python3
"""
Free daily UK price collector - no paid API.

Reads products.json (each product's "urls" object mapping retailer name ->
product page URL) and retailers.json (each retailer's scraping method/XPath,
set once and reused for every product from that retailer). Fetches each URL
directly, extracts price (and stock, if configured), and appends today's
results to data/price_history.json plus a full snapshot in data/latest.json.

Output schema is identical to collector.py (the PriceAPI version), so
index.html needs no changes regardless of which collector you use.

IMPORTANT - only enable ONE collector workflow at a time (this one or
collector.py's). Running both on the same day will produce two separate log
entries per product for the same date and confuse the 30-day average and
margin figures.

Known limitations (same as the manual IMPORTXML version this is based on):
  - Only works for retailers that render price/stock into the raw HTML.
    JavaScript-rendered prices won't be seen (Amazon and some others block
    or hide data this way entirely).
  - Retailers can and do change their page markup, which breaks an XPath
    without warning. If a product's price stops appearing, check
    retailers.json first.
  - No search built in - you must paste the exact product URL per retailer
    into products.json yourself. This script does not discover URLs.
"""

import os
import re
import sys
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from lxml import html as lxml_html

ROOT = Path(__file__).parent
PRODUCTS_FILE = ROOT / "products.json"
RETAILERS_FILE = ROOT / "retailers.json"
HISTORY_FILE = ROOT / "data" / "price_history.json"
LATEST_FILE = ROOT / "data" / "latest.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}
REQUEST_TIMEOUT_SECONDS = 20
PAUSE_BETWEEN_REQUESTS_SECONDS = 4   # be polite to retailer servers

IN_STOCK_PATTERN = re.compile(r"in stock|available|add to (basket|cart)", re.I)
OUT_OF_STOCK_PATTERN = re.compile(r"out of stock|sold out|unavailable|notify me", re.I)
PRICE_PATTERN = re.compile(r'"price"\s*:\s*"?([0-9]+\.?[0-9]*)"?')


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            print(f"  warning: {path} was unreadable, using default", file=sys.stderr)
    return default


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    r.raise_for_status()
    return r.text


def node_text(node):
    """XPath can return a string (e.g. from /@attr or /text()) or an lxml
    Element (e.g. from //span[...]). str(element) does NOT give its visible
    text - it gives a debug repr like '<Element span at 0x...>'. Always route
    through this so both cases give the actual text content."""
    if isinstance(node, str):
        return node
    if hasattr(node, "text_content"):
        return node.text_content()
    return str(node)


def extract_price_xpath(tree, xpath):
    if not xpath:
        return None
    matches = tree.xpath(xpath)
    if not matches:
        return None
    cleaned = re.sub(r"[^0-9.]", "", node_text(matches[0]))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_price_jsonld(tree):
    for script in tree.xpath("//script[@type='application/ld+json']/text()"):
        m = PRICE_PATTERN.search(script)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def extract_stock(tree, xpath):
    """Returns True (in stock), False (out of stock), or None (unknown/not configured)."""
    if not xpath:
        return None
    matches = tree.xpath(xpath)
    if not matches:
        return None
    text = " ".join(node_text(m) for m in matches)
    if OUT_OF_STOCK_PATTERN.search(text):
        return False
    if IN_STOCK_PATTERN.search(text):
        return True
    return None


def get_offer(url, retailer_cfg):
    html_text = fetch(url)
    tree = lxml_html.fromstring(html_text)
    if retailer_cfg.get("method") == "jsonld":
        price = extract_price_jsonld(tree)
    else:
        price = extract_price_xpath(tree, retailer_cfg.get("price_xpath", ""))
    stock = extract_stock(tree, retailer_cfg.get("stock_xpath", ""))
    return price, stock


def main():
    products = load_json(PRODUCTS_FILE, [])
    retailers = {r["name"]: r for r in load_json(RETAILERS_FILE, [])}

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = load_json(HISTORY_FILE, [])
    latest = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for product in products:
        pid = product["id"]
        print(f"Collecting: {product['component']} ({product['category']})")
        offers = []

        for retailer_name, url in (product.get("urls") or {}).items():
            if not url:
                continue
            cfg = retailers.get(retailer_name)
            if not cfg:
                print(f"  no retailers.json entry for '{retailer_name}', skipping", file=sys.stderr)
                continue

            try:
                price, stock = get_offer(url, cfg)
            except requests.RequestException as e:
                print(f"  {retailer_name}: request failed - {e}", file=sys.stderr)
                continue
            except Exception as e:
                print(f"  {retailer_name}: parse error - {e}", file=sys.stderr)
                continue

            if price is None:
                print(f"  {retailer_name}: no price found - check price_xpath in retailers.json", file=sys.stderr)
                continue

            # Unknown stock status (no stock_xpath configured, or nothing matched)
            # is treated as in-stock rather than silently dropping the offer -
            # this matches the earlier Sheets version's behaviour.
            offers.append({
                "retailer": retailer_name,
                "price": price,
                "in_stock": stock if stock is not None else True,
                "url": url,
            })
            time.sleep(PAUSE_BETWEEN_REQUESTS_SECONDS)

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

    HISTORY_FILE.write_text(json.dumps(history, indent=2))
    LATEST_FILE.write_text(json.dumps(latest, indent=2))
    print(f"Done. {len(history)} total history rows logged, {len(latest)} products updated today.")


if __name__ == "__main__":
    main()
