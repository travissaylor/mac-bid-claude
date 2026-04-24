#!/usr/bin/env python3
"""Fetch live bid data from mac.bid's DynamoDB REST endpoint, with local caching.

There is ONE correct endpoint:

    https://api.macdiscount.com/map-bid/ddb/lot/{internal_id}

where `internal_id` is the lot's numeric primary key (NOT `aid`, NOT `lid`).
The `internal_id` is obtained from the SSR cache produced by
`scripts/scrape_lot_ssr.py` at `cache/lots/{lid}.ssr.json` under
`raw.props.pageProps.activeLot.id`. When called with `--aid/--lid`, this
script is SSR-dependent: it resolves `internal_id` from that cache. Callers
that already have `internal_id` can pass it directly via `--internal-id`.

The projected field `current_max_bid` is the current WINNING BIDDER's
proxy-bid ceiling (upstream key: `max_bid`). It is renamed here to
`current_max_bid` to avoid confusion with this project's own `max_bid`
concept (our recommended bid ceiling), which is unrelated.

Emits a compact JSON projection on stdout; full upstream payload under `raw`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

CACHE_DIR = "/Users/tsaylor/projects/mac-bid-claude/cache/lots"
ENDPOINT = "https://api.macdiscount.com/map-bid/ddb/lot/{internal_id}"
USER_AGENT = "mac-bid-claude/0.1 (research)"
TIMEOUT = 15
DEFAULT_TTL = 300


def _safe(s: str) -> str:
    # Defensive: lid is substituted into a filename; scrub path separators.
    return s.replace("/", "_").replace("..", "_")


def cache_path(lid, internal_id: int) -> str:
    # Identity keys on lid when available (human-facing), else internal_id.
    if lid:
        return os.path.join(CACHE_DIR, f"{_safe(lid)}.json")
    return os.path.join(CACHE_DIR, f"internal-{internal_id}.json")


def ssr_cache_path(lid: str) -> str:
    return os.path.join(CACHE_DIR, f"{_safe(lid)}.ssr.json")


def load_cached(path: str, max_age: int):
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > max_age:
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


def resolve_internal_id_from_ssr(lid: str):
    """Returns (internal_id, error_payload). Exactly one is non-None."""
    path = ssr_cache_path(lid)
    if not os.path.exists(path):
        return None, {
            "error": "ssr-cache-missing",
            "lid": lid,
            "hint": "run scripts/scrape_lot_ssr.py --aid <aid> --lid <lid> first",
        }
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return None, {
            "error": "ssr-cache-unreadable",
            "lid": lid,
            "detail": str(e),
            "hint": "re-run scrape_lot_ssr.py with --no-cache",
        }
    try:
        internal_id = doc["raw"]["props"]["pageProps"]["activeLot"]["id"]
    except (KeyError, TypeError):
        return None, {
            "error": "internal-id-not-in-ssr",
            "lid": lid,
            "hint": "SSR cache exists but activeLot.id is missing — re-run scrape_lot_ssr.py with --no-cache",
        }
    try:
        return int(internal_id), None
    except (TypeError, ValueError):
        return None, {
            "error": "internal-id-not-integer",
            "lid": lid,
            "value": internal_id,
        }


def fetch(internal_id: int):
    url = ENDPOINT.format(internal_id=internal_id)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read()
    return json.loads(data), url


def _coerce_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _coerce_int(v):
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _coerce_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"1", "true", "t", "yes", "y"}:
            return True
        if s in {"0", "false", "f", "no", "n"}:
            return False
    return None


def project(aid, lid, internal_id: int, raw) -> dict:
    # `current_max_bid` is upstream's `max_bid` (the current winning bidder's
    # proxy-bid ceiling) — renamed to avoid collision with our project's own
    # `max_bid` concept.
    d = raw if isinstance(raw, dict) else {}
    out = {
        "aid": str(aid) if aid is not None else None,
        "lid": str(lid) if lid is not None else None,
        "internal_id": int(internal_id),
        "current_bid": _coerce_float(d.get("current_bid")),
        "current_max_bid": _coerce_float(d.get("max_bid")),
        "total_bids": _coerce_int(d.get("total_bids")),
        "winning_bidder_id": _coerce_int(d.get("winning_bidder_id")),
        "watchers_count": _coerce_int(d.get("watchers_count")),
        "end_time": d.get("end_time"),
        "extension_window": d.get("extension_window"),
        "is_open": _coerce_bool(d.get("is_open")),
        "lot_number": d.get("lot_number"),
        "auction_id": _coerce_int(d.get("auction_id")),
        "location_id": _coerce_int(d.get("location_id")),
        "raw": raw,
    }
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--internal-id", type=int, default=None,
                   help="Lot's numeric internal primary key (preferred).")
    p.add_argument("--aid", default=None,
                   help="Auction ID (convenience; requires --lid; triggers SSR lookup for internal_id).")
    p.add_argument("--lid", default=None,
                   help="Lot ID within the auction (convenience; requires --aid).")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--max-age-seconds", type=int, default=DEFAULT_TTL)
    args = p.parse_args()

    internal_id = args.internal_id
    aid = args.aid
    lid = args.lid

    # Validate input shape: need either --internal-id or (--aid AND --lid).
    if internal_id is None and not (aid and lid):
        sys.stderr.write(
            "error: provide either --internal-id N, or both --aid A and --lid L\n"
        )
        sys.stdout.write(json.dumps({
            "error": "missing-input",
            "hint": "provide --internal-id N, or both --aid A and --lid L",
        }, separators=(",", ":")))
        return 1

    # Resolve internal_id from SSR cache if necessary.
    if internal_id is None:
        resolved, err = resolve_internal_id_from_ssr(lid)
        if err is not None:
            sys.stderr.write(f"error: {err['error']}: {err.get('hint','')}\n")
            sys.stdout.write(json.dumps(err, separators=(",", ":")))
            return 1
        internal_id = resolved

    path = cache_path(lid, internal_id)

    if not args.no_cache:
        cached = load_cached(path, args.max_age_seconds)
        if cached is not None:
            sys.stdout.write(json.dumps(cached, separators=(",", ":")))
            return 0

    try:
        raw, url_used = fetch(internal_id)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = ""
        sys.stdout.write(json.dumps({
            "error": "ddb-fetch-failed",
            "status": e.code,
            "detail": detail or str(e),
            "internal_id": internal_id,
            "aid": aid,
            "lid": lid,
        }, separators=(",", ":")))
        return 1
    except urllib.error.URLError as e:
        sys.stdout.write(json.dumps({
            "error": "ddb-fetch-failed",
            "status": 0,
            "detail": str(e.reason),
            "internal_id": internal_id,
            "aid": aid,
            "lid": lid,
        }, separators=(",", ":")))
        return 1
    except (TimeoutError, json.JSONDecodeError) as e:
        sys.stdout.write(json.dumps({
            "error": "ddb-fetch-failed",
            "status": 0,
            "detail": str(e),
            "internal_id": internal_id,
            "aid": aid,
            "lid": lid,
        }, separators=(",", ":")))
        return 1

    obj = project(aid, lid, internal_id, raw)
    obj["_ddb_endpoint_used"] = url_used

    # Sanity check lot_number/auction_id against inputs (non-fatal).
    if lid is not None and obj.get("lot_number") is not None:
        if str(obj["lot_number"]).strip() != str(lid).strip():
            sys.stderr.write(
                f"warning: lot_number mismatch: upstream={obj['lot_number']!r} input_lid={lid!r}\n"
            )
    if aid is not None and obj.get("auction_id") is not None:
        try:
            if int(obj["auction_id"]) != int(aid):
                sys.stderr.write(
                    f"warning: auction_id mismatch: upstream={obj['auction_id']} input_aid={aid}\n"
                )
        except (TypeError, ValueError):
            pass

    try:
        write_cache(path, obj)
    except OSError as e:
        sys.stderr.write(f"cache write failed: {e}\n")

    sys.stdout.write(json.dumps(obj, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
