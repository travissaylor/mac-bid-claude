---
name: ebay-comps
description: Resolve a product's fair market value from eBay sold listings. Use when a user asks "what's this worth on eBay", "find sold comps for X", "median sold price", does a UPC lookup, or otherwise needs sold-price research for a physical good. Returns a median sold price with a confidence rating and the search trail used to get there. Decoupled from any specific caller — works equally well for mac.bid auction-lot research or ad-hoc price lookups.
---

# ebay-comps skill

Resolve a product to a median eBay sold price via a cascading search. Stop as soon as enough comps accumulate.

## Inputs

Caller must supply ONE of:
- `upc` — 12-digit UPC (preferred; most precise match)
- `title + brand + model` — structured identifier
- `description` — freeform text

More specific inputs beat less specific ones. If caller has a UPC, use it.

Note: the value stored in upstream 'upc' fields is sometimes an Amazon ASIN, not a real GTIN. The cascade handles this — callers can pass whatever they have without pre-validating.

## Cascade

Execute steps in order. **Stop the moment cumulative comps ≥ 5**, or when all steps are exhausted.

1. **UPC search** (skip if no UPC)

   Before running, **validate the UPC input**. eBay's `gtin:` filter expects a real UPC/EAN/ISBN (12-14 numeric digits) and rejects Amazon ASINs (e.g. `B0CY4Y22HS`), which upstream sources like mac.bid sometimes store in their "upc" field.
   - Real UPC/EAN/ISBN: matches `^[0-9]{12,14}$`.
   - ASIN pattern: starts with `B0` and contains letters, or any value with alphabetic characters, or shorter than 12 digits → not a GTIN.
   - If validation fails, **skip Step 1 entirely** and start with Step 2. Record it in the search trail as `{step: "upc", query: "<value>", note: "skipped — not a valid GTIN (likely ASIN)"}`.

   `scripts/ebay_search.py --upc <upc> --condition NEW,LIKE_NEW,OPEN_BOX`

2. **Natural query** — build from `brand + model + key descriptor`; keep it short (3-6 tokens).
   `scripts/ebay_search.py --query "<query>" --condition NEW,LIKE_NEW,OPEN_BOX`

3. **Broadened query** — drop the model number, or swap a specific spec for its category (e.g. "DeWalt DCD777C2" → "DeWalt 20V drill kit"). Same condition filter.

4. **Relaxed condition** — rerun the broadened query with no `--condition` flag (includes used, excludes "for parts or not working" only when a condition filter is set, so relaxed mode does include parts listings — note this in the output).

## Default filters (applied by the script)

- Sold / completed / won listings only
- US marketplace (`EBAY_US`)
- When `--condition` is present: exclude "for parts or not working"

## Authentication

OAuth2 client credentials resolve in this order:
1. `EBAY_APP_ID` / `EBAY_CERT_ID` environment variables (CI / ad-hoc overrides)
2. Local cache file `~/.config/mac-bid-claude/ebay.env` (mode 0600), populated by `scripts/refresh_ebay_credentials.py`
3. 1Password CLI direct reads from `op://Personal/za7ym3agvpwbszokahxsfr5sq4/{username,credential}`

The cache file exists to skip the per-call Touch ID prompt that `op read` triggers — without it, every script invocation prompts for biometric approval. Run `python3 scripts/refresh_ebay_credentials.py` once after install (and again whenever eBay rotates the keys) to populate it. If neither the cache nor `op` resolves the values, the script prints a clear diagnostic and exits 1.

```
python scripts/ebay_search.py --query "..."        # auto-resolves from cache, then op
```

## Caching

Results cache to `cache/ebay/{hash}.json`, keyed by query+filter hash. Cache entries < 24h old are reused. Pass `--no-cache` to force a fresh API hit (e.g. when the caller suspects stale data or is validating the script itself).

## Output

Return a single JSON-shaped object:

```
{
  "median_sold_price_usd": <float>,
  "comp_count": <int>,
  "price_range": {"min": <float>, "max": <float>},
  "confidence": "high" | "advisory",
  "search_trail": [
    {"step": 1, "query": "upc:012345678905", "comp_count": 0},
    {"step": 2, "query": "DeWalt DCD777C2", "comp_count": 7}
  ],
  "sample_titles": ["...", "...", "..."]
}
```

Rules:
- `confidence = "high"` iff `comp_count >= 5`, else `"advisory"`.
- `sample_titles`: 3-5 titles drawn from the **winning step** (the step whose results pushed the total to ≥ 5, or the last non-empty step).
- `search_trail` lists every step actually executed, in order, with the per-step comp count.

## Insufficient data

If total comps across all cascade steps < 3, **do not compute a median**. Return:

```
{
  "confidence": "advisory",
  "comp_count": <n>,
  "note": "insufficient comp data — fewer than 3 sold listings found",
  "search_trail": [...]
}
```

A two-comp median is misleading; flag it instead.

## Notes for the caller

- The skill does not decide *what* to do with the price. It returns a number plus context. Buy/bid logic stays with the caller.
- When `confidence == "advisory"`, surface that to the end user — do not report the median as if it were authoritative.
- The skill never writes to any auction system. It only reads from eBay.
