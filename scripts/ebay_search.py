#!/usr/bin/env python3
"""eBay sold-comps search primitive.

Single-call tool for the cascade-search orchestrator. Given a query and/or UPC
plus optional condition whitelist, returns compact JSON with comp count, median
price, price range, sample titles, and a compact comps array.

LIMITATION: The eBay Browse API (item_summary/search) returns *active* listings,
not sold listings. True sold-comp data requires either the deprecated Finding
API or the Marketplace Insights API (restricted access, application required).
For scaffolding, active-listing medians are a reasonable first-pass
approximation. On first wire-up, revisit whether we need Marketplace Insights
access or a different workaround. See PROJECT.md for the open question.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CACHE_DIR = "/Users/tsaylor/projects/mac-bid-claude/cache/ebay"
TOKEN_CACHE = os.path.join(CACHE_DIR, "_token.json")
DEFAULT_TTL = 86400  # 24h

OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"

# eBay numeric condition IDs. LIKE_NEW and OPEN_BOX both map to 1500
# ("New other / open box") which is eBay's closest equivalent.
CONDITION_MAP = {
    "NEW": "1000",
    "LIKE_NEW": "1500",
    "OPEN_BOX": "1500",
    "USED": "3000",
}


OP_PATH_APP_ID = "op://Personal/za7ym3agvpwbszokahxsfr5sq4/username"
OP_PATH_CERT_ID = "op://Personal/za7ym3agvpwbszokahxsfr5sq4/credential"

# Local credential cache populated by scripts/refresh_ebay_credentials.py.
# Lets routine runs skip the per-call Touch ID prompt from `op read`.
CRED_CACHE_FILE = os.path.expanduser("~/.config/mac-bid-claude/ebay.env")


def _op_read(path):
    import subprocess
    try:
        r = subprocess.run(["op", "read", path], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return None, "op CLI not found on PATH"
    except subprocess.TimeoutExpired:
        return None, "op read timed out (10s)"
    if r.returncode != 0:
        return None, (r.stderr or "").strip() or f"op read failed (code {r.returncode})"
    return r.stdout.strip(), None


def _load_cred_cache():
    if not os.path.exists(CRED_CACHE_FILE):
        return
    try:
        with open(CRED_CACHE_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


def resolve_credentials():
    _load_cred_cache()
    # Env vars (including those just loaded from the cache file) win.
    app_id = os.environ.get("EBAY_APP_ID")
    cert_id = os.environ.get("EBAY_CERT_ID")
    errs = []
    if not app_id:
        app_id, err = _op_read(OP_PATH_APP_ID)
        if err:
            errs.append(f"app_id: {err}")
    if not cert_id:
        cert_id, err = _op_read(OP_PATH_CERT_ID)
        if err:
            errs.append(f"cert_id: {err}")
    if not app_id or not cert_id:
        print(
            "Could not resolve eBay credentials. "
            f"Run `python3 scripts/refresh_ebay_credentials.py` to populate {CRED_CACHE_FILE}, "
            "set EBAY_APP_ID / EBAY_CERT_ID env vars, "
            f"or ensure 1Password CLI is signed in so these paths resolve: "
            f"{OP_PATH_APP_ID}, {OP_PATH_CERT_ID}. "
            f"Errors: {'; '.join(errs) if errs else 'none'}",
            file=sys.stderr,
        )
        sys.exit(1)
    return app_id, cert_id


def die_json(obj, code=1):
    sys.stdout.write(json.dumps(obj, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.exit(code)


def ensure_cache_dir():
    os.makedirs(CACHE_DIR, exist_ok=True)


def load_cached_token():
    if not os.path.exists(TOKEN_CACHE):
        return None
    try:
        with open(TOKEN_CACHE, "r") as f:
            data = json.load(f)
        if data.get("expires_at", 0) > time.time() + 60:
            return data.get("access_token")
    except (OSError, ValueError):
        return None
    return None


def save_token(access_token, expires_in):
    ensure_cache_dir()
    payload = {
        "access_token": access_token,
        "expires_at": time.time() + int(expires_in),
    }
    tmp = TOKEN_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, TOKEN_CACHE)


def fetch_token(app_id, cert_id):
    cached = load_cached_token()
    if cached:
        return cached

    basic = base64.b64encode(f"{app_id}:{cert_id}".encode("utf-8")).decode("ascii")
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        OAUTH_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        die_json({"error": "ebay-auth-failed", "detail": f"HTTP {e.code}: {detail}"})
    except (urllib.error.URLError, TimeoutError) as e:
        die_json({"error": "ebay-auth-failed", "detail": str(e)})

    token = data.get("access_token")
    expires_in = data.get("expires_in", 7200)
    if not token:
        die_json({"error": "ebay-auth-failed", "detail": "no access_token in response"})
    save_token(token, expires_in)
    return token


def canonical_key(query, upc, condition, limit):
    cond = ",".join(sorted(condition)) if condition else ""
    canonical = f"{query or ''}|{upc or ''}|{cond}|{limit}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def cache_path_for(key):
    return os.path.join(CACHE_DIR, f"{key}.json")


def load_cache(key, ttl):
    path = cache_path_for(key)
    if not os.path.exists(path):
        return None
    try:
        mtime = os.path.getmtime(path)
        if time.time() - mtime > ttl:
            return None
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_cache(key, payload):
    ensure_cache_dir()
    path = cache_path_for(key)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    os.replace(tmp, path)


def build_filter(condition_list, upc):
    clauses = []
    if condition_list:
        ids = sorted({CONDITION_MAP[c] for c in condition_list if c in CONDITION_MAP})
        if ids:
            clauses.append(f"conditionIds:{{{'|'.join(ids)}}}")
    clauses.append("buyingOptions:{FIXED_PRICE|AUCTION}")
    clauses.append("itemLocationCountry:US")
    if upc:
        clauses.append(f"gtin:{{{upc}}}")
    return ",".join(clauses)


def search(token, query, upc, condition_list, limit):
    q = query if query else upc
    params = {
        "q": q,
        "filter": build_filter(condition_list, upc),
        "limit": str(limit),
    }
    url = SEARCH_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            detail = str(e)
        die_json(
            {
                "error": "ebay-search-failed",
                "status": e.code,
                "detail": detail,
            }
        )
    except (urllib.error.URLError, TimeoutError) as e:
        die_json({"error": "ebay-search-failed", "status": 0, "detail": str(e)})


def median(values):
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def extract_comps(raw):
    comps = []
    for item in raw.get("itemSummaries", []) or []:
        price_obj = item.get("price") or {}
        try:
            price = float(price_obj.get("value")) if price_obj.get("value") is not None else None
        except (TypeError, ValueError):
            price = None
        if price is None:
            continue
        comps.append(
            {
                "title": item.get("title") or "",
                "price": price,
                "condition": item.get("condition") or "",
                "url": item.get("itemWebUrl") or "",
            }
        )
    return comps


def build_output(query, upc, condition_list, comps, cached):
    prices = [c["price"] for c in comps]
    med = median(prices)
    pr = [min(prices), max(prices)] if prices else [None, None]
    return {
        "query": query,
        "upc": upc,
        "condition": condition_list,
        "comp_count": len(comps),
        "median_price_usd": round(med, 2) if med is not None else None,
        "price_range": pr,
        "sample_titles": [c["title"] for c in comps[:5]],
        "comps": comps,
        "cached": cached,
        "note": "prices are from active listings, not sold — see script header comment",
    }


def parse_args(argv):
    p = argparse.ArgumentParser(
        description="eBay sold-comps (active-listing approximation) search primitive."
    )
    p.add_argument("--query", default=None)
    p.add_argument("--upc", default=None)
    p.add_argument(
        "--condition",
        default=None,
        help="Comma-separated: NEW,LIKE_NEW,OPEN_BOX,USED",
    )
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--limit", type=int, default=50)
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    if not args.query and not args.upc:
        print("error: --query or --upc (or both) is required", file=sys.stderr)
        sys.exit(2)

    condition_list = []
    if args.condition:
        condition_list = [c.strip().upper() for c in args.condition.split(",") if c.strip()]
        unknown = [c for c in condition_list if c not in CONDITION_MAP]
        if unknown:
            print(
                f"error: unknown condition(s): {','.join(unknown)}. "
                f"valid: {','.join(CONDITION_MAP.keys())}",
                file=sys.stderr,
            )
            sys.exit(2)

    app_id, cert_id = resolve_credentials()

    key = canonical_key(args.query, args.upc, condition_list, args.limit)

    if not args.no_cache:
        cached = load_cache(key, DEFAULT_TTL)
        if cached is not None:
            cached["cached"] = True
            sys.stdout.write(json.dumps(cached, separators=(",", ":")))
            sys.stdout.write("\n")
            return

    token = fetch_token(app_id, cert_id)
    raw = search(token, args.query, args.upc, condition_list, args.limit)
    comps = extract_comps(raw)
    output = build_output(args.query, args.upc, condition_list, comps, cached=False)

    save_cache(key, output)

    sys.stdout.write(json.dumps(output, separators=(",", ":")))
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
