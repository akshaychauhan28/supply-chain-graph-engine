"""Rank the network's critical nodes two ways and compare the answers.

    python scripts/analyze_network.py
    python scripts/analyze_network.py --no-save

Centrality is computed on the physical projection -- the one containing ports
and air hubs -- because that is the graph the failure simulation operates on.
Scoring commercial-graph centrality against a simulation that models logistics
would be rigged: hubs are not in that graph, so centrality could never find
them, and the comparison would prove nothing except that the two were measuring
different networks.

The comparison at the end is the point of the exercise: where a cheap
topological proxy and a measured outcome disagree, and in which direction.
"""

from __future__ import annotations

import argparse
import pickle
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg                                   # noqa: E402
from src.analysis import (                                      # noqa: E402
    DisruptionSimulator,
    centrality_table,
    compare_rankings,
    physical_centrality_table,
    rank_correlation,
)
from src.generator import generate                              # noqa: E402


def human(n: float) -> str:
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"${n / div:,.1f}{unit}"
    return f"${n:,.0f}"


def rule(title: str) -> None:
    print(f"\n{'=' * 92}\n{title}\n{'=' * 92}")


def fmt(col: str, v) -> str:
    if pd.isna(v):
        return "-"
    if isinstance(v, str):
        return v
    # Order matters: "share_of_oem_value" contains both words, and formatting
    # a proportion as currency prints every row as "$0".
    if "share" in col:
        return f"{v:.1%}"
    if "value" in col:
        return human(v)
    if "rank" in col or "gap" in col or col.endswith(("firms", "oems", "cascade")):
        return f"{v:,.0f}"
    if isinstance(v, float):
        return f"{v:.5f}"
    return f"{v:,}"


