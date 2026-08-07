"""Integrity tests for the star schema and the analytical SQL.

A warehouse fails quietly. A broken foreign key or a dropped row does not
raise -- it just makes a dashboard show a slightly smaller number than the
truth, and nobody notices because nobody has the truth to compare against.
These tests are the comparison.

Every query in sql/ is also executed against a real database here, because a
query file that nobody runs is a query file that does not work. Several of
these caught issues during the build, including a bind parameter written inside
a SQL comment that SQLAlchemy dutifully turned into a placeholder the database
never saw.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis import DisruptionSimulator, physical_centrality_table   # noqa: E402
from src.generator import generate                                        # noqa: E402
from src.schema import Tier                                               # noqa: E402
from src.warehouse import build_warehouse, create_engine_for              # noqa: E402

SQL_DIR = ROOT / "sql"

SMALL = {
    Tier.RAW_MATERIAL: 90, Tier.REFINED_MATERIAL: 70, Tier.EQUIPMENT: 25,
    Tier.FAB: 18, Tier.OSAT: 30, Tier.EMS: 80, Tier.OEM: 110,
    Tier.LOGISTICS: 12,
}


@pytest.fixture(scope="module")
def loaded():
    """A small network loaded into an in-memory database."""
    net = generate(seed=4242, tier_sizes=SMALL)
    meta = pd.DataFrame([
        {"node_id": nid, "name": n.name, "tier": n.tier.name,
         "region": str(n.region), "category": str(n.category)}
        for nid, n in net.nodes.items()
    ])
    cent = physical_centrality_table(net.physical).merge(meta, on="node_id", how="left")
    sim = DisruptionSimulator(net)
    impact = sim.rank_single_failures(reroute_freight=True)
    blockade = sim.rank_single_failures(reroute_freight=False)

    engine = create_engine_for("sqlite://")
    counts = build_warehouse(net, cent, impact, blockade, engine)
    return net, engine, counts


def q(engine, sql: str, **params) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql_query(text(sql), conn, params=params or None)


# -- completeness ----------------------------------------------------------

def test_every_node_and_relationship_is_loaded(loaded):
    net, _, counts = loaded
    assert counts["dim_node"] == len(net.nodes)
    assert counts["fact_supply_relationship"] == len(net.relationships)
    assert counts["fact_node_criticality"] == len(net.nodes)


def test_criticality_covers_every_node_including_oems(loaded):
    """OEMs have no simulated impact, but they must still have a row.

    An inner join from the impact table would drop them silently, and every
    dashboard grouping by tier would then be missing its largest tier.
    """
    net, engine, _ = loaded
    df = q(engine, """
        SELECT t.tier_name, COUNT(*) AS n
        FROM fact_node_criticality c
        JOIN dim_node n ON n.node_id = c.node_id
        JOIN dim_tier t ON t.tier_id = n.tier_id
        GROUP BY t.tier_name
    """)
    assert set(df["tier_name"]) == {t.name for t in Tier}
    oem_rows = int(df.loc[df["tier_name"] == "OEM", "n"].iloc[0])
    assert oem_rows == SMALL[Tier.OEM]


# -- referential integrity -------------------------------------------------

@pytest.mark.parametrize("column", ["supplier_node_id", "buyer_node_id"])
def test_relationship_endpoints_exist(loaded, column):
    _, engine, _ = loaded
    orphans = q(engine, f"""
        SELECT COUNT(*) AS n
        FROM fact_supply_relationship s
        LEFT JOIN dim_node n ON n.node_id = s.{column}
        WHERE n.node_id IS NULL
    """)
    assert int(orphans["n"].iloc[0]) == 0


def test_every_fact_category_exists_in_the_dimension(loaded):
    _, engine, _ = loaded
    orphans = q(engine, """
        SELECT COUNT(*) AS n
        FROM fact_supply_relationship s
        LEFT JOIN dim_category c ON c.category_id = s.category_id
        WHERE c.category_id IS NULL
    """)
    assert int(orphans["n"].iloc[0]) == 0


def test_routed_shipments_point_at_actual_hubs(loaded):
    _, engine, _ = loaded
    bad = q(engine, """
        SELECT COUNT(*) AS n
        FROM fact_supply_relationship s
        JOIN dim_node h ON h.node_id = s.via_hub_node_id
        WHERE s.via_hub_node_id IS NOT NULL
          AND h.is_logistics_hub = 0
    """)
    assert int(bad["n"].iloc[0]) == 0


def test_material_only_flows_downstream(loaded):
    """The tier ordering has to survive the trip into SQL."""
    _, engine, _ = loaded
    backwards = q(engine, """
        SELECT COUNT(*) AS n
        FROM fact_supply_relationship s
        JOIN dim_node sup ON sup.node_id = s.supplier_node_id
        JOIN dim_node buy ON buy.node_id = s.buyer_node_id
        WHERE sup.tier_id >= buy.tier_id
    """)
    assert int(backwards["n"].iloc[0]) == 0


# -- semantic consistency --------------------------------------------------

def test_single_source_flag_agrees_with_alternatives(loaded):
    _, engine, _ = loaded
    mismatch = q(engine, """
        SELECT COUNT(*) AS n
        FROM fact_supply_relationship
        WHERE (qualified_alternatives = 0) <> (is_single_source = TRUE)
    """)
    assert int(mismatch["n"].iloc[0]) == 0


def test_annual_value_is_volume_times_unit_cost(loaded):
    _, engine, _ = loaded
    bad = q(engine, """
        SELECT COUNT(*) AS n
        FROM fact_supply_relationship
        WHERE ABS(annual_value_usd - (annual_volume_units * unit_cost_usd))
              > 0.01 * annual_value_usd
    """)
    assert int(bad["n"].iloc[0]) == 0


def test_blockade_damage_is_never_less_than_rerouted(loaded):
    """Freight substitution can only help, in the warehouse as in the model."""
    _, engine, _ = loaded
    bad = q(engine, """
        SELECT COUNT(*) AS n
        FROM fact_node_criticality
        WHERE blockade_value_at_risk IS NOT NULL
          AND production_value_at_risk IS NOT NULL
          AND blockade_value_at_risk < production_value_at_risk - 0.01
    """)
    assert int(bad["n"].iloc[0]) == 0


def test_bom_line_grain_is_preserved(loaded):
    """A buyer has at most one BOM line per input, which is what lets the
    exposure query count lines with COUNT(DISTINCT buyer)."""
    _, engine, _ = loaded
    df = q(engine, """
        SELECT buyer_node_id, category_id, COUNT(DISTINCT qualified_alternatives) AS variants
        FROM fact_supply_relationship
        GROUP BY buyer_node_id, category_id
        HAVING COUNT(DISTINCT qualified_alternatives) > 1
    """)
    assert df.empty, "suppliers on one BOM line disagree about redundancy"


# -- the analytical queries ------------------------------------------------

@pytest.mark.parametrize("path", sorted(SQL_DIR.glob("*.sql")), ids=lambda p: p.stem)
def test_every_query_runs_and_returns_rows(loaded, path):
    net, engine, _ = loaded
    sql = path.read_text(encoding="utf-8")
    params = {}
    if ":root_node_id" in sql:
        upstream = next(
            nid for nid, n in net.nodes.items() if n.tier is Tier.RAW_MATERIAL
        )
        params = {"root_node_id": upstream}
    df = q(engine, sql, **params)
    assert not df.empty, f"{path.name} returned nothing"


def test_blast_radius_reaches_further_than_the_actual_cascade(loaded):
    """Potential exposure must exceed measured failure -- that gap is redundancy.

    If these ever matched, it would mean no buyer anywhere had a second source,
    and the whole premise of the fragility analysis would be gone.
    """
    net, engine, _ = loaded
    sql = (SQL_DIR / "07_blast_radius.sql").read_text(encoding="utf-8")
    target = max(
        (nid for nid, n in net.nodes.items() if n.tier is Tier.REFINED_MATERIAL),
        key=lambda nid: sum(1 for r in net.relationships if r.source == nid),
    )
    radius = q(engine, sql, root_node_id=target)
    exposed = int(radius["firms_exposed"].sum())

    actual = q(engine, """
        SELECT failed_firms FROM fact_node_criticality WHERE node_id = :nid
    """, nid=target)
    failed = int(actual["failed_firms"].iloc[0])

    assert exposed > 0
    assert failed <= exposed
