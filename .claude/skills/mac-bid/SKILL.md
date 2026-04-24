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
- `scripts/ebay_search.py --query "<query>" [--upc <upc>] [--condition NEW,LIKE_NEW,OPEN_BOX]` — single eBay Browse sold-search pass. Query/UPC are flags, **not positional**. Returns count, median, range, sample. The cascade across multiple passes is orchestrated **here** in the skill, not inside the script.
- `scripts/max_bid.py --median <price> --tax <rate> --location {home|transfer|remote} [--location-cost <num>] [--discount 0.30 --buyers-premium 0.15 --lot-fee 3.00] [--current-bid <num>]` — runs the formula, returns max_bid. `--location` is an enum that picks the default cost tier; override with `--location-cost <num>` if needed.

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

## Orchestration model — main agent dispatches, sub-agents do the work

The main agent's job is to **parse input, dispatch sub-agents, collect compact JSON, and print a one-line summary to the user.** It does not run scripts, read SSR dumps, analyze images, or write lot reports itself. Every workflow below follows the same shape:

```
parse input  →  dispatch sub-agent(s)  →  collect JSON  →  (optional: dispatch again)  →  print summary
```

### Sub-agent roster

All agents live under `.claude/agents/` and are invoked via the Agent tool. Pass inputs as compact JSON.

| Agent                | Purpose                                                                           | Owns                                                            |
| -------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `mac-bid-lot`        | Full single-lot analysis pipeline end-to-end. Returns compact summary JSON.      | SSR scrape, live fetch, buildings lookup, eBay cascade, image analysis, condition gating, max_bid, deal score, resell, lot report write. |
| `mac-bid-ebay`       | eBay comp cascade only. Returns cascade JSON.                                    | UPC vs ASIN detection, 4-step cascade, confidence grading.      |
| `mac-bid-images`     | Image damage / mismatch analysis only. Returns flag JSON.                        | Download, vision analysis, severity classification.             |
| `mac-bid-discovery`  | Open-lots scan + rank. Returns candidate list JSON.                              | mac.bid search endpoint, lightweight heuristic ranking.         |
| `mac-bid-watchlist`  | Watchlist refresh end-to-end. Returns flag summary JSON; rewrites `watchlist.md`.| Parse/rewrite `watchlist.md`, parallel fetch, flag computation. |
| `mac-bid-report`     | Writes `discovery-*.md` or `compare-*.md` from a list of lot summaries.          | Aggregate report templates and file write.                      |

### Rules

- **Main agent never runs scripts or reads SSR / image data.** Those go through sub-agents.
- **Pass compact inputs only.** The main agent has `aid`+`lid` (from user URL) and whatever JSON the last sub-agent returned. Don't echo full payloads — the sub-agent reads the cache itself.
- **Parallel fan-out goes in a single assistant message.** For workflows that spawn N sub-agents, send all N Agent tool calls in one message so the harness runs them concurrently.
- **`mac-bid-lot` does eBay + images inline inside its own context** (does NOT spawn `mac-bid-ebay`/`mac-bid-images` as sub-sub-agents). Nesting adds overhead and no context saving at that layer. The standalone `mac-bid-ebay` and `mac-bid-images` agents exist for ad-hoc standalone use (e.g. user asks "just run the eBay comps on this product").
- **All lot identity keys on `lid`.** `aid` is a per-modal context param passed to scripts but never used as an identity key for reports, cache files, or watchlist entries.

---

## Workflow 1 — Analyze single lot

**Inputs**: one mac.bid URL containing `aid`+`lid`, or direct `aid`+`lid` input.

**Input parsing** (main agent):
- Any mac.bid URL with `?aid=<aid>&lid=<lid>` is a lot reference regardless of path (`/lot/*`, `/account/watchlist`, `/search`, etc.).
- `aid` may be numeric (`79565`) or alphanumeric (`MVL2604-24-A1`). `lid` is short alphanumeric.
- If user supplies only one of aid/lid, ask for the missing piece — do not guess.
- Legacy `/lot/<id>` path-style URLs are insufficient — ask for both.
- Two URLs with same `lid` but different `aid` → dedup on `lid`.

