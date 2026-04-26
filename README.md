# mac-bid-claude

A Claude Code workspace for researching auction lots on [mac.bid](https://www.mac.bid). Hand Claude a URL or a question; it pulls listing data, finds eBay comps, computes a recommended max bid, analyzes the product photos, and writes a markdown report. Then it stays available for follow-up questions.

This is an **interactive workspace**, not an application. Claude does the research, you ask follow-ups, findings land in files.

> Claude never places bids. Research and recommendations only.

See [PROJECT.md](PROJECT.md) for the full design rationale, data sources, formulas, and conventions.

## Setup

1. **Clone and open in Claude Code**
   ```sh
   cd /path/to/mac-bid-claude
   claude
   ```

2. **Cache eBay credentials** (one-time, avoids Touch ID prompts on every comp lookup)
   ```sh
   python3 scripts/refresh_ebay_credentials.py
   ```
   Reads from 1Password (`op://Personal/...`) and writes a 0600 cache to `~/.config/mac-bid-claude/ebay.env`. Re-run after key rotation.

3. **(Optional) Open the project root in Obsidian** to browse reports and the watchlist as a vault. `cache/`, `scripts/`, `.claude/` are hidden from Obsidian via `.obsidian/app.json`.

## How to use it

Just talk to Claude. Common asks:

| You say | What happens |
|---|---|
| "Analyze `https://www.mac.bid/lot/...`" | Fetches lot, runs eBay cascade, vision-analyzes photos, writes `reports/lot-{lid}.md` |
| "What's closing tonight at my warehouses?" | Queries open lots, ranks by deal score, drafts a shortlist |
| "Which of these 3 is the best buy?" | Side-by-side comparison report |
| "Refresh my watchlist" | Updates `current_bid` on every report with `status: watching` |
| "Pass on lot 1795Q" | Flips that report's `status` to `passed` (drops it from the watchlist view) |
| "Why is this priced oddly?" | Conversational investigation; writes to `findings/` if worth keeping |
| "Could I resell this?" | Evaluates expected net proceeds against the 2× margin bar (FB Marketplace primary, eBay fallback) |

The `mac-bid` skill routes all of these. You don't need to invoke it explicitly — just describe what you want.

## Where things live

```
reports/lot-{lid}.md    per-lot analysis (frontmatter + body)
findings/               ad-hoc investigation notes
watchlist.md            embeds watchlist.base (Obsidian view)
watchlist.base          Base view: reports where status == watching
cache/lots/{lid}.json   raw mac.bid lot payloads
cache/ebay/{hash}.json  cached eBay comp results
scripts/                Python helpers Claude calls via Bash
.claude/skills/         mac-bid and ebay-comps skill definitions
```

## Defaults worth knowing

- **Home warehouses**: building IDs `15, 16, 6, 1` (Pittsburgh-area PA)
- **Discount threshold**: 30% off eBay sold median
- **Buyer's premium**: 15% · **Lot fee**: $3 · **Sales tax**: ~6% PA
- **Transfer cost**: +$10 · **Remote**: +$25 · **Home**: $0
- **Resell flag**: net proceeds ≥ 2× all-in cost

These are encoded in the max-bid formula in PROJECT.md. Override per-lot in conversation.
