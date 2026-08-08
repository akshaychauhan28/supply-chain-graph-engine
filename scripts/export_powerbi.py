"""Export the warehouse as a Power BI-ready star schema.

    python scripts/export_powerbi.py

Writes data/powerbi/*.csv -- one file per table, ready to import as a model
with working relationships, slicers and drill-through.

Why not just point Power BI at data/analytics/
----------------------------------------------
Those files are query *results* -- pre-aggregated top-30 lists. Importing them
gives seven disconnected tables: a slicer on one visual filters nothing else,
no visual can drill past the thirty rows that survived the LIMIT, and the tool
ends up rendering static tables it cannot interact with. That is a report
rebuilt inside a modelling tool.

A star schema gives Power BI what it is designed for: filter context flowing
from dimensions into facts, so one region slicer moves every visual on every
page at once.

Role-playing dimensions
-----------------------
fact_supply_relationship references dim_node twice -- once as supplier, once as
buyer. Power BI allows only ONE active relationship between a given pair of
tables, so the second would sit inactive and need USERELATIONSHIP in every
measure that touched it.

The standard fix is a role-playing dimension: two physical copies of the same
dimension, one per role, with prefixed column names so "Supplier Region" and
"Buyer Region" can appear on the same visual without ambiguity. Both are
written out here so the model can be built without any DAX gymnastics.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.warehouse import create_engine_for   # noqa: E402

# Tables lifted straight from the warehouse.
DIRECT_TABLES = (
    "dim_tier",
    "dim_region",
    "dim_category",
    "dim_node",
    "fact_node_criticality",
    "fact_supply_relationship",
)


def tier_label(raw: str) -> str:
    """RAW_MATERIAL -> Raw Material. Enum names do not belong on an axis."""
    special = {"OSAT": "OSAT", "EMS": "EMS", "OEM": "OEM", "FAB": "Fab"}
    return special.get(raw, raw.replace("_", " ").title())


def export(engine, out_dir: Path) -> dict[str, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}

    with engine.connect() as conn:
        frames = {
            name: pd.read_sql_query(text(f"SELECT * FROM {name}"), conn)
            for name in DIRECT_TABLES
        }

    frames["dim_tier"]["tier_label"] = frames["dim_tier"]["tier_name"].map(tier_label)

    # SQLite has no boolean type, so every flag arrives as 0/1 -- which Power BI
    # will cheerfully SUM into a meaningless total, and which forces every
    # measure to read `= 1` instead of `= TRUE()`. Paired text labels make the
    # slicers self-explanatory; the numeric column stays for the measures.
    rel = frames["fact_supply_relationship"]
    rel["sourcing_type"] = rel["is_single_source"].map(
        {1: "Sole-sourced", 0: "Multi-sourced"}
    )
    rel["is_routed_freight"] = rel["via_hub_node_id"].notna().map(
        {True: "Crosses a hub", False: "Direct"}
    )

    # A node's tier, region and category are what every visual slices by, so
    # resolve them once here rather than making the report author build three
    # relationship hops to read a tier name.
    nodes = frames["dim_node"].merge(
        frames["dim_tier"][["tier_id", "tier_name", "tier_label", "flow_order"]],
        on="tier_id", how="left",
    ).merge(
        frames["dim_region"][["region_id", "trade_bloc"]], on="region_id", how="left"
    )
    frames["dim_node"] = nodes

    # Role-playing copies. Prefixing every column keeps "Supplier Region" and
    # "Buyer Region" unambiguous when both land on the same visual.
    for role in ("supplier", "buyer"):
        copy = nodes.copy()
        copy.columns = [
            f"{role}_{c}" if c != "node_id" else f"{role}_node_id" for c in copy.columns
        ]
        frames[f"dim_{role}"] = copy

    # One flat, fully-labelled row per node. This is the table the risk
    # register and the centrality-versus-impact scatter both read from, and
    # having it pre-joined keeps those visuals to a single table each.
    criticality = frames["fact_node_criticality"].merge(
        nodes[["node_id", "node_name", "tier_name", "tier_label", "flow_order",
               "region_id", "trade_bloc", "category_id", "is_logistics_hub"]],
        on="node_id", how="left",
    )
    # Power BI reads a boolean column far more reliably as an explicit label
    # than as 0/1, which it will otherwise happily aggregate with SUM.
    #
    # The .astype(bool) is load-bearing. SQLite hands this column back as
    # int64, and pandas 3 no longer resolves a dict key of True against an
    # integer 1 the way earlier versions did -- a {True: ..., False: ...} map
    # against this column returns all-null, silently, with no warning. Casting
    # first makes the key types match whatever the source dialect produced.
    criticality["node_kind"] = criticality["is_logistics_hub"].astype(bool).map(
        {True: "Port or air hub", False: "Firm"}
    )
    criticality["breaks_something"] = (
        criticality["failed_firms"].fillna(0) > 0
    ).map({True: "Causes failure", False: "Replaceable"})

    # Impact columns stay BLANK for OEMs rather than zero. Nothing sits
    # downstream of a finished good, so it was never simulated -- and "not
    # applicable" is a different statement from "measured, found harmless".
    # Filling with zero would drag every tier average down and make finished
    # goods look like the safest thing in the network.
    frames["fact_node_criticality"] = criticality

    for name, df in frames.items():
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        written[name] = len(df)
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()

    url = args.db or f"sqlite:///{(args.data / 'warehouse.db').as_posix()}"
    db_file = args.data / "warehouse.db"
    if args.db is None and not db_file.exists():
        print(f"No warehouse at {db_file} -- run scripts/build_warehouse.py first")
        return 1

    out_dir = args.data / "powerbi"
    counts = export(create_engine_for(url), out_dir)

    print(f"Wrote {len(counts)} tables to {out_dir}\n")
    for name, n in counts.items():
        kind = "fact" if name.startswith("fact") else "dim "
        print(f"  {kind}  {name:<28} {n:>7,} rows")

    print("\nImport all of them, then build these relationships:")
    print("  dim_supplier[supplier_node_id]  1 -> *  fact_supply_relationship[supplier_node_id]")
    print("  dim_buyer[buyer_node_id]        1 -> *  fact_supply_relationship[buyer_node_id]")
    print("  dim_category[category_id]       1 -> *  fact_supply_relationship[category_id]")
    print("  dim_node[node_id]               1 -> *  fact_node_criticality[node_id]")
    print("\nSet cross-filter direction to Single on all four.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
