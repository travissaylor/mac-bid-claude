# mac-bid-claude

A Claude Code workspace for researching lots on [mac.bid](https://www.mac.bid). You hand Claude a URL (or a question), it pulls listing data, finds secondary-market comps, computes a recommended max bid, analyzes the product photos, and writes a markdown report you can come back to. It then stays available for follow-up questions and ad-hoc investigation.

The project is an **interactive Claude workspace**, not an application. The point is flexibility: you bring a question, Claude does the research, you ask follow-ups, findings land in files.

## Why this project exists

A sibling project at `/Users/tsaylor/projects/mac-bid-analyzer` does similar analysis as a standalone app — CLI, HTTP server, browser extension, Telegram bot. It's great for quick decisions on the go (phone, in the warehouse aisle). It's deliberately rigid: fixed formula, fixed workflow.

This project is for **deeper research sessions**: narrative reports, cross-listing comparisons, conversational follow-ups, resell evaluation, "why is this odd?" investigations. When you want more than a thumbs-up / thumbs-down on a lot, you come here.

**The two projects share no code.** This one is self-contained. API endpoints and formulas are re-implemented here, informed by but not coupled to the analyzer.

## Core workflows

1. **Analyze a single lot** — "Analyze `https://www.mac.bid/lot/...`" → Claude fetches lot data, runs an eBay cascade search, checks product photos for damage/missing parts, computes a max bid, and writes `reports/lot-{id}.md`.
2. **Discover deals** — "What's closing tonight at my warehouses with a good deal score?" → Claude queries open lots, ranks them, drafts a shortlist.
3. **Compare multiple lots** — "Which of these 3 is the best buy?" → side-by-side analysis in a single report.
4. **Manage a watchlist** — the watchlist is `watchlist.base`, an Obsidian Base view over `reports/lot-*.md` with `status: watching`. Claude refreshes current bids by updating each report's `current_bid` frontmatter; the view reflects automatically. Lots are added to the watchlist by analyzing them (new reports default to `status: watching`) and removed by flipping `status` to `passed`.
5. **Ad-hoc investigations** — "Why is this priced oddly?" / "Is this retail price realistic?" → conversational; findings land in `findings/` when worth preserving.
6. **Resell evaluation** — toggleable per-lot. Claude evaluates whether expected net proceeds clear the 2× margin bar on FB Marketplace (primary resale venue) or eBay (fallback).

## Skills

- **`mac-bid`** — primary skill. Covers all workflows above; invoked with params indicating which workflow.
- **`ebay-comps`** — reusable: UPC → sold-price median via cascade search. Standalone so it can be used for non-mac.bid price lookups too.

Both live under `.claude/skills/` as project-local skills.

## Directory layout

This project is an Obsidian vault — open the project root in Obsidian. `cache/`, `scripts/`, `.claude/`, and `.git/` are hidden from Obsidian via `userIgnoreFilters` in `.obsidian/app.json`; Claude still reads/writes them normally.

```
mac-bid-claude/
  PROJECT.md              # this file
  watchlist.md            # thin wrapper that embeds watchlist.base
  watchlist.base          # Obsidian Base: reports where status == watching
  reports/lot-{lid}.md    # per-lot analysis reports (frontmatter + body)
  findings/               # ad-hoc investigation notes (wikilinks to reports)
  cache/
    lots/{lid}.json       # raw mac.bid lot payloads (also holds aid, upc, etc.)
    ebay/{hash}.json      # cached eBay cascade results
    buildings.json        # warehouse + tax-rate lookup
  scripts/                # Python helpers invoked by skills via Bash
  .claude/skills/         # mac-bid and ebay-comps skill definitions
  .claude/agents/         # mac-bid sub-agents
  .obsidian/              # vault config (excluded folders, plugins, property types)
```

## Data sources

- **mac.bid**: public endpoints — no login required.
  - `lid` is the canonical, stable identifier for a lot (short alphanumeric like `1795Q`, `3078E`) — use it alone for identity, dedup, cache keys (`cache/lots/{lid}.json`), report filenames (`reports/lot-{lid}.md`), and watchlist entries.
  - `aid` is a per-modal URL routing parameter (string — numeric like `79565` or alphanumeric like `MVL2604-24-A1`) that varies by context: the same underlying `lid` can surface under different `aid` values depending on which modal exposes it (watchlist vs search vs other). mac.bid modal URLs require `aid` as a query param, and upstream APIs (DDB, SSR) likely need one too, so scripts still accept and forward it — but it is NOT part of lot identity.
  - Lot URLs are modal-style — any mac.bid URL carrying both `aid` and `lid` query params encodes a specific lot, regardless of the page path. Shapes the user sees:
    - Watchlist modal: `https://www.mac.bid/account/watchlist?aid=<aid>&lid=<lid>`
    - Search modal: `https://www.mac.bid/search?q=...&aid=<aid>&lid=<lid>`
  - **Live bid (DDB)**: `GET https://api.macdiscount.com/map-bid/ddb/lot/{internal_id}` — returns current_bid, max_bid (proxy-bid ceiling of current winner), watchers_count, end_time, extension_window, is_open, total_bids, winning_bidder_id, location_id, auction_id, lot_number. The `internal_id` is the lot's numeric primary key — NOT the `aid` or `lid` from modal URLs. It's obtained from SSR hydration data at `props.pageProps.activeLot.id`.
  - **SSR page (for metadata)**: `GET https://www.mac.bid/search?aid={aid}&lid={lid}` renders the lot modal with a full `__NEXT_DATA__` JSON blob. Parse out `props.pageProps.activeLot` for all lot metadata (title, description, condition, UPC, retail, images, warehouse info, auction-level overrides). Deep-link URLs like `/lot/{aid}/{lid}` are unreliable — prefer the search modal URL pattern.
  - **Lot metadata (REST alternative)**: `GET https://api.macdiscount.com/auction/{auction_number}/lot/{lot_number}` — returns the same activeLot-shaped record as SSR extraction. Useful when you already have the auction_number (e.g., from a prior SSR scrape). Does NOT include live current_bid — DDB is the only source for that.
  - **Buildings**: `GET https://api.macdiscount.com/buildings` (already pinned).
- **eBay**: Browse API via OAuth2 client credentials. Cascade search: UPC → LLM-generated query → broadened query → relaxed without condition filter. Minimum 5 comps for a confident recommendation; below that, Claude labels the number advisory.
- **FB Marketplace**: no API. eBay sold comps serve as the price anchor for most items. For FB-sensitive categories, Claude prompts you to eyeball FB manually and paste what you see.
- **Product photos**: analyzed inline by Claude's vision. Looking for damage, missing parts, product mismatch; each flag carries a severity.

## Conventions

**Scripts** emit compact JSON on stdout. Caching, retries, and API quirks stay inside the scripts — Claude's context sees summaries, not raw payloads.

**Secrets** via 1Password CLI (`op read "op://..."`). eBay API credentials resolve from `op://Personal/za7ym3agvpwbszokahxsfr5sq4/{username,credential}` (item: "API Credentials", username field = eBay app ID, credential field = eBay cert ID). To avoid per-call Touch ID prompts, run `python3 scripts/refresh_ebay_credentials.py` once — it writes a 0600 cache to `~/.config/mac-bid-claude/ebay.env` that `scripts/ebay_search.py` sources automatically. Re-run after key rotation. Setting `EBAY_APP_ID`/`EBAY_CERT_ID` env vars overrides both the cache file and `op read`.

**Default home warehouses**: building IDs `15, 16, 6, 1` (Pittsburgh-area PA). Transfer = +$10. Remote = +$25. Home = $0.

**Personal-use max-bid formula**:
```
target_all_in = ebay_sold_median * (1 - discount_threshold)       # default discount = 30%
max_bid       = (target_all_in - lot_fee - location_cost) / (1 + buyers_premium + sales_tax)
```
Defaults: `buyers_premium = 15%`, `lot_fee = $3.00`, `sales_tax` per-building (~6% PA).

**Resell threshold**: expected net proceeds ≥ **2× all-in cost** to flag a lot "resell-worthy." Below that, still analyzed, labeled "marginal for resell."

**FB-sensitive categories** (living list — extend as you learn): large furniture, mattresses, major appliances, grills/outdoor equipment, exercise equipment, anything heavy or awkward to ship.

**Report frontmatter schema** (every `reports/lot-*.md` starts with this YAML block):

```yaml
lot_id, title, warehouse_id, warehouse_name, condition,
current_bid, max_bid, closes_at (ISO-8601 with TZ),
status, recommend, resell_eligible
```

Derived values (`deal_score`, `past_max`, `headroom`) are **not stored** — `watchlist.base` computes them via formulas so they can't go stale when `current_bid` updates. Lot identifiers (`aid`, `auction_number`, `internal_id`, `upc`) live in `cache/lots/{lid}.json`, not frontmatter.

**Status values** (`status` is the only field the user owns; Claude never flips it autonomously):

| Value | Meaning |
|---|---|
| `watching` | Active on the watchlist — appears in `watchlist.base` |
| `passed` | Decided not to bid |
| `bid` | Bid placed, auction still live |
| `won` / `lost` | Auction resolved |
| `archived` | Historical; cleared from all views |

**Obsidian CLI for property writes**: when Claude updates a single frontmatter field (`current_bid` on refresh, `status` on remove), prefer `obsidian-cli property set` over regex-editing the YAML — preserves ordering and types.

**Condition gating**:
- `NEW` / `LIKE NEW` / `OPEN BOX` + ≥5 comps → auto-recommend a max bid.
- `USED` / `SALVAGE` / `DAMAGED` → flag for manual review unless you provide override context.

**Deal score**: `(max_bid - current_bid) / max_bid * 100`. 90 = amazing early-auction deal, 10 = near your max, ≤0 = skip.

## Non-goals

- **No autonomous bidding.** Claude never places a bid. Research and recommendations only — you always bid yourself.
- **No server, no extension, no bot.** That's the sibling project's domain.
- **No shared code with mac-bid-analyzer.** Independent by design.
- **No mac.bid login** for the primary workflows. The public endpoints cover everything research-related. `agent-browser` stays available for edge cases (inspecting logged-in pages), but it's not the default fetch path.

## Open questions

- eBay Browse API returns active listings, not sold — the comp median runs ~15-20% above true sold prices. Decide whether to switch to the Marketplace Insights API (restricted access, app allowlist needed) or mechanically adjust (e.g., multiply active median by 0.85 before feeding to max-bid).
- Whether multi-warehouse transfer cost should be modeled more granularly than the flat `$0 / $10 / $25` tiers.
- Whether discovery queries should cache aggressively or fetch fresh each session.
- When `ebay-comps` grows enough to live outside this project as a globally-installed skill.
