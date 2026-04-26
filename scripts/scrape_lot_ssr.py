#!/usr/bin/env python3
"""Fetch an SSR'd mac.bid lot page and extract the embedded __NEXT_DATA__ JSON.

`lid` is the canonical lot identifier. `aid` is a per-modal routing parameter
needed to build URLs, but the same underlying lot can surface under different
`aid` values depending on which modal (search, watchlist, etc.) opened it —
so the lot's canonical record may carry an `aid` that differs from the one in
the URL we fetched. Cache keys and output identity therefore use `lid` alone;
`aid` is still required to construct candidate URLs and is preserved in the
output for debugging.

This script probes several URL shapes and uses the first one whose
__NEXT_DATA__ actually mentions the `lid`.

Emits a compact projection plus the full parsed blob under `raw`."""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

CACHE_DIR = "/Users/tsaylor/projects/mac-bid-claude/cache/lots"
URL_PATTERN_CACHE = os.path.join(CACHE_DIR, "_ssr_url_pattern.txt")
# URL patterns to probe, in preference order. Each takes {aid} and {lid}.
URL_PATTERNS = (
    "https://www.mac.bid/lot/{aid}/{lid}",
    "https://www.mac.bid/search?aid={aid}&lid={lid}",
    "https://www.mac.bid/account/watchlist?aid={aid}&lid={lid}",
)
# Realistic Chrome-on-macOS UA; plain urllib UAs get 403'd by many CDNs.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT = 20
DEFAULT_TTL = 3600

# Next.js embeds its page data as a JSON island with this exact id+type; DOTALL to span newlines.
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def cache_path(lid: str) -> str:
    # `lid` is the canonical lot identifier; `aid` can vary per-modal for the
    # same underlying lot, so it is deliberately not part of the cache key.
    return os.path.join(CACHE_DIR, f"{lid}.ssr.json")


