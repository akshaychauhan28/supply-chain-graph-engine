"""Test whether the generated network actually has the structure we claim.

    python scripts/validate_topology.py
    python scripts/validate_topology.py --boot 500

Three questions, in order of how much they matter:

  1. Is the supplier degree distribution heavy-tailed, and can we put a number
     and a confidence statement on it?
  2. Is a power law actually the right description, or does a lognormal fit the
     data just as well or better?
  3. Is any of this distinguishable from a random graph -- and specifically,
     is there structure left over once degree alone is accounted for?

Question 3 is the one that decides whether the generator did anything. If a
configuration model with our exact degree sequence reproduces every other
property, then "supply chain" was just a label on a random graph.
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg          # noqa: E402
from src import validate as V          # noqa: E402
from src.generator import generate     # noqa: E402
from src.schema import Tier            # noqa: E402


def rule(title: str) -> None:
    print(f"\n{'=' * 76}\n{title}\n{'=' * 76}")


def load(path: Path, seed: int):
    cached = path / "network.pkl"
    if cached.exists():
        with open(cached, "rb") as fh:
            net = pickle.load(fh)
        if net.seed == seed:
            print(f"Loaded cached network (seed {seed}) from {cached}")
            return net
    print(f"Generating network (seed {seed})...")
    return generate(seed=seed)


def describe_fit(name: str, data: np.ndarray, n_boot: int, seed: int) -> None:
    print(f"\n  {name}")
    print(f"    observations       : {len(data):,}  "
          f"(min {data.min():.0f}, median {np.median(data):.0f}, max {data.max():.0f})")

    fit = V.fit_power_law(data)
    print(f"    power-law fit      : alpha = {fit.alpha:.3f}, "
          f"x_min = {fit.x_min}, tail = {fit.n_tail:,} ({fit.tail_fraction:.0%})")
    print(f"    KS distance        : {fit.ks_distance:.4f}")

    fit.p_value = V.bootstrap_gof(data, fit, n_boot=n_boot, seed=seed)
    print(f"    bootstrap p-value  : {fit.p_value:.3f}   -> power law {fit.verdict}")

    if not fit.is_informative:
        print(f"    An exponent of {fit.alpha:.1f} is not scaling behaviour -- it is the")
        print("    estimator describing a distribution that falls off far too fast")
        print("    to be a power law. For a quantity bounded by construction this is")
        print("    the correct and expected outcome, not a problem to tune away.")
        return

    print("                         (p > 0.10 means we cannot reject it;")
    print("                          it never proves the distribution)")

    cmp = V.compare_lognormal(data, fit)
    print(f"    vs lognormal       : LR = {cmp.log_likelihood_ratio:+.2f}, "
          f"p = {cmp.p_value:.3f}")
    print(f"                         -> {cmp.verdict}")
    print(f"                         (lognormal mu={cmp.mu:.2f}, sigma={cmp.sigma:.2f})")


def compare_nulls(net, seed: int) -> None:
    com = net.commercial
    real = V.summarise(com, "generated", seed=seed)
    er = V.summarise(V.erdos_renyi_like(com, seed=seed), "erdos-renyi", seed=seed)
    conf = V.summarise(V.configuration_like(com, seed=seed), "configuration", seed=seed)

    header = f"  {'metric':<26}{'generated':>13}{'erdos-renyi':>14}{'config model':>15}"
    print(header)
    print(f"  {'-' * (len(header) - 2)}")

    rows = [
        ("nodes", "n_nodes", "{:,.0f}"),
        ("edges", "n_edges", "{:,.0f}"),
        ("max out-degree", "max_out_degree", "{:,.0f}"),
        ("out-degree Gini", "gini_out_degree", "{:.3f}"),
        ("top decile share", "top_decile_share", "{:.1%}"),
        ("clustering", "clustering", "{:.4f}"),
        ("mean path length", "mean_path_length", "{:.2f}"),
        ("p90 path length", "p90_path_length", "{:.1f}"),
        ("reachable pairs", "reachable_pairs_frac", "{:.2%}"),
    ]
    for label, attr, fmt in rows:
        vals = [fmt.format(getattr(s, attr)) for s in (real, er, conf)]
        print(f"  {label:<26}{vals[0]:>13}{vals[1]:>14}{vals[2]:>15}")

    print()
    print("  Erdos-Renyi keeps only node and edge counts, so any difference means")
    print("  the network is not randomly wired. The configuration model is the")
    print("  demanding comparison: it reproduces every node's in- and out-degree")
    print("  exactly, so whatever still differs is structure that degree alone")
    print("  cannot explain -- it comes from the tier ordering and geography.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=cfg.RANDOM_SEED)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--boot", type=int, default=300,
                    help="bootstrap replicates for the goodness-of-fit test")
    args = ap.parse_args()

    net = load(args.data, args.seed)
    com = net.commercial

    rule("DEGREE DISTRIBUTIONS  (commercial projection)")
    out_deg = np.array([d for _, d in com.out_degree() if d > 0], dtype=int)
    describe_fit("OUT-DEGREE  (customers per supplier)", out_deg, args.boot, args.seed)

    in_deg = np.array([d for _, d in com.in_degree() if d > 0], dtype=int)
    describe_fit("IN-DEGREE  (suppliers per buyer)", in_deg, args.boot, args.seed)

    print("\n  In-degree is not expected to be heavy-tailed and it would be a")
    print("  warning sign if it were. A buyer's supplier count is capped by the")
    print("  length of its bill of materials times a handful of vendors per")
    print("  line, so it is bounded by construction. Out-degree is unbounded --")
    print("  nothing stops one firm selling to everyone -- which is why the")
    print("  interesting concentration lives there.")

    rule("PER-TIER CONCENTRATION")
    print(f"  {'tier':<20}{'suppliers':>11}{'max cust.':>11}{'Gini':>8}{'top 10%':>10}")
    print(f"  {'-' * 58}")
    for tier in (Tier.RAW_MATERIAL, Tier.REFINED_MATERIAL, Tier.EQUIPMENT,
                 Tier.FAB, Tier.OSAT, Tier.EMS):
        degs = np.array([
            d for n, d in com.out_degree()
            if com.nodes[n]["tier_name"] == tier.name and d > 0
        ], dtype=float)
        if len(degs) < 5:
            continue
        ordered = np.sort(degs)[::-1]
        decile = max(1, len(ordered) // 10)
        print(f"  {tier.name:<20}{len(degs):>11,}{degs.max():>11,.0f}"
              f"{V.gini(degs):>8.3f}{ordered[:decile].sum() / ordered.sum():>10.1%}")
    print()
    print("  Gini here is inequality in customer counts within a tier. Higher")
    print("  means a few firms in that tier carry disproportionately many")
    print("  relationships -- which is where systemic risk accumulates.")

    rule("NULL MODEL COMPARISON  (commercial projection)")
    compare_nulls(net, args.seed)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
