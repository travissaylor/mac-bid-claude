---
name: mac-bid
description: Research and analyze auction lots on mac.bid. Trigger on mac.bid URLs, lot IDs, "analyze this lot", "what's closing tonight", "which of these is the best buy", "is this a good deal", watchlist refresh/add/remove, resell evaluation for mac.bid items, or any ad-hoc question about a mac.bid listing, warehouse, or deal score. Never places bids — research only.
---

# mac-bid skill

Interactive workspace for researching mac.bid auction lots. Six workflows, one skill. Detect which workflow applies from the user's input, then orchestrate.

## Lot identity convention

`lid` is the **canonical, stable** identifier for a lot. `aid` is a per-modal URL routing parameter — the same underlying lot can surface under different `aid` values depending on which page opened the modal, but its `lid` stays the same across those contexts.

Extract both `aid` and `lid` from any mac.bid URL's query params (`?aid=<aid>&lid=<lid>`), regardless of path. Pass both to scripts when calling APIs (`--aid`/`--lid` — the endpoints need both). But for **identity** — dedup, cache keys, report filenames, watchlist entries — key on `lid` alone.

`aid` may be numeric (`79565`) or alphanumeric (`MVL2604-24-A1`). `lid` is short alphanumeric. If the user supplies only one of aid/lid, ask for the missing piece — do not guess. Legacy `/lot/<id>` path-style URLs are insufficient — ask for both. Two URLs with same `lid` but different `aid` → dedup on `lid`.

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

High-level only — run with `--help` for full flag detail.

- `scripts/fetch_lot.py` — live DDB payload (current bid, bids, closing time).
- `scripts/scrape_lot_ssr.py` — SSR `__NEXT_DATA__` parse (title, brand, model, UPC, condition, category, retail, image URLs, description).
- `scripts/fetch_buildings.py` — refreshes `cache/buildings.json` (building_id → name, city, state, sales_tax_rate).
- `scripts/ebay_search.py` — single eBay Browse sold-search pass. The cascade across multiple passes is orchestrated by `mac-bid-ebay` / `mac-bid-lot`, not by this script.
- `scripts/max_bid.py` — runs the formula, returns max_bid (and the floor lane when `--floor-median` is passed).

Scripts emit compact JSON. Prefer them over inline fetching so caching/retries stay centralized.

## Formulas (inline — do not re-derive)

**Max bid**
```
target_all_in = ebay_sold_median * (1 - discount_threshold)   # default discount = 0.30
max_bid       = (target_all_in - lot_fee - location_cost) / (1 + buyers_premium + sales_tax)
```
Defaults: `buyers_premium = 0.15`, `lot_fee = 3.00`, `discount = 0.30`. `sales_tax` is per-building (look up in `cache/buildings.json`; PA ≈ 0.06).

If SSR has non-null `lot_fee_override` or `buyers_premium_override`, the lot agent passes them through.

**Floor max bid (worst-case lane)**
```
floor_median        = p25 of the same eBay comp set (or for-parts / cheaper-variant median where applicable)
target_all_in_floor = floor_median * (1 - discount)
max_bid_floor       = (target_all_in_floor - lot_fee - location_cost) / (1 + buyers_premium + sales_tax)
```
Same formula as `max_bid`, pessimistic median input. Default floor source is `p25` from the same comp set used for the median — no extra API call.

**Deal score**
```
deal_score = (max_bid - current_bid) / max_bid * 100
```
90 = great early-auction opportunity. 10 = near max. ≤0 = skip.

## Location cost

Home warehouse (building IDs **15, 16, 6, 1** — PA): `$0`. Transfer: `+$10`. Remote: `+$25`. Default assumption: user wants home warehouses unless they say otherwise.

## Condition gating

NEW / LIKE NEW / OPEN BOX with ≥5 comps → auto-recommend. USED / SALVAGE / DAMAGED / unknown → manual review. (Detail and image-flag override live in `mac-bid-lot.md`.)

## Pointers to sub-agent detail

Detail the orchestrator does **not** need inline:

- **eBay cascade rules** (UPC vs ASIN, 4-step ladder, outlier exclusion, confidence grading) — see `mac-bid-ebay.md` (and replicated in `mac-bid-lot.md`).
- **Image flag taxonomy and severity** — see `mac-bid-images.md` (and replicated in `mac-bid-lot.md`).
- **Report frontmatter schema, status values, timestamp rules, full report template, FB-sensitive categories list** — see `mac-bid-lot.md`.
- **Resell formula** — see `mac-bid-lot.md` step 9.
- **Wikilinks**: findings live at `findings/{YYYY-MM-DD}-{slug}.md`; reports cross-link via `[[findings/...]]` and `[[lot-{lid}]]`.

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

**Dispatch**:
1. Spawn `mac-bid-lot` with `{aid, lid, write_report: true, resell: <bool>}`.
2. Print one-line summary from returned JSON: recommendation + deal score + one key flag. Link to `reports/lot-{lid}.md`.

That's the entire main-agent flow. The sub-agent handles SSR, live fetch, buildings, eBay cascade, images, gating, max_bid, report write.

## Workflow 2 — Discover deals

**Inputs**: "what's closing tonight", "deals today", optionally constrained to warehouses / categories.

**Dispatch**:
1. Spawn `mac-bid-discovery` with `{intent, building_ids, category_filter, condition_filter, max_candidates, close_within_hours, price_ceiling}`. Returns a candidate list.
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

## Workflow 5 — Ad-hoc investigation

**Inputs**: open-ended question ("why is retail listed as $2000?", "is this brand reputable?", odd bidding pattern).

Ad-hoc investigations are **conversational by nature** — full delegation to a sub-agent usually breaks the back-and-forth. The main agent handles these directly, reaching for sub-agents only for well-defined chunks:
- Need comps on a product? Spawn `mac-bid-ebay` standalone.
- Need image analysis? Spawn `mac-bid-images` standalone.
- Need a full lot pull? Spawn `mac-bid-lot`.

When a finding is worth preserving, write to `findings/{YYYY-MM-DD}-{slug}.md`. If conversational/disposable, skip the file. Ask if unsure.

## Workflow 6 — Resell evaluation

Stacks on (1) or (3). Inputs: same as host workflow + "evaluate for resell" / "flip" / explicit toggle.

**Dispatch**: pass `resell: true` (and `intended_venue` if specified) to `mac-bid-lot`. The sub-agent runs the resell math, prompts the FB-sensitive eyeball check if applicable, and adds a **Resell evaluation** section to the report. Print verdict (`resell-worthy` / `marginal` / `not worth it`) in the summary line.

---

## Non-goals

- **Never place bids.** Recommendation and research only. The user always bids themselves.
- **Do not use mac.bid login.** Public DDB + SSR + `/buildings` endpoints cover every research workflow.
- **`agent-browser` is not the default fetch path.** It's available for edge cases (logged-in pages, oddities the JSON endpoints don't expose), but scripts come first.
- No shared code with the sibling `mac-bid-analyzer` project. This workspace is self-contained.