**Dispatch**:
1. Spawn `mac-bid-lot` with `{aid, lid, write_report: true, resell: <bool>}`.
2. Receive summary JSON.
3. Print one-line summary: recommendation + deal score + one key flag. Link to `reports/lot-{lid}.md`.

That's the entire main-agent flow. The sub-agent handles SSR, live fetch, buildings, eBay cascade, images, gating, max_bid, report write.

## Workflow 2 — Discover deals

**Inputs**: "what's closing tonight", "deals today", optionally constrained to warehouses / categories.

**Dispatch**:
1. Spawn `mac-bid-discovery` with `{intent, building_ids, category_filter, condition_filter, max_candidates, close_within_hours, price_ceiling}`. Returns a candidate list (aid+lid each).
2. **Fan out**: in a single assistant message, spawn one `mac-bid-lot` per candidate with `write_report: false`. Each returns a compact summary.
3. Spawn `mac-bid-report` with `{report_type: "discovery", date, user_intent, lot_summaries: [...]}`. Returns `report_path` + `headline`.
4. Print `headline` to user + offer per-lot full reports on request.

## Workflow 3 — Compare lots

**Inputs**: 2+ URLs/IDs + "compare" / "which is best".

**Dispatch**:
1. Parse all aid+lid pairs.
2. **Fan out**: in a single assistant message, spawn one `mac-bid-lot` per lot with `write_report: true`. Each returns a summary + writes its own `reports/lot-{lid}.md`.
3. Spawn `mac-bid-report` with `{report_type: "compare", date, slug, lot_summaries: [...]}`. Returns `report_path` + `headline` (includes winner).
4. Print winner + report path to user.

## Workflow 4 — Watchlist refresh

The watchlist is a Base view (`watchlist.base`) over reports with `status: watching`. There's no standalone watchlist file to parse — refreshing the watchlist means re-fetching live bids for each matching report and updating its `current_bid` frontmatter.

**Inputs**: "refresh watchlist", "check my watches", "add/remove watch".

**Dispatch**:
1. Spawn `mac-bid-watchlist` with `{action: "refresh" | "remove", ...}`.
   - `refresh`: sub-agent scans `reports/lot-*.md` for `status: watching`, fetches live DDB for each in parallel, updates `current_bid` via `obsidian-cli property set`, flags past-max lots.
   - `remove`: flips a lot's `status` from `watching` to `passed`.
   - **"Add"** is not a real action here — adding to the watchlist = running the analyze workflow (Workflow 1), which produces a report with `status: watching` by default.
2. Print the returned `summary_line` to the user.

No main-agent work beyond dispatch.

## Workflow 5 — Ad-hoc investigation

**Inputs**: open-ended question ("why is retail listed as $2000?", "is this brand reputable?", odd bidding pattern).

Ad-hoc investigations are **conversational by nature** — full delegation to a sub-agent usually breaks the back-and-forth. The main agent handles these directly, reaching for sub-agents only for well-defined chunks:
- Need comps on a product? Spawn `mac-bid-ebay` standalone.
- Need image analysis? Spawn `mac-bid-images` standalone.
- Need a full lot pull? Spawn `mac-bid-lot`.

When a finding is worth preserving, write to `findings/{YYYY-MM-DD}-{slug}.md`. If conversational/disposable, skip the file. Ask if unsure.

## Workflow 6 — Resell evaluation

Stacks on (1) or (3). Inputs: same as host workflow + "evaluate for resell" / "flip" / explicit toggle.

