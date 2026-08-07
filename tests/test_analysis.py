"""Tests for the disruption simulator.

The cascade is the one piece of this project whose output nobody can sanity
check by eye. A centrality number that is wrong looks obviously wrong next to
the graph; a cascade that silently under-propagates just reports smaller
numbers and looks entirely reasonable. So it is tested against hand-built
networks small enough to reason about completely.

Each fixture below is a supply chain you can trace on paper in ten seconds,
and the assertions are the answers worked out by hand.
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analysis import DisruptionSimulator          # noqa: E402
from src.generator import GeneratedNetwork            # noqa: E402
from src.schema import (                              # noqa: E402
    Category,
    LogisticsKind,
    Node,
    Region,
    Relationship,
    Tier,
)


def firm(node_id, tier, category, region=Region.TAIWAN) -> Node:
    return Node(
        node_id=node_id, name=node_id, tier=tier, region=region,
        category=category, capacity_index=1.0,
    )


def hub(node_id, serves, region=Region.TAIWAN) -> Node:
    return Node(
        node_id=node_id, name=node_id, tier=Tier.LOGISTICS, region=region,
        category=Category.FREIGHT, capacity_index=1.0,
        logistics_kind=LogisticsKind.SEAPORT, serves_regions=frozenset(serves),
    )


def rel(src, dst, category, alternatives=0, via=None, value=1_000_000.0) -> Relationship:
    return Relationship(
        source=src, target=dst, category=category, lead_time_days=30,
        annual_volume_units=1000, unit_cost_usd=value / 1000,
        qualified_alternatives=alternatives, via_hub=via,
    )


def build(nodes, relationships) -> GeneratedNetwork:
    """Minimal network. The simulator reads nodes and relationships only."""
    return GeneratedNetwork(
        nodes={n.node_id: n for n in nodes},
        relationships=relationships,
        commercial=nx.DiGraph(),
        physical=nx.DiGraph(),
        seed=0,
    )


# -- a straight chain ------------------------------------------------------

@pytest.fixture
def chain():
    """RAW -> REFINED -> FAB -> OSAT -> EMS -> OEM, single-sourced throughout."""
    nodes = [
        firm("RAW", Tier.RAW_MATERIAL, Category.SILICON_FEEDSTOCK),
        firm("MAT", Tier.REFINED_MATERIAL, Category.WAFER),
        firm("FAB", Tier.FAB, Category.LOGIC_DIE),
        firm("OSAT", Tier.OSAT, Category.LOGIC_IC),
        firm("EMS", Tier.EMS, Category.PCB_ASSEMBLY),
        firm("OEM", Tier.OEM, Category.SMARTPHONE),
    ]
    rels = [
        rel("RAW", "MAT", Category.SILICON_FEEDSTOCK),
        rel("MAT", "FAB", Category.WAFER),
        rel("FAB", "OSAT", Category.LOGIC_DIE),
        rel("OSAT", "EMS", Category.LOGIC_IC),
        rel("EMS", "OEM", Category.PCB_ASSEMBLY),
    ]
    return build(nodes, rels)


def test_single_source_failure_cascades_the_whole_chain(chain):
    sim = DisruptionSimulator(chain)
    res = sim.simulate("RAW")
    assert res.failed_firms == 5          # MAT, FAB, OSAT, EMS, OEM
    assert res.failed_oems == 1
    assert res.share_of_oem_value == pytest.approx(1.0)
    assert res.deepest_cascade == 5


def test_failure_partway_down_takes_only_what_is_below(chain):
    sim = DisruptionSimulator(chain)
    res = sim.simulate("OSAT")
    assert res.failed_firms == 2          # EMS, OEM -- RAW/MAT/FAB keep running
    assert res.failed_oems == 1


def test_removing_the_last_node_breaks_nothing(chain):
    sim = DisruptionSimulator(chain)
    res = sim.simulate("OEM")
    assert res.failed_firms == 0
    assert res.production_value_at_risk == 0.0


def test_removed_node_is_not_counted_as_a_casualty(chain):
    """The question is what the failure *costs*, not that it happened."""
    sim = DisruptionSimulator(chain)
    assert sim.simulate("MAT").failed_firms == 4   # FAB, OSAT, EMS, OEM


# -- redundancy ------------------------------------------------------------

def test_a_second_qualified_supplier_absorbs_the_failure():
    nodes = [
        firm("RAW_A", Tier.RAW_MATERIAL, Category.SILICON_FEEDSTOCK),
        firm("RAW_B", Tier.RAW_MATERIAL, Category.SILICON_FEEDSTOCK),
        firm("MAT", Tier.REFINED_MATERIAL, Category.WAFER),
        firm("FAB", Tier.FAB, Category.LOGIC_DIE),
    ]
    rels = [
        rel("RAW_A", "MAT", Category.SILICON_FEEDSTOCK, alternatives=1),
        rel("RAW_B", "MAT", Category.SILICON_FEEDSTOCK, alternatives=1),
        rel("MAT", "FAB", Category.WAFER),
    ]
    sim = DisruptionSimulator(build(nodes, rels))
    assert sim.simulate("RAW_A").failed_firms == 0
    # Losing both is a different story.
    assert sim.simulate(["RAW_A", "RAW_B"]).failed_firms == 2


def test_redundancy_is_per_input_not_per_supplier_count():
    """The failure this catches is the one spend analysis makes.

    FAB has three suppliers, which looks comfortable. Two of them sell the
    same wafer; the third is the only source of photoresist. Losing the
    photoresist supplier stops the fab, and no count of total suppliers would
    have told you that.
    """
    nodes = [
        firm("WAFER_A", Tier.REFINED_MATERIAL, Category.WAFER),
        firm("WAFER_B", Tier.REFINED_MATERIAL, Category.WAFER),
        firm("RESIST", Tier.REFINED_MATERIAL, Category.PHOTORESIST),
        firm("FAB", Tier.FAB, Category.LOGIC_DIE),
    ]
    rels = [
        rel("WAFER_A", "FAB", Category.WAFER, alternatives=1),
        rel("WAFER_B", "FAB", Category.WAFER, alternatives=1),
        rel("RESIST", "FAB", Category.PHOTORESIST, alternatives=0),
    ]
    sim = DisruptionSimulator(build(nodes, rels))
    assert sim.simulate("WAFER_A").failed_firms == 0
    assert sim.simulate("RESIST").failed_firms == 1


# -- freight ---------------------------------------------------------------

def _freight_network(n_hubs: int):
    """Supplier in Japan shipping to a Taiwanese buyer through Japanese hubs."""
    nodes = [
        firm("MAT", Tier.REFINED_MATERIAL, Category.WAFER, region=Region.JAPAN),
        firm("FAB", Tier.FAB, Category.LOGIC_DIE, region=Region.TAIWAN),
    ]
    nodes += [
        hub(f"HUB_{i}", serves={Region.JAPAN}, region=Region.JAPAN)
        for i in range(n_hubs)
    ]
    rels = [rel("MAT", "FAB", Category.WAFER, via="HUB_0")]
    return build(nodes, rels)


def test_port_closure_reroutes_when_another_gateway_serves_the_region():
    sim = DisruptionSimulator(_freight_network(n_hubs=2))
    assert sim.simulate("HUB_0", reroute_freight=True).failed_firms == 0


def test_port_closure_severs_supply_when_it_is_the_only_gateway():
    sim = DisruptionSimulator(_freight_network(n_hubs=1))
    assert sim.simulate("HUB_0", reroute_freight=True).failed_firms == 1


def test_blockade_mode_ignores_alternative_gateways():
    """The stress case: the assigned route is gone and nothing substitutes."""
    sim = DisruptionSimulator(_freight_network(n_hubs=2))
    assert sim.simulate("HUB_0", reroute_freight=False).failed_firms == 1


def test_losing_every_gateway_stops_the_shipment():
    sim = DisruptionSimulator(_freight_network(n_hubs=3))
    assert sim.simulate(["HUB_0", "HUB_1", "HUB_2"]).failed_firms == 1


# -- sweep -----------------------------------------------------------------

def test_sweep_ranks_by_measured_damage(chain):
    sim = DisruptionSimulator(chain)
    df = sim.rank_single_failures()
    assert "OEM" not in set(df["node_id"]), "finished goods have nothing downstream"
    assert df.iloc[0]["node_id"] == "RAW"
    assert list(df["share_of_oem_value"]) == sorted(
        df["share_of_oem_value"], reverse=True
    )


def test_rerouting_never_reports_more_damage_than_a_blockade():
    """Freight substitution can only ever help. A mode that made things worse
    would mean the two paths through the simulator had diverged."""
    from src.generator import generate
    net = generate(seed=99, tier_sizes={
        Tier.RAW_MATERIAL: 60, Tier.REFINED_MATERIAL: 45, Tier.EQUIPMENT: 20,
        Tier.FAB: 12, Tier.OSAT: 20, Tier.EMS: 50, Tier.OEM: 70,
        Tier.LOGISTICS: 10,
    })
    sim = DisruptionSimulator(net)
    reroute = sim.rank_single_failures(reroute_freight=True).set_index("node_id")
    blockade = sim.rank_single_failures(reroute_freight=False).set_index("node_id")
    joined = reroute.join(blockade, rsuffix="_blockade")
    assert (joined["failed_firms"] <= joined["failed_firms_blockade"]).all()
