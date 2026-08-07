"""Star schema for the supply network and its analysis results.

Why land a graph in a relational warehouse at all
-------------------------------------------------
Because the graph is the wrong shape for the people who need the answers. A
NetworkX object cannot be joined against a spend report, opened in Power BI, or
queried by someone who does not write Python. The graph is where criticality is
*computed*; the warehouse is where it gets *used*.

This is also how the handoff works in practice. Graph analysis is a specialist
step that runs periodically; the output is a table of node-level risk scores
that everything downstream treats as ordinary dimensional data.

Model
-----
    dim_tier          the seven layers, in flow order
    dim_region        geography plus trade bloc
    dim_category      the 40 product types, their tier and economics
    dim_node          every firm and logistics hub
    fact_supply_relationship   one row per BOM line -- the grain of the network
    fact_node_criticality      one row per node -- centrality and simulated impact

`fact_supply_relationship` is at BOM-line grain (one buyer, one input, one
supplier) rather than one row per supplier pair, and that choice carries the
analysis. Redundancy is a property of an input, not of a relationship: a buyer
with eight suppliers is still one fire away from a line stop if a single input
has one qualified source. Aggregating to the pair would destroy exactly the
information the risk queries need.

Dialect
-------
Written against SQLAlchemy Core so it runs on SQLite with no setup and on
PostgreSQL with a connection string. Nothing here uses a Postgres-only type,
and the analytical SQL in sql/ sticks to standard window functions and
recursive CTEs that both engines support.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    insert,
)
from sqlalchemy.engine import Engine

from . import config as cfg
from .schema import Tier

metadata = MetaData()

dim_tier = Table(
    "dim_tier", metadata,
    Column("tier_id", Integer, primary_key=True),
    Column("tier_name", String(32), nullable=False),
    Column("flow_order", Integer, nullable=False),
    Column("description", String(160)),
)

dim_region = Table(
    "dim_region", metadata,
    Column("region_id", String(32), primary_key=True),
    Column("region_name", String(32), nullable=False),
    Column("trade_bloc", String(32)),
)

dim_category = Table(
    "dim_category", metadata,
    Column("category_id", String(48), primary_key=True),
    Column("tier_id", Integer, ForeignKey("dim_tier.tier_id"), nullable=False),
    Column("median_unit_cost_usd", Float),
    Column("median_annual_volume", Float),
    # The configured difficulty of qualifying a second vendor. Kept in the
    # warehouse so an analyst can see *why* an input is fragile, not just that
    # it is -- lithography is sole-sourced because requalification takes years,
    # and that reason belongs next to the number.
    Column("single_source_probability", Float),
)

dim_node = Table(
    "dim_node", metadata,
    Column("node_id", String(32), primary_key=True),
    Column("node_name", String(96), nullable=False),
    Column("tier_id", Integer, ForeignKey("dim_tier.tier_id"), nullable=False),
    Column("region_id", String(32), ForeignKey("dim_region.region_id"), nullable=False),
    Column("category_id", String(48), ForeignKey("dim_category.category_id")),
    Column("capacity_index", Float),
    Column("logistics_kind", String(16)),
    Column("is_logistics_hub", Boolean, nullable=False),
)

fact_supply_relationship = Table(
    "fact_supply_relationship", metadata,
    Column("relationship_id", Integer, primary_key=True, autoincrement=False),
    Column("supplier_node_id", String(32), ForeignKey("dim_node.node_id"), nullable=False),
    Column("buyer_node_id", String(32), ForeignKey("dim_node.node_id"), nullable=False),
    Column("category_id", String(48), ForeignKey("dim_category.category_id"), nullable=False),
    Column("via_hub_node_id", String(32), ForeignKey("dim_node.node_id")),
    Column("lead_time_days", Integer, nullable=False),
    Column("annual_volume_units", Integer, nullable=False),
    Column("unit_cost_usd", Float, nullable=False),
    Column("annual_value_usd", Float, nullable=False),
    Column("qualified_alternatives", Integer, nullable=False),
    Column("is_single_source", Boolean, nullable=False),
)

fact_node_criticality = Table(
    "fact_node_criticality", metadata,
    Column("node_id", String(32), ForeignKey("dim_node.node_id"), primary_key=True),
    # predicted -- topology only
    Column("customer_count", Integer),
    Column("supplier_count", Integer),
    Column("betweenness", Float),
    Column("pagerank", Float),
    Column("rank_betweenness", Integer),
    Column("rank_pagerank", Integer),
    Column("rank_customers", Integer),
    Column("best_centrality_rank", Integer),
    # measured -- simulated failure, freight reroutes
    Column("failed_firms", Integer),
    Column("failed_oems", Integer),
    Column("production_value_at_risk", Float),
    Column("share_of_oem_value", Float),
    Column("deepest_cascade", Integer),
    Column("rank_impact", Integer),
    # measured -- blockade stress case, freight cannot reroute
    Column("blockade_value_at_risk", Float),
    Column("blockade_share_of_oem_value", Float),
    # predicted minus measured; large positive = centrality underrates it
    Column("rank_gap", Integer),
)

TIER_DESCRIPTIONS: dict[Tier, str] = {
    Tier.RAW_MATERIAL: "Ore, polysilicon feedstock, crude industrial gases",
    Tier.REFINED_MATERIAL: "Wafers, photoresist, process gases, substrates",
    Tier.EQUIPMENT: "Lithography, etch, deposition, metrology, test handlers",
    Tier.FAB: "Wafer fabrication and foundry services",
    Tier.OSAT: "Outsourced assembly, packaging and test",
    Tier.EMS: "Board assembly and electronic manufacturing services",
    Tier.OEM: "Finished goods",
    Tier.LOGISTICS: "Seaports and air cargo hubs",
}


def bloc_for(region) -> str:
    for bloc, label in ((cfg.EAST_ASIA_BLOC, "East Asia"), (cfg.WESTERN_BLOC, "Western")):
        if region in bloc:
            return label
    return "Unaligned"


def create_engine_for(url: str) -> Engine:
    return create_engine(url, future=True)


def build_warehouse(
    net,
    centrality: pd.DataFrame,
    impact: pd.DataFrame,
    blockade: pd.DataFrame,
    engine: Engine,
) -> dict[str, int]:
    """Create the schema and load it. Returns row counts per table."""
    metadata.drop_all(engine)
    metadata.create_all(engine)

    tiers = [
        {"tier_id": int(t), "tier_name": t.name,
         "flow_order": int(t) if t is not Tier.LOGISTICS else 99,
         "description": TIER_DESCRIPTIONS[t]}
        for t in Tier
    ]
    regions = [
        {"region_id": str(r), "region_name": str(r), "trade_bloc": bloc_for(r)}
        for r in cfg.R
    ]
    categories = [
        {"category_id": str(c), "tier_id": int(tier),
         "median_unit_cost_usd": cfg.UNIT_COST_USD.get(c),
         "median_annual_volume": cfg.VOLUME_MEDIAN.get(c),
         "single_source_probability": cfg.SINGLE_SOURCE_PROB.get(
             c, cfg.DEFAULT_SINGLE_SOURCE_PROB)}
        for c, tier in cfg.CATEGORY_TIER.items()
    ]
    nodes = [
        {"node_id": nid, "node_name": n.name, "tier_id": int(n.tier),
         "region_id": str(n.region), "category_id": str(n.category),
         "capacity_index": n.capacity_index,
         "logistics_kind": str(n.logistics_kind) if n.logistics_kind else None,
         "is_logistics_hub": n.tier is Tier.LOGISTICS}
        for nid, n in net.nodes.items()
    ]
    relationships = [
        {"relationship_id": i,
         "supplier_node_id": r.source, "buyer_node_id": r.target,
         "category_id": str(r.category), "via_hub_node_id": r.via_hub,
         "lead_time_days": r.lead_time_days,
         "annual_volume_units": r.annual_volume_units,
         "unit_cost_usd": r.unit_cost_usd,
         "annual_value_usd": r.annual_value_usd,
         "qualified_alternatives": r.qualified_alternatives,
         "is_single_source": r.is_single_source}
        for i, r in enumerate(net.relationships)
    ]

    crit = _criticality_rows(net, centrality, impact, blockade)

    counts = {}
    with engine.begin() as conn:
        for table, rows in (
            (dim_tier, tiers),
            (dim_region, regions),
            (dim_category, categories),
            (dim_node, nodes),
            (fact_supply_relationship, relationships),
            (fact_node_criticality, crit),
        ):
            if rows:
                conn.execute(insert(table), rows)
            counts[table.name] = len(rows)
    return counts


def _criticality_rows(net, centrality, impact, blockade) -> list[dict]:
    """Join predicted and measured criticality onto one row per node.

    Left-joined from the node list rather than inner-joined from either input,
    so nodes missing from one side survive with nulls instead of vanishing.
    OEMs have no simulated impact -- nothing sits downstream of a finished
    good -- and dropping them here would silently shrink the dimension.
    """
    cent = centrality.set_index("node_id")
    imp = impact.set_index("node_id")
    blk = blockade.set_index("node_id")

    def get(frame, node_id, column, default=None):
        if node_id not in frame.index:
            return default
        value = frame.at[node_id, column]
        return None if pd.isna(value) else value

    rows = []
    for node_id in net.nodes:
        best = get(cent, node_id, "rank_betweenness")
        best_ranks = [
            get(cent, node_id, c)
            for c in ("rank_betweenness", "rank_pagerank", "rank_customers")
        ]
        best_ranks = [r for r in best_ranks if r is not None]
        best = int(min(best_ranks)) if best_ranks else None
        rank_impact = get(imp, node_id, "rank_impact")

        rows.append({
            "node_id": node_id,
            "customer_count": _int(get(cent, node_id, "customers")),
            "supplier_count": _int(get(cent, node_id, "suppliers")),
            "betweenness": get(cent, node_id, "betweenness"),
            "pagerank": get(cent, node_id, "pagerank"),
            "rank_betweenness": _int(get(cent, node_id, "rank_betweenness")),
            "rank_pagerank": _int(get(cent, node_id, "rank_pagerank")),
            "rank_customers": _int(get(cent, node_id, "rank_customers")),
            "best_centrality_rank": best,
            "failed_firms": _int(get(imp, node_id, "failed_firms")),
            "failed_oems": _int(get(imp, node_id, "failed_oems")),
            "production_value_at_risk": get(imp, node_id, "production_value_at_risk"),
            "share_of_oem_value": get(imp, node_id, "share_of_oem_value"),
            "deepest_cascade": _int(get(imp, node_id, "deepest_cascade")),
            "rank_impact": _int(rank_impact),
            "blockade_value_at_risk": get(blk, node_id, "production_value_at_risk"),
            "blockade_share_of_oem_value": get(blk, node_id, "share_of_oem_value"),
            "rank_gap": (
                int(best - rank_impact)
                if best is not None and rank_impact is not None else None
            ),
        })
    return rows


def _int(value):
    return None if value is None or pd.isna(value) else int(value)


def load_query(name: str, sql_dir: Path) -> str:
    return (sql_dir / f"{name}.sql").read_text(encoding="utf-8")


__all__ = [
    "build_warehouse",
    "create_engine_for",
    "dim_category",
    "dim_node",
    "dim_region",
    "dim_tier",
    "fact_node_criticality",
    "fact_supply_relationship",
    "load_query",
    "metadata",
]
