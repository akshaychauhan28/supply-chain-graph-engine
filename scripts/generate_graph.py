"""Generate the synthetic supply network and report what came out.

    python scripts/generate_graph.py
    python scripts/generate_graph.py --seed 7 --out data

The summary this prints is not decoration. It is the first check on whether the
generative rules produced a supply chain or just a layered random graph, and it
is worth reading every line before trusting anything downstream. Session 2 turns
these eyeball checks into real statistical tests.
"""

from __future__ import annotations

import argparse
import pickle
import sys
from collections import Counter
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg           # noqa: E402
from src.generator import generate      # noqa: E402
from src.schema import Tier             # noqa: E402


def human(n: float) -> str:
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(n) >= div:
            return f"{n / div:,.1f}{unit}"
    return f"{n:,.0f}"


def rule(title: str = "") -> None:
    print(f"\n{'=' * 74}")
    if title:
        print(title)
        print("=" * 74)


def report(net) -> None:
    com, phy = net.commercial, net.physical

    rule("POPULATION")
    for tier in Tier:
        members = [n for n in net.nodes.values() if n.tier is tier]
        if members:
            print(f"  {tier.name:<18} {len(members):>6,}")
    print(f"  {'TOTAL':<18} {len(net.nodes):>6,}")

    rule("GRAPH SIZE")
    print(f"  commercial   {com.number_of_nodes():>6,} nodes   "
          f"{com.number_of_edges():>7,} lanes   "
          f"density {nx.density(com):.5f}")
    print(f"  physical     {phy.number_of_nodes():>6,} nodes   "
          f"{phy.number_of_edges():>7,} lanes   "
          f"density {nx.density(phy):.5f}")
    print(f"  supply relationships (BOM lines): {len(net.relationships):,}")
    print(f"  commercial graph is a DAG: {nx.is_directed_acyclic_graph(com)}")
    print(f"  physical graph is a DAG:   {nx.is_directed_acyclic_graph(phy)}")

    # If finished goods are not reachable from raw materials, the network is
    # not a supply chain at all -- it is disconnected layers -- and every
    # downstream result would be meaningless. Cheapest possible check that the
    # BOM wiring actually joined the tiers up.
    rule("CONNECTIVITY  (commercial projection)")
    raw = [n for n, a in com.nodes(data=True) if a["tier_name"] == Tier.RAW_MATERIAL.name]
    oem = {n for n, a in com.nodes(data=True) if a["tier_name"] == Tier.OEM.name}
    probe = com.copy()
    probe.add_edges_from(("__source__", n) for n in raw)
    downstream = nx.descendants(probe, "__source__")
    reached = oem & downstream
    print(f"  finished goods reachable from raw materials : "
          f"{len(reached):,} / {len(oem):,} ({len(reached) / len(oem):.1%})")
    # dag_longest_path_length uses the `weight` attribute unless told
    # otherwise, and our weight is lead time in days -- so ask for both.
    print(f"  longest supply path (hops)                  : "
          f"{nx.dag_longest_path_length(com, weight=None)}")
    print(f"  worst-case cumulative lead time (days)      : "
          f"{nx.dag_longest_path_length(com):,.0f}")

    rule("DEGREE CONCENTRATION  (commercial projection)")
    sellers = [
        n for n, a in com.nodes(data=True) if a["tier_name"] != Tier.OEM.name
    ]
    orphans = [n for n in sellers if com.out_degree(n) == 0]
    print(f"  firms that could sell    : {len(sellers):,}")
    print(f"  firms with no customers  : {len(orphans):,} "
          f"({len(orphans) / len(sellers):.1%})")
    out_deg = np.array([d for _, d in com.out_degree()], dtype=float)
    nonzero = out_deg[out_deg > 0]
    print(f"  suppliers with >=1 customer : {len(nonzero):,}")
    print(f"  mean customers per supplier : {nonzero.mean():.2f}")
    print(f"  median                      : {np.median(nonzero):.0f}")
    print(f"  max                         : {nonzero.max():.0f}")
    ordered = np.sort(nonzero)[::-1]
    for pct in (0.01, 0.05, 0.10):
        cut = max(1, int(len(ordered) * pct))
        share = ordered[:cut].sum() / ordered.sum()
        print(f"  top {pct:>4.0%} of suppliers hold  : {share:>6.1%} of all customer links")

    rule("REGIONAL CONCENTRATION  (did the geography rules land?)")
    for tier in (Tier.FAB, Tier.EQUIPMENT, Tier.REFINED_MATERIAL, Tier.EMS):
        counts = Counter(
            n.region for n in net.nodes.values() if n.tier is tier
        )
        total = sum(counts.values())
        top = ", ".join(
            f"{r} {c / total:.0%}" for r, c in counts.most_common(3)
        )
        print(f"  {tier.name:<18} {top}")

    rule("SOURCING FRAGILITY")
    # Count BOM *lines* (one buyer, one input), not relationships. A
    # single-sourced line contributes one relationship while a triple-sourced
    # line contributes three, so measuring over relationships silently halves
    # the apparent single-sourcing rate.
    lines: dict[tuple[str, str], int] = {}
    for r in net.relationships:
        lines[(r.target, str(r.category))] = r.qualified_alternatives
    single = sum(1 for alt in lines.values() if alt == 0)
    print(f"  BOM lines (buyer x input) : {len(lines):,}")
    print(f"  single-sourced            : {single:,} ({single / len(lines):.1%})")
    print(f"  underlying relationships  : {len(net.relationships):,}")

    by_cat_single: Counter[str] = Counter()
    by_cat_total: Counter[str] = Counter()
    for (_, cat), alt in lines.items():
        by_cat_total[cat] += 1
        if alt == 0:
            by_cat_single[cat] += 1
    worst = sorted(
        ((c, by_cat_single[c] / n, by_cat_single[c])
         for c, n in by_cat_total.items() if n >= 30),
        key=lambda x: -x[1],
    )[:6]
    print("  most single-sourced inputs:")
    for cat, share, n in worst:
        print(f"    {cat:<24} {share:>6.1%}  ({n:,} lines)")

    rule("ECONOMICS")
    total_value = sum(r.annual_value_usd for r in net.relationships)
    print(f"  total annual flow value : ${human(total_value)}")
    lead = np.array([r.lead_time_days for r in net.relationships])
    print(f"  lead time  median {np.median(lead):.0f}d   "
          f"p90 {np.percentile(lead, 90):.0f}d   max {lead.max():.0f}d")

    rule("BIGGEST SUPPLIERS BY CUSTOMER COUNT  (eyeball check only)")
    print("  These are NOT the analysis results -- raw degree is the crudest")
    print("  possible measure. Session 3 replaces this with centrality and")
    print("  node-removal simulation, and the rankings will change.\n")
    top = sorted(com.out_degree(), key=lambda kv: -kv[1])[:12]
    for node_id, deg in top:
        a = com.nodes[node_id]
        print(f"    {deg:>4} customers  {a['tier_name']:<17} {a['region']:<16} {a['name']}")

    rule("FREIGHT ROUTING  (physical projection)")
    routed = sum(1 for r in net.relationships if r.via_hub)
    cross = sum(
        1 for r in net.relationships
        if net.nodes[r.source].region != net.nodes[r.target].region
    )
    print(f"  cross-border relationships : {cross:,} ({cross / len(net.relationships):.0%})")
    print(f"  routed through a hub       : {routed:,} ({routed / max(cross, 1):.0%} of cross-border)")
    hub_load = Counter(r.via_hub for r in net.relationships if r.via_hub)
    print("  busiest hubs:")
    for hub_id, n in hub_load.most_common(6):
        node = net.nodes[hub_id]
        print(f"    {n:>5} shipments  {node.name} ({node.region})")


def save(net, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    nx.write_graphml(net.commercial, out_dir / "commercial.graphml")
    nx.write_graphml(net.physical, out_dir / "physical.graphml")
    with open(out_dir / "network.pkl", "wb") as fh:
        pickle.dump(net, fh)

    pd.DataFrame([n.to_attrs() | {"node_id": n.node_id} for n in net.nodes.values()]).to_csv(
        out_dir / "nodes.csv", index=False
    )
    pd.DataFrame([r.to_row() for r in net.relationships]).to_csv(
        out_dir / "relationships.csv", index=False
    )
    print(f"\nWrote graphml, pickle and CSVs to {out_dir}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=cfg.RANDOM_SEED)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args()

    problems = cfg.validate_config()
    if problems:
        print("Config is inconsistent -- fix these before generating:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("Config validated: BOM respects tier ordering, all distributions sum to 1.")

    net = generate(seed=args.seed)
    report(net)
    if not args.no_save:
        save(net, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
