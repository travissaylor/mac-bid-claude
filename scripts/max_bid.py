#!/usr/bin/env python3
"""Compute a recommended max bid for a mac.bid lot from an eBay sold median.

Pure-calculation helper. Inputs via CLI flags, compact JSON on stdout.
See PROJECT.md (Conventions) for the canonical formula.
"""

import argparse
import json
import sys

LOCATION_TIERS = {"home": 0.0, "transfer": 10.0, "remote": 25.0}


def fail(msg: str) -> None:
    sys.stdout.write(json.dumps({"error": msg}, separators=(",", ":")))
    sys.exit(1)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Recommend a max bid for a mac.bid lot.",
        add_help=True,
    )
    p.add_argument("--median", type=float, required=True,
                   help="eBay sold median in USD")
    p.add_argument("--tax", type=float, required=True,
                   help="Sales tax rate, e.g. 0.06 for 6 percent")
    p.add_argument("--location", choices=list(LOCATION_TIERS.keys()),
                   default="home", help="Warehouse location tier")
    p.add_argument("--location-cost", type=float, default=None,
                   help="Override location cost (USD)")
    p.add_argument("--lot-fee", type=float, default=3.00,
                   help="Flat lot fee in USD (default 3.00)")
    p.add_argument("--buyers-premium", type=float, default=0.15,
                   help="Buyer's premium rate (default 0.15)")
    p.add_argument("--discount", type=float, default=0.30,
                   help="Discount threshold (default 0.30)")
    p.add_argument("--current-bid", type=float, default=None,
                   help="Current bid in USD; enables deal-score output")
    p.add_argument("--resell", action="store_true",
                   help="Also compute resell-worthiness check")
    p.add_argument("--floor-median", type=float, default=None,
                   help="Pessimistic median in USD; enables max_bid_floor output")
    p.add_argument("--floor-source", type=str, default=None,
                   help="Descriptive label for floor median source (e.g. p25, for-parts)")
    return p.parse_args()


def validate(args: argparse.Namespace) -> None:
    if args.median < 0:
        fail("--median must be >= 0")
    if args.tax < 0 or args.tax > 1:
        fail("--tax must be between 0 and 1")
    if args.buyers_premium < 0 or args.buyers_premium > 1:
        fail("--buyers-premium must be between 0 and 1")
    if args.discount < 0 or args.discount > 1:
        fail("--discount must be between 0 and 1")
    if args.lot_fee < 0:
        fail("--lot-fee must be >= 0")
    if args.location_cost is not None and args.location_cost < 0:
        fail("--location-cost must be >= 0")
    if args.current_bid is not None and args.current_bid < 0:
        fail("--current-bid must be >= 0")
    if args.floor_median is not None and args.floor_median < 0:
        fail("--floor-median must be >= 0")


def main() -> None:
    args = parse_args()
    validate(args)

    location_cost = (args.location_cost if args.location_cost is not None
                     else LOCATION_TIERS[args.location])

    target_all_in = args.median * (1 - args.discount)
    denom = 1 + args.buyers_premium + args.tax
    max_bid = (target_all_in - args.lot_fee - location_cost) / denom

    warnings: list[str] = []
    if args.median < 5:
        warnings.append(
            "median low — verify comp quality before trusting output"
        )
    if max_bid <= 0:
        warnings.append("max_bid <= 0 — skip this lot")
    elif max_bid < 1:
        warnings.append("max_bid below $1 — functionally skip")
    if args.current_bid is not None and args.current_bid > max_bid:
        warnings.append("already past max — skip")

    target_all_in_floor = None
    max_bid_floor = None
    if args.floor_median is not None:
        target_all_in_floor = args.floor_median * (1 - args.discount)
        max_bid_floor = (target_all_in_floor - args.lot_fee - location_cost) / denom
        if max_bid_floor <= 0:
            warnings.append("max_bid_floor <= 0 — floor unusable")
        elif max_bid_floor < 1:
            warnings.append("max_bid_floor below $1 — functionally skip at floor")
        if args.current_bid is not None and args.current_bid > max_bid_floor:
            warnings.append("current bid past max_bid_floor")

    deal_score = None
    if args.current_bid is not None and max_bid > 0:
        deal_score = round((max_bid - args.current_bid) / max_bid * 100, 1)

    inputs: dict = {
        "median": round(args.median, 2),
        "discount": args.discount,
        "buyers_premium": args.buyers_premium,
        "lot_fee": round(args.lot_fee, 2),
        "location": args.location,
        "location_cost": round(location_cost, 2),
        "tax": args.tax,
    }
    if args.floor_median is not None:
        inputs["floor_median"] = round(args.floor_median, 2)
        inputs["floor_source"] = args.floor_source

    out: dict = {
        "inputs": inputs,
        "target_all_in": round(target_all_in, 2),
    }
    if args.floor_median is not None:
        out["target_all_in_floor"] = round(target_all_in_floor, 2)
    out["max_bid"] = round(max_bid, 2)
    if args.floor_median is not None:
        out["max_bid_floor"] = round(max_bid_floor, 2)
    out["deal_score"] = deal_score

    if args.resell:
        effective_max = max(max_bid, 0.0)
        all_in_at_max = (effective_max * (1 + args.buyers_premium + args.tax)
                         + args.lot_fee + location_cost)
        expected_net = args.median * 0.87
        if all_in_at_max > 0:
            ratio = expected_net / all_in_at_max
        else:
            ratio = float("inf")
        if ratio >= 2:
            label = "resell-worthy"
        elif ratio >= 1.5:
            label = "marginal for resell"
        else:
            label = "below threshold"
        out["resell"] = {
            "expected_net_usd": round(expected_net, 2),
            "all_in_at_max_bid": round(all_in_at_max, 2),
            "ratio": round(ratio, 2) if ratio != float("inf") else None,
            "label": label,
        }

    out["warnings"] = warnings

    sys.stdout.write(json.dumps(out, separators=(",", ":")))


if __name__ == "__main__":
    main()
