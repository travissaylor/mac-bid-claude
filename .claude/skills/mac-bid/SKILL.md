---
name: mac-bid
description: Research and analyze auction lots on mac.bid. Trigger on mac.bid URLs, lot IDs, "analyze this lot", "what's closing tonight", "which of these is the best buy", "is this a good deal", watchlist refresh/add/remove, resell evaluation for mac.bid items, or any ad-hoc question about a mac.bid listing, warehouse, or deal score. Never places bids — research only.
---

# mac-bid skill

Interactive workspace for researching mac.bid auction lots. Six workflows, one skill. Detect which workflow applies from the user's input, then orchestrate.

## Lot identity convention

`lid` is the **canonical, stable** identifier for a lot. `aid` is a per-modal URL routing parameter — the same underlying lot can surface under different `aid` values depending on which page opened the modal, but its `lid` stays the same across those contexts.

Extract both `aid` and `lid` from any mac.bid URL's query params (`?aid=<aid>&lid=<lid>`), regardless of path. Pass both to scripts when calling APIs (`--aid`/`--lid` — the endpoints need both). But for **identity** — dedup, cache keys, report filenames, watchlist entries — key on `lid` alone.

## Workflow routing

| Signal in user input                                       | Workflow                          |
| ---------------------------------------------------------- | --------------------------------- |
| Single mac.bid URL with `aid`+`lid` query params, or direct `aid`/`lid` input | (1) Single-lot analysis           |
| "closing tonight", "deals today", "open lots at my wh"     | (2) Discovery                     |
| Two or more URLs/IDs + "compare" / "which"                 | (3) Compare                       |
| "watchlist", "refresh my watchlist", "add/remove ... watch"| (4) Watchlist                     |
| Open question ("why is this odd", "is retail realistic")   | (5) Ad-hoc investigation          |
| "resell", "flip", "net proceeds", or user says evaluate it | (6) Resell evaluation (layers on) |

Resell evaluation is a toggle. It can stack on (1) or (3). Default off unless asked.

## Scripts (all live in `/scripts/`, invoke via Bash)

- `scripts/fetch_lot.py --aid <aid> --lid <lid>` — live DDB payload (current bid, bids count, closing time, lot_id, building_id).
- `scripts/scrape_lot_ssr.py --aid <aid> --lid <lid>` — SSR `__NEXT_DATA__` parse (title, brand, model, UPC, condition, category, retail, image URLs, description).
- `scripts/fetch_buildings.py` — refreshes `cache/buildings.json` (building_id → name, city, state, sales_tax_rate).
- `scripts/ebay_search.py <query> [--condition NEW,LIKE_NEW,OPEN_BOX]` — single eBay Browse sold-search pass. Returns count, median, range, sample. The cascade across multiple passes is orchestrated **here** in the skill, not inside the script.
- `scripts/max_bid.py --median X --tax Y --location Z [--discount 0.30 --buyers-premium 0.15 --lot-fee 3.00]` — runs the formula, returns max_bid.

Scripts emit compact JSON. Prefer them over inline fetching so caching/retries stay centralized.

## Formulas (inline — do not re-derive)

**Max bid**
```
target_all_in = ebay_sold_median * (1 - discount_threshold)   # default discount = 0.30
max_bid       = (target_all_in - lot_fee - location_cost) / (1 + buyers_premium + sales_tax)
```
Defaults: `buyers_premium = 0.15`, `lot_fee = 3.00`, `discount = 0.30`. `sales_tax` is per-building (look up in `cache/buildings.json`; PA ≈ 0.06).

Before calling `scripts/max_bid.py`, check the SSR output: if `lot_fee_override` is non-null, pass it as `--lot-fee <value>`; if `buyers_premium_override` is non-null, pass it as `--buyers-premium <value>`. Otherwise the script's defaults apply.

**Deal score**
```
deal_score = (max_bid - current_bid) / max_bid * 100
```
90 = great early-auction opportunity. 10 = near max. ≤0 = skip.

**Resell (only if toggled on)**
```
all_in_cost      = current_bid * (1 + buyers_premium + sales_tax) + lot_fee + location_cost
net_proceeds     = expected_sale_price * (1 - venue_fees) - shipping_cost
resell_worthy    = net_proceeds >= 2 * all_in_cost
```
Below 2×: still report, label "marginal for resell." FB Marketplace is primary venue (fees ~0, shipping often $0 for local), eBay is fallback (~13% fees + shipping).

## Location cost

Home warehouse (building IDs **15, 16, 6, 1** — PA): `$0`. Transfer: `+$10`. Remote: `+$25`. Default assumption: user wants home warehouses unless they say otherwise.

## Condition gating

- `NEW` / `LIKE NEW` / `OPEN BOX` **and** ≥5 eBay comps → auto-recommend max bid.
- `USED` / `SALVAGE` / `DAMAGED` → do **not** auto-recommend. Flag for manual review. If the user gives override context ("ok for used"), proceed but label the recommendation as override-based.
- Unknown/missing condition → treat as manual review.

