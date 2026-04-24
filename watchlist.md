# Watchlist

The live view is below — it's a [Base](https://help.obsidian.md/bases) that queries every `reports/lot-*.md` file with `status: watching` in its frontmatter.

![[watchlist.base]]

## How it works

- **Adding a lot**: run the analyze workflow (hand Claude a mac.bid URL). The resulting report is written with `status: watching` and automatically appears here.
- **Removing a lot**: flip its `status` to `passed` (you lost interest), `bid` (you placed a bid), `won`, `lost`, or `archived`. Ask Claude or edit the frontmatter directly.
- **Refreshing current bids**: ask Claude to "refresh the watchlist" — it re-fetches each lot's live DDB payload and updates `current_bid` in place via `obsidian-cli`. The `Past max` and `Deal score` formulas recompute automatically.
- **The one field you own**: `status`. Everything else is Claude-maintained.

## Status values

| Value | Meaning |
|---|---|
| `watching` | Active on the watchlist — appears in the Base view above |
| `passed` | Decided not to bid |
| `bid` | Bid placed, auction still live |
| `won` | Won the auction |
| `lost` | Lost the auction |
| `archived` | Historical; cleared from all views |