def show(df: pd.DataFrame, cols: dict[str, str], n: int = 15) -> None:
    head = df.head(n)
    widths = {}
    for c, label in cols.items():
        rendered = [fmt(c, v) for v in head[c]]
        widths[c] = max(len(label), max((len(s) for s in rendered), default=0)) + 2
    print("  " + "".join(f"{label:<{widths[c]}}" for c, label in cols.items()))
    print("  " + "-" * sum(widths.values()))
    for _, row in head.iterrows():
        print("  " + "".join(f"{fmt(c, row[c]):<{widths[c]}}" for c in cols))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=cfg.RANDOM_SEED)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    cached = args.data / "network.pkl"
    if cached.exists():
        with open(cached, "rb") as fh:
            net = pickle.load(fh)
        print(f"Loaded network (seed {net.seed})")
    else:
        net = generate(seed=args.seed)

    meta = pd.DataFrame([
        {"node_id": nid, "name": n.name, "tier": n.tier.name,
         "region": str(n.region), "category": str(n.category)}
        for nid, n in net.nodes.items()
    ])

    # -- centrality --------------------------------------------------------
    t0 = time.perf_counter()
    print("Computing centrality on the physical projection...")
    cent = physical_centrality_table(net.physical).merge(meta, on="node_id", how="left")
    print(f"  done in {time.perf_counter() - t0:.1f}s")

    rule("WHAT CENTRALITY SAYS IS IMPORTANT")
    print("\n  By betweenness -- sits on the most supply paths:\n")
    show(cent.sort_values("betweenness", ascending=False),
         {"name": "node", "tier": "tier", "region": "region",
          "customers": "cust", "betweenness": "betweenness"}, n=12)
    print("\n  By PageRank on the reversed graph -- most depended upon:\n")
    show(cent.sort_values("pagerank", ascending=False),
         {"name": "node", "tier": "tier", "region": "region",
          "customers": "cust", "pagerank": "pagerank"}, n=12)

    # -- simulation --------------------------------------------------------
    sim = DisruptionSimulator(net)
    n_candidates = sum(1 for n in net.nodes.values() if n.tier.name != "OEM")
    print(f"\nSimulating failure of every non-OEM node ({n_candidates:,} candidates)...")
    t0 = time.perf_counter()
    impact = sim.rank_single_failures(reroute_freight=True)
    stress = sim.rank_single_failures(reroute_freight=False)
    print(f"  done in {time.perf_counter() - t0:.1f}s")
    print(f"  total OEM production value modelled: {human(sim.total_oem_value)}")

    rule("WHAT ACTUALLY BREAKS  (single-node failure, BOM-aware cascade)")
    print("\n  A firm fails when ANY required input loses every qualified supplier.")
    print("  Freight reroutes through an alternative gateway where one exists.\n")
    show(impact, {"name": "node", "tier": "tier", "region": "region",
                  "category": "produces", "failed_oems": "OEMs lost",
                  "share_of_oem_value": "share", "production_value_at_risk": "at risk"})

    rule("STRESS CASE  (same failures, freight cannot reroute)")
    print("\n  A blockade rather than a closed facility. Much stronger assumption --")
    print("  useful as an upper bound, misleading if quoted as a port closure.\n")
    show(stress[stress["tier"] == "LOGISTICS"],
         {"name": "gateway", "region": "region", "failed_oems": "OEMs lost",
          "share_of_oem_value": "share", "production_value_at_risk": "at risk"}, n=6)

    # -- the comparison ----------------------------------------------------
    merged = compare_rankings(cent, impact)

    rule("DO THE PROXIES AGREE WITH THE MEASUREMENT?")
    print()
    for measure in ("customers", "betweenness", "pagerank"):
        rho = rank_correlation(merged, measure)
        print(f"  Spearman rank correlation, {measure:<12} vs measured impact : {rho:+.3f}")
    print()
    print("  Correlation over all nodes flatters the proxies -- most nodes break")
    print("  nothing and tie at the bottom of both rankings. The question that")
    print("  matters is whether the top of the centrality list is the top of the")
    print("  damage list, because that is the list someone would actually act on.")

    for k in (10, 25, 50):
        top_imp = set(impact.head(k)["node_id"])
        line = []
        for measure in ("betweenness", "pagerank", "customers"):
            hits = len(set(cent.nsmallest(k, f"rank_{measure}")["node_id"]) & top_imp)
            line.append(f"{measure} {hits:>2}/{k}")
        print(f"\n  Top {k:>2} most damaging nodes found by:  " + "   ".join(line))

    rule("THE NODES CENTRALITY UNDERRATES")
    print("\n  High measured impact, poor centrality rank -- what a topology-only")
    print("  analysis would have missed entirely.\n")
    blind = merged[merged["rank_impact"] <= 40].sort_values("rank_gap", ascending=False)
    show(blind, {"name": "firm", "tier": "tier", "category": "produces",
                 "rank_impact": "impact#", "best_centrality_rank": "best cent#",
                 "rank_gap": "gap", "share_of_oem_value": "share"}, n=10)

    rule("AND THE ONES IT OVERRATES")
    print("\n  Prominent in the graph, harmless when removed -- every buyer they\n"
          "  serve has an alternative.\n")
    overrated = merged[merged["best_centrality_rank"] <= 40].sort_values("rank_gap")
    show(overrated, {"name": "firm", "tier": "tier", "category": "produces",
                     "best_centrality_rank": "best cent#", "rank_impact": "impact#",
                     "failed_firms": "firms lost", "share_of_oem_value": "share"}, n=8)

    rule("CASCADE BEHAVIOUR")
    print(f"\n  nodes whose failure spreads beyond direct customers : "
          f"{(impact['deepest_cascade'] > 1).sum():,} / {len(impact):,}")
    print(f"  deepest cascade observed                            : "
          f"{impact['deepest_cascade'].max()} rounds")
    print(f"  nodes halting >1% of production value               : "
          f"{(impact['share_of_oem_value'] > 0.01).sum():,}")
    print(f"  nodes halting >5% of production value               : "
          f"{(impact['share_of_oem_value'] > 0.05).sum():,}")
    print(f"  nodes that break nothing at all                     : "
          f"{(impact['failed_firms'] == 0).sum():,} "
          f"({(impact['failed_firms'] == 0).mean():.0%})")

    by_tier = impact.groupby("tier")["share_of_oem_value"].agg(["max", "mean", "count"])
    print("\n  worst single failure by tier:")
    for tier, row in by_tier.sort_values("max", ascending=False).iterrows():
        print(f"    {tier:<18} worst {row['max']:>6.1%}   mean {row['mean']:>6.2%}   "
              f"({int(row['count']):,} nodes)")

    if not args.no_save:
        args.data.mkdir(parents=True, exist_ok=True)
        cent.to_csv(args.data / "centrality.csv", index=False)
        impact.to_csv(args.data / "disruption_impact.csv", index=False)
        stress.to_csv(args.data / "disruption_impact_blockade.csv", index=False)
        merged.to_csv(args.data / "criticality_comparison.csv", index=False)
        print(f"\nWrote centrality.csv, disruption_impact.csv, "
              f"disruption_impact_blockade.csv and criticality_comparison.csv "
              f"to {args.data}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