## eBay cascade (skill orchestrates, not the script)

Run each step via `scripts/ebay_search.py`. Stop as soon as total comps ≥ 5.

1. **UPC pass** — validate the UPC value before passing to `scripts/ebay_search.py --upc`. A real UPC matches `^[0-9]{12,14}$`. If the value contains letters, starts with `B0`, or isn't 12–14 digits, it's an ASIN (mac.bid sometimes stores an Amazon ASIN like `B0CY4Y22HS` in its `upc` field) — **skip the UPC pass** and go straight to Step 2 (natural-language query). The ASIN can still inform query generation (look it up on Amazon for a canonical product title) but must not be passed as `--upc`.
2. **LLM query pass** — generate a natural-language query from `{brand} {model} {short title}`. Keep it specific.
3. **Broadened pass** — drop the model number, use brand + product category.
4. **Condition-relaxed pass** — repeat (3) without any `--condition` filter.

Below 5 total comps after all four: still compute, but label the recommendation **"advisory, low comp count"** and include the search trail in the report.

## Image analysis

Pull image URLs from SSR payload. Analyze inline using vision. Flag:
- Damage (scratches, dents, cracks, water stains)
- Missing parts (mismatched count vs. listing, missing accessories visible in stock photos)
- Product mismatch (photo shows a different model/brand than the title)
- Third-party refurb stickers (ASTSYS, etc.) visible on the chassis are a **low-severity** signal. Does not command the premium that manufacturer-official (HP/Dell/Apple) refurb would. Note in the report but don't reduce max-bid for this alone.

Each flag carries severity: **low** / **medium** / **high**. High-severity flags should reduce the max-bid recommendation or trigger manual review.

## FB-sensitive categories

For these, eBay comps underprice reality. Prompt user to eyeball FB Marketplace manually and paste back what they see:

- Large furniture
- Mattresses
- Major appliances (washers, dryers, refrigerators, ovens)
- Grills and outdoor equipment
- Exercise equipment
- Anything heavy or awkward to ship

Include an explicit "FB Marketplace check" section in the report for these.

---

## Workflow 1 — Analyze single lot

**Inputs**: one mac.bid URL containing `aid` and `lid` query params, or direct `aid`+`lid` input (e.g. "analyze 79565/3078E", "analyze aid=79565 lid=3078E").

**Input parsing**:
- Any mac.bid URL with both `?aid=<aid>&lid=<lid>` query params is a lot reference, regardless of path (`/lot/*`, `/account/watchlist`, `/search`, `/past-auctions/*`, etc.). The lot modal state is encoded in those params.
- `aid` may be numeric (`79565`) or alphanumeric (`MVL2604-24-A1`). `lid` is short alphanumeric (`1795Q`, `3078E`).
- If the user supplies only one of `aid` or `lid`, ask for the missing piece — do not guess. Scripts require both to hit the APIs.
- Legacy `/lot/<id>` path-style URLs (if still encountered) should be surfaced to the user as insufficient — ask them for both `aid` and `lid`.
- If the user pastes two URLs with the same `lid` but different `aid` values, treat them as the **same lot** (dedup on `lid`). `aid` differs only because the lot was surfaced from different modal contexts.

**Steps**:
1. Extract `aid` and `lid` from the URL query string (or accept them directly from user input).
2. `scripts/scrape_lot_ssr.py --aid <aid> --lid <lid>` → product metadata + image URLs. **Writes SSR cache to `cache/lots/{lid}.ssr.json` including `internal_id`.**
3. `scripts/fetch_lot.py --aid <aid> --lid <lid>` → live bid data + `building_id`. **Reads the SSR cache to resolve `internal_id` before hitting the DDB endpoint.**

> Steps 2 and 3 must run **sequentially, not in parallel** — `fetch_lot` depends on the SSR-derived `internal_id`.
4. If `cache/buildings.json` missing or stale (>7 days), run `scripts/fetch_buildings.py`. Look up sales_tax and location classification.
5. Run eBay cascade (above).
6. Analyze 2–6 images via vision. Record flags.
7. Apply condition gating. If blocked, write the report with "manual review" instead of a recommendation.
8. `scripts/max_bid.py` with the cascade median, tax rate, location cost.
9. Compute deal score.
10. If resell toggled: compute resell evaluation.
11. Write `reports/lot-{lid}.md` using the template below.

**Output**: `reports/lot-{lid}.md`. Also print a one-line summary to the user (deal score + recommendation + one key flag if any).

## Workflow 2 — Discover deals

**Inputs**: "what's closing [tonight|today|soon]", optionally constrained to warehouses or categories.

