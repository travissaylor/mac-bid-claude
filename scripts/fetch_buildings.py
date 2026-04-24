#!/usr/bin/env python3
"""Fetch and cache the mac.bid warehouse/buildings list (with tax rates).
Emits the cached JSON unchanged on stdout."""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

CACHE_PATH = "/Users/tsaylor/projects/mac-bid-claude/cache/buildings.json"
# TODO: verify this endpoint on first use. macdiscount.com's public routes have moved before;
# if this 404s, try `/buildings/list` or inspect the mac.bid SSR payload for the real path.
ENDPOINT = "https://api.macdiscount.com/buildings"
USER_AGENT = "mac-bid-claude/0.1 (research)"
TIMEOUT = 15
DEFAULT_TTL = 86400


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


def fetch():
    req = urllib.request.Request(ENDPOINT, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--max-age-seconds", type=int, default=DEFAULT_TTL)
    args = p.parse_args()

    if not args.no_cache:
        cached = load_cached(CACHE_PATH, args.max_age_seconds)
        if cached is not None:
            sys.stdout.write(json.dumps(cached, separators=(",", ":")))
            return 0

    try:
        data = fetch()
    except urllib.error.HTTPError as e:
        sys.stdout.write(json.dumps({"error": str(e), "status": e.code, "endpoint": ENDPOINT}, separators=(",", ":")))
        return 1
    except urllib.error.URLError as e:
        sys.stdout.write(json.dumps({"error": str(e.reason), "status": 0, "endpoint": ENDPOINT}, separators=(",", ":")))
        return 1
    except (TimeoutError, json.JSONDecodeError) as e:
        sys.stdout.write(json.dumps({"error": str(e), "status": 0, "endpoint": ENDPOINT}, separators=(",", ":")))
        return 1

    try:
        write_cache(CACHE_PATH, data)
    except OSError as e:
        sys.stderr.write(f"cache write failed: {e}\n")

    sys.stdout.write(json.dumps(data, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
