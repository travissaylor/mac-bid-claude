# Watchlist

Source of truth for lots being watched. Claude reads this file, refreshes current bids, flags lots that have moved past your max, and adds/removes entries on request.

## How to use

Add a line under `## Watchlist` in this format:

`- [ ] lot-{id} — {short label} — max $XX`

The checkbox is the past-max flag: Claude checks it when the current bid exceeds your max.

## Watchlist

<!-- Example: - [ ] lot-12345678 — Dewalt drill kit — max $45 -->

## Archive

<!-- Lots you have bid on or passed on. Move entries here from Watchlist when done. -->