**Steps**:
1. Query open-lots endpoint (via `scripts/fetch_lot.py` or a discovery variant) filtered to building IDs 15, 16, 6, 1 by default.
2. For each candidate, do a **lightweight pass**: current bid, condition, title. Skip if condition fails gating and user didn't opt in.
3. Rank by a quick heuristic (e.g., `current_bid / retail_price` low, closing soon, condition good).
4. Take top ~10. For each, run the full single-lot pipeline **without writing individual reports**.
5. Write `reports/discovery-{YYYY-MM-DD}.md` — ranked shortlist with deal score, warnings, link to each lot. Offer to generate full per-lot reports on request.

## Workflow 3 — Compare lots

**Inputs**: 2+ URLs/IDs + "compare" / "which is best".

**Steps**:
1. Run steps 1–10 of Workflow 1 for each lot (in parallel where possible).
2. Write `reports/compare-{YYYY-MM-DD}-{slug}.md` — table of lots across columns, narrative verdict at the end picking the winner and stating why. Include deal scores, max bids, flags.

## Workflow 4 — Watchlist refresh

**Inputs**: "refresh watchlist", "check my watches", or implicit when user mentions `watchlist.md`.

Source of truth: `/watchlist.md` at project root. Human-editable markdown. Each entry has at minimum: lot URL/ID, user's max bid, optional notes.

**Steps**:
1. Parse `watchlist.md`. Each entry's lot reference provides both `aid` and `lid` (parsed from the URL's query params or explicitly listed).
2. For each entry: `scripts/fetch_lot.py --aid <aid> --lid <lid>` — current bid, time remaining.
3. For each entry, flag:
   - `past-max`: current_bid ≥ user_max
   - `approaching`: current_bid ≥ 0.8 × user_max
   - `closing-soon`: <2 hours remaining
   - `ended`: auction closed
4. Rewrite `watchlist.md` in place, preserving user's structure, adding a "last checked" line per entry and a flag emoji/tag. Don't delete entries — user edits that file.
5. On add/remove requests: edit `watchlist.md` accordingly.
6. Summarize flags to the user in chat.

## Workflow 5 — Ad-hoc investigation

**Inputs**: open-ended question ("why is retail listed as $2000?", "is this brand reputable?", "what's up with this odd bidding pattern").

**Steps**:
1. Use whatever scripts + web search + image analysis fit the question.
2. Converse. Ask clarifying questions if needed.
3. When the finding is worth preserving, write to `findings/{YYYY-MM-DD}-{slug}.md`. If the investigation stays conversational/disposable, don't create a file. Ask if unsure.

## Workflow 6 — Resell evaluation

Stacks on (1) or (3). Inputs: same as host workflow + "evaluate for resell" / "flip" / explicit toggle.

**Steps**:
1. Run host workflow through the comps step.
2. Ask user for intended venue if ambiguous (FB default, eBay fallback). For FB-sensitive categories, prompt the manual FB check.
3. Compute `net_proceeds` and `all_in_cost`. Check 2× threshold.
4. Add a **Resell evaluation** section to the report with: expected sale price, venue, fees, shipping, net proceeds, all-in cost, ratio, verdict (`resell-worthy` / `marginal` / `not worth it`).

---

## Report template — `reports/lot-{lid}.md`

```markdown
# Lot {lid} — {title}
<!-- Observed via aid={aid}. Filename/identity key on lid alone; aid is per-modal context. -->

## Summary
- **Recommendation**: {max_bid | manual review | advisory}
- **Deal score**: {n}
- **Key flags**: {bullet list or "none"}

## Listing snapshot
- Current bid: ${current_bid} ({n_bids} bids)
- Closes: {timestamp} ({time_remaining})
- Warehouse: {name} (id {building_id}) — {home|transfer|remote}, tax {rate}
- Condition: {condition}
- Retail: ${retail}

## Product analysis
{Summary from SSR + photo analysis. Each image flag with severity.}

## eBay comps
- Count: {n}
- Median: ${median}
- Range: ${low} – ${high}
- Search trail:
  1. UPC `{upc}` → {n} results
  2. Query `{q}` → {n} results
  3. ...

## Max bid calculation
- ebay_sold_median: ${median}
- discount_threshold: 30%
- target_all_in: ${target}
- lot_fee: $3.00
- location_cost: ${loc}
- buyers_premium: 15%
- sales_tax: {rate}
- **max_bid: ${max}**

## Deal score
(max_bid - current_bid) / max_bid * 100 = **{score}**

## Resell evaluation
{only if toggled}

## Warnings / flags
{bullet list}
```

---

## Non-goals

- **Never place bids.** Recommendation and research only. The user always bids themselves.
- **Do not use mac.bid login.** Public DDB + SSR + `/buildings` endpoints cover every research workflow.
- **`agent-browser` is not the default fetch path.** It's available for edge cases (logged-in pages, oddities the JSON endpoints don't expose), but scripts come first.
- No shared code with the sibling `mac-bid-analyzer` project. This workspace is self-contained.
