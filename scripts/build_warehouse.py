"""Load the network and its analysis into a star schema, then run the SQL.

    python scripts/build_warehouse.py
    python scripts/build_warehouse.py --db postgresql+psycopg2://user:pw@localhost/supplychain

Defaults to SQLite at data/warehouse.db so this runs with no setup at all.
Point --db at PostgreSQL and the same code and the same SQL run unchanged --
nothing in the schema or the queries uses a dialect-specific feature.

Every query in sql/ is executed and written to data/analytics/ as CSV, which is
what the Power BI layer consumes. Power BI can connect to the database
directly, but going through flat exports keeps the dashboard reproducible for
anyone who clones the repo without a database running.
"""

from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg                                   # noqa: E402
from src.analysis import (                                      # noqa: E402
    DisruptionSimulator,
    compare_rankings,
    physical_centrality_table,
)
from src.generator import generate                              # noqa: E402
from src.warehouse import build_warehouse, create_engine_for    # noqa: E402

SQL_DIR = ROOT / "sql"


def rule(title: str) -> None:
    print(f"\n{'=' * 84}\n{title}\n{'=' * 84}")


def load_network(data_dir: Path, seed: int):
    cached = data_dir / "network.pkl"
    if cached.exists():
        with open(cached, "rb") as fh:
            net = pickle.load(fh)
        if net.seed == seed:
            print(f"Loaded cached network (seed {seed})")
            return net
    print(f"Generating network (seed {seed})...")
    return generate(seed=seed)


def load_analysis(net, data_dir: Path):
    """Reuse saved analysis if it is present, otherwise recompute.

    Betweenness on this graph is the slow step, so the CSVs written by
    analyze_network.py are worth reusing when they exist.
    """
    cent_path = data_dir / "centrality.csv"
    impact_path = data_dir / "disruption_impact.csv"
    blockade_path = data_dir / "disruption_impact_blockade.csv"

    if all(p.exists() for p in (cent_path, impact_path, blockade_path)):
        print("Reusing saved centrality and disruption results")
        return (
            pd.read_csv(cent_path),
            pd.read_csv(impact_path),
            pd.read_csv(blockade_path),
        )

    print("Computing centrality (this is the slow step)...")
    meta = pd.DataFrame([
        {"node_id": nid, "name": n.name, "tier": n.tier.name,
         "region": str(n.region), "category": str(n.category)}
        for nid, n in net.nodes.items()
    ])
    cent = physical_centrality_table(net.physical).merge(meta, on="node_id", how="left")
    sim = DisruptionSimulator(net)
    print("Simulating single-node failures...")
    impact = sim.rank_single_failures(reroute_freight=True)
    blockade = sim.rank_single_failures(reroute_freight=False)
    return cent, impact, blockade


def run_queries(engine, out_dir: Path, root_node_id: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(SQL_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        params = {"root_node_id": root_node_id} if ":root_node_id" in sql else {}
        with engine.connect() as conn:
            df = pd.read_sql_query(text(sql), conn, params=params)

        target = out_dir / f"{path.stem}.csv"
        df.to_csv(target, index=False)
        print(f"\n  {path.name}  ->  {len(df):,} rows  ->  {target.name}")
        if not df.empty:
            preview = df.head(5).to_string(index=False, max_colwidth=26)
            print("\n".join("      " + line for line in preview.splitlines()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=cfg.RANDOM_SEED)
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--db", default=None,
                    help="SQLAlchemy URL; defaults to SQLite in the data directory")
    args = ap.parse_args()

    args.data.mkdir(parents=True, exist_ok=True)
    url = args.db or f"sqlite:///{(args.data / 'warehouse.db').as_posix()}"
    print(f"Target database: {url.split('@')[-1]}")

    net = load_network(args.data, args.seed)
    centrality, impact, blockade = load_analysis(net, args.data)

    rule("LOADING STAR SCHEMA")
    engine = create_engine_for(url)
    counts = build_warehouse(net, centrality, impact, blockade, engine)
    for table, n in counts.items():
        print(f"  {table:<32} {n:>8,} rows")

    # The blast-radius query needs a subject. Pick the most damaging *upstream*
    # node rather than the most damaging overall: the worst node in the network
    # is usually a board assembler sitting one hop from finished goods, and
    # traversing from there produces a single-level answer that demonstrates
    # nothing. An upstream materials supplier shows the multi-hop propagation
    # the recursive query exists to expose.
    merged = compare_rankings(centrality, impact)
    upstream = merged[merged["tier"].isin(["RAW_MATERIAL", "REFINED_MATERIAL"])]
    root = (upstream if not upstream.empty else merged).iloc[0]
    print(f"\n  blast-radius subject: {root['name']} "
          f"({root['tier']}, {root['category']})")

    rule("RUNNING ANALYTICAL SQL")
    run_queries(engine, args.data / "analytics", str(root["node_id"]))

    rule("DONE")
    print(f"\n  warehouse : {url.split('@')[-1]}")
    print(f"  exports   : {args.data / 'analytics'}")
    print("\n  Point Power BI at either. The CSVs are the reproducible path;")
    print("  the database is the one that looks like production.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