def load_pattern_hint() -> str:
    """Return the last-known-good URL pattern, or empty string."""
    try:
        with open(URL_PATTERN_CACHE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def save_pattern_hint(pattern: str) -> None:
    try:
        os.makedirs(os.path.dirname(URL_PATTERN_CACHE), exist_ok=True)
        tmp = URL_PATTERN_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(pattern)
        os.replace(tmp, URL_PATTERN_CACHE)
    except OSError:
        pass


def load_cached(path: str, max_age: int):
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > max_age:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_cache(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
        # Respect server charset when present; fall back to utf-8.
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


def extract_next_data(page: str):
    m = NEXT_DATA_RE.search(page)
    if not m:
        return None
    blob = html.unescape(m.group(1))
    return json.loads(blob)


def _dig(d, *path):
    cur = d
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def _first_present(d: dict, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _absolutize(url: str) -> str:
    if not url:
        return url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return "https://www.mac.bid" + url
    return url


def _extract_images(lot_obj) -> list:
    if not isinstance(lot_obj, dict):
        return []
    for k in ("photos", "images", "image_list", "image_urls", "photo_list"):
        v = lot_obj.get(k)
        if not v:
            continue
        out = []
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    out.append(_absolutize(item))
                elif isinstance(item, dict):
                    for ik in ("url", "image_url", "src", "href"):
                        if item.get(ik):
                            out.append(_absolutize(item[ik]))
                            break
            if out:
                return out
    return []


_LOT_KEY_HINTS = (
    "title", "auction_title", "product_name", "productName",
    "condition", "condition_name", "conditionName",
    "upc", "UPC",
)
_LOT_ID_KEYS = ("lid", "lot_id", "lotId", "lot_number", "lotNumber", "id")
_AUCTION_ID_KEYS = ("aid", "auction_id", "auctionId")


def _looks_like_lot(obj, aid: str, lid: str) -> bool:
    """Does this dict smell like a lot record matching (aid, lid)?"""
    if not isinstance(obj, dict):
        return False
    # Must have at least one recognizable lot-ish field.
    if not any(k in obj for k in _LOT_KEY_HINTS):
        return False
    # And one of its id fields should reference the requested lid (string compare).
    lid_s = str(lid)
    for k in _LOT_ID_KEYS:
        if k in obj and str(obj[k]) == lid_s:
            return True
    return False


def _walk_for_lot(node, aid: str, lid: str, depth: int = 0):
    """Best-effort DFS through pageProps for a dict that looks like our lot."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        if _looks_like_lot(node, aid, lid):
            return node
        for v in node.values():
            found = _walk_for_lot(v, aid, lid, depth + 1)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_for_lot(item, aid, lid, depth + 1)
            if found is not None:
                return found
    return None


def project(aid: str, lid: str, raw) -> dict:
    # Try a few known paths, then fall back to a structural walk. Any shape miss
    # yields nulls but raw is preserved. `activeLot` is the currently-confirmed
    # SSR path; the others are historical/defensive fallbacks.
    lot_obj = None
    try:
        for path in (("props", "pageProps", "activeLot"),
                     ("props", "pageProps", "lot"),
                     ("props", "pageProps", "modalLot"),
                     ("props", "pageProps", "lotData")):
            candidate = _dig(raw, *path)
            if isinstance(candidate, dict):
                lot_obj = candidate
                break
        if not isinstance(lot_obj, dict):
            lot_obj = _walk_for_lot(_dig(raw, "props", "pageProps"), aid, lid)
    except Exception:
        lot_obj = None

    out = {
        "aid": str(aid),
        "lid": str(lid),
        "internal_id": None,
        "auction_number": None,
        "title": None,
        "brand": None,
        "upc": None,
        "model": None,
        "condition": None,
        "retail_price": None,
        "instant_win_price": None,
        "buyers_assurance_cost": None,
        "expected_close_date": None,
        "description": None,
        "total_bids": None,
        "unique_bidders": None,
        "is_tested": None,
        "tested_note": None,
        "damaged_note": None,
        "warehouse_location": None,
        "location_id": None,
        "location_name": None,
        "pickup_date": None,
        "lot_fee_override": None,
        "buyers_premium_override": None,
        "category_name": None,
        "image_urls": [],
        "building_id": None,
    }

    if isinstance(lot_obj, dict):
        try:
            auction_obj = lot_obj.get("auction")
            if not isinstance(auction_obj, dict):
                auction_obj = {}

            out["internal_id"] = lot_obj.get("id")
            out["auction_number"] = auction_obj.get("auction_number")
            out["title"] = _first_present(lot_obj, ("auction_title", "title", "product_name", "productName"))
            out["brand"] = _first_present(lot_obj, ("brand", "brand_name"))
            out["upc"] = _first_present(lot_obj, ("upc", "UPC", "barcode"))
            out["model"] = _first_present(lot_obj, ("model", "model_number", "modelNumber"))
            out["condition"] = _first_present(lot_obj, ("condition_name", "condition", "conditionName"))
            out["retail_price"] = _first_present(lot_obj, ("retail_price", "retailPrice", "msrp"))
            out["instant_win_price"] = lot_obj.get("instant_win_price")
            out["buyers_assurance_cost"] = lot_obj.get("buyers_assurance_cost")
            out["expected_close_date"] = lot_obj.get("expected_close_date")
            out["description"] = lot_obj.get("description")
            out["total_bids"] = lot_obj.get("total_bids")
            out["unique_bidders"] = lot_obj.get("unique_bidders")
            out["is_tested"] = lot_obj.get("is_tested")
            out["tested_note"] = lot_obj.get("tested_note")
            out["damaged_note"] = lot_obj.get("damaged_note")
            out["warehouse_location"] = lot_obj.get("warehouse_location")
            out["location_id"] = lot_obj.get("current_location_id")
            out["location_name"] = auction_obj.get("location_name")
            out["pickup_date"] = auction_obj.get("pickup_date")
            out["lot_fee_override"] = auction_obj.get("lot_fee_override")
            out["buyers_premium_override"] = auction_obj.get("buyers_premium_override")
            out["category_name"] = lot_obj.get("category_name")
            # building_id: prefer auction.building_id, fall back to lot-level
            # fields, then to current_location_id (typically the same value).
            building_id = auction_obj.get("building_id")
            if building_id is None:
                building_id = _first_present(lot_obj, ("building_id", "buildingId"))
            if building_id is None:
                building_id = lot_obj.get("current_location_id")
            out["building_id"] = building_id
            out["image_urls"] = _extract_images(lot_obj)
        except Exception:
            pass

    out["raw"] = raw
    return out


def _stdout_projection(obj):
    """Strip the heavyweight `raw` blob before serializing to stdout.

    The cache file on disk keeps `raw` for debugging; downstream callers
    (Claude sub-agents) only need the projected fields."""
    return {k: v for k, v in obj.items() if k != "raw"}


def _try_url(url: str, aid: str, lid: str):
    """Fetch `url`; return parsed __NEXT_DATA__ dict iff it mentions `lid`.
    Returns None on any failure or on a mismatch. Raises on unrecoverable fetch errors
    only when the caller explicitly needs to surface them — here we swallow so we can
    try the next pattern."""
    try:
        page = fetch_html(url)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    try:
        raw = extract_next_data(page)
    except json.JSONDecodeError:
        return None
    if raw is None:
        return None
    # Cheap substring check against the re-serialized blob. Only `lid` must
    # appear — `lid` is the canonical lot identifier, while `aid` is a
    # context-dependent routing parameter that can legitimately differ
    # between the URL we fetched and the lot's canonical record.
    try:
        blob = json.dumps(raw)
    except (TypeError, ValueError):
        return None
    if str(lid) in blob:
        return raw
    return None


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--aid", required=True, help="mac.bid auction id (string)")
    p.add_argument("--lid", required=True, help="mac.bid lot id within auction (string)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--max-age-seconds", type=int, default=DEFAULT_TTL)
    args = p.parse_args()

    aid = str(args.aid)
    lid = str(args.lid)
    path = cache_path(lid)

    if not args.no_cache:
        cached = load_cached(path, args.max_age_seconds)
        if cached is not None:
            sys.stdout.write(json.dumps(_stdout_projection(cached), separators=(",", ":")))
            return 0

    # Prefer last-known-good pattern first, then the rest in declared order.
    hint = load_pattern_hint()
    ordered = list(URL_PATTERNS)
    if hint in ordered:
        ordered.remove(hint)
        ordered.insert(0, hint)

    tried = []
    raw = None
    winning_url = None
    winning_pattern = None
    for pattern in ordered:
        url = pattern.format(aid=aid, lid=lid)
        tried.append(url)
        candidate = _try_url(url, aid, lid)
        if candidate is not None:
            raw = candidate
            winning_url = url
            winning_pattern = pattern
            break

    if raw is None:
        sys.stdout.write(json.dumps({
            "error": "ssr-lot-not-resolved",
            "aid": aid,
            "lid": lid,
            "tried": tried,
            "hint": "Update scripts/scrape_lot_ssr.py with the correct URL pattern — see PROJECT.md §Open questions",
        }, separators=(",", ":")))
        return 1

    if winning_pattern:
        save_pattern_hint(winning_pattern)

    obj = project(aid, lid, raw)
    obj["_ssr_url_used"] = winning_url
    try:
        write_cache(path, obj)
    except OSError as e:
        sys.stderr.write(f"cache write failed: {e}\n")

    sys.stdout.write(json.dumps(_stdout_projection(obj), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