**Steps**:
1. Run host workflow through the comps step.
2. Ask user for intended venue if ambiguous (FB default, eBay fallback). For FB-sensitive categories, prompt the manual FB check.
3. Compute `net_proceeds` and `all_in_cost`. Check 2× threshold.
4. Add a **Resell evaluation** section to the report with: expected sale price, venue, fees, shipping, net proceeds, all-in cost, ratio, verdict (`resell-worthy` / `marginal` / `not worth it`).

---

## Obsidian vault conventions

This project is an Obsidian vault. Reports are vault notes; the watchlist is a Base view over report frontmatter. That changes how reports are written and updated.

### Frontmatter schema (required on every `reports/lot-{lid}.md`)

```yaml
---
lot_id: {lid}
title: {product title, single line, no brand prefix if title already includes it}
warehouse_id: {building_id}
warehouse_name: {short name — "Monroeville", "Washington PA", "Robinson"}
condition: {NEW | LIKE NEW | OPEN BOX | USED | SALVAGE | DAMAGED}
current_bid: {number, no $ sign}
max_bid: {number to 2 decimals, no $ sign; or null if manual-review}
closes_at: {ISO-8601 with timezone, e.g. 2026-04-27T23:52:48Z}
status: watching     # see status values below
recommend: {yes | no | manual}
resell_eligible: {true | false}
---
```

**Derived fields live in `watchlist.base`, not frontmatter.** Do not store `deal_score`, `past_max`, `headroom` — the Base formula computes them from `current_bid` and `max_bid`. Storing them creates staleness when `current_bid` updates.

**Identifiers stay in the cache, not frontmatter.** `aid`, `auction_number`, `lot_number`, `internal_id`, `upc` belong in `cache/lots/{lid}.json`. Keep frontmatter about decision-making fields only.

### Status values (user-owned field)

| Value | Meaning |
|---|---|
| `watching` | Active on the watchlist |
| `passed` | Decided not to bid |
| `bid` | Bid placed, auction still live |
| `won` | Won |
| `lost` | Lost |
| `archived` | Historical |

`status` is **the only field the user owns**. When writing a new report, default to `watching`. Never flip it autonomously based on analysis — the user decides.

### Timestamps

Always ISO-8601 with timezone. Prefer `Z` (UTC) since the upstream mac.bid `end_time` is UTC. Example: `2026-04-27T23:52:48Z`. Do NOT emit `2026-04-27 23:52:48 UTC` — Obsidian Bases cannot sort that format.

### Wikilinks

When a finding is worth preserving, write `findings/{YYYY-MM-DD}-{slug}.md` and reference it from the related report's "Notes" section using `[[findings/{YYYY-MM-DD}-{slug}]]`. When a finding references a lot, use `[[lot-{lid}]]` so backlinks surface on the report.

### Property updates — prefer `obsidian-cli`

When updating a single property (refreshing `current_bid`, flipping `status`), prefer the Obsidian CLI over regex-editing the markdown:

```bash
obsidian-cli property set reports/lot-{lid}.md current_bid 47
```

This preserves frontmatter ordering and types. Raw `Edit` tool is fine for multi-field rewrites or report body changes.

---

## Report template — `reports/lot-{lid}.md`

```markdown
---
lot_id: {lid}
title: {title}
warehouse_id: {building_id}
warehouse_name: {short name}
condition: {CONDITION}
current_bid: {n}
max_bid: {n}
closes_at: {ISO-8601 Z}
status: watching
recommend: {yes | no | manual}
resell_eligible: {true | false}
---

[[watchlist|← Back to watchlist]]

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

## Notes
{Optional: wikilinks to related findings, e.g. [[findings/2026-04-24-retail-pricing-quirks]]}
```

---

## Non-goals

- **Never place bids.** Recommendation and research only. The user always bids themselves.
- **Do not use mac.bid login.** Public DDB + SSR + `/buildings` endpoints cover every research workflow.
- **`agent-browser` is not the default fetch path.** It's available for edge cases (logged-in pages, oddities the JSON endpoints don't expose), but scripts come first.
- No shared code with the sibling `mac-bid-analyzer` project. This workspace is self-contained.
