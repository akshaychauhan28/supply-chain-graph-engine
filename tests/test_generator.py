"""Invariants the generated network must satisfy.

These are not unit tests in the usual sense -- they are structural assertions
about the synthetic data. That distinction matters: the risk with a generated
dataset is not that a function throws, it is that the data looks fine and is
quietly wrong, so every downstream number is meaningless in a way nobody
notices. Each test here corresponds to something that has to be true for the
analysis to mean anything.

Two of these caught real bugs during the first build:
  * the physical graph was not acyclic, because collapsing a port into one node
    created edges running backwards up the chain;
  * the single-sourcing rate came out at half its configured value, because it
    was being measured per relationship rather than per BOM line.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import networkx as nx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import config as cfg          # noqa: E402
from src.generator import generate     # noqa: E402
from src.schema import Tier            # noqa: E402


@pytest.fixture(scope="module")
def net():
    return generate(seed=cfg.RANDOM_SEED)


# -- configuration ---------------------------------------------------------

def test_config_is_internally_consistent():
    assert cfg.validate_config() == []


def test_every_bom_input_comes_from_upstream():
    for product, recipe in cfg.BOM.items():
        buyer_tier = cfg.CATEGORY_TIER[product]
        for input_cat, _ in recipe:
            assert cfg.CATEGORY_TIER[input_cat] < buyer_tier, (
                f"{product} requires {input_cat} from an equal or downstream tier"
            )


# -- structure -------------------------------------------------------------

def test_commercial_graph_is_acyclic(net):
    assert nx.is_directed_acyclic_graph(net.commercial)


def test_physical_graph_is_acyclic(net):
    """Regression: hubs used to be single nodes, which created cycles.

    A port receiving from an EMS firm and shipping to a materials firm made the
    graph cyclic, so topological order was undefined and every path-based
    measure counted routes no material could travel. Hubs are now split into
    one gate per tier transition.
    """
    assert nx.is_directed_acyclic_graph(net.physical)


def test_no_self_loops(net):
    assert nx.number_of_selfloops(net.commercial) == 0
    assert nx.number_of_selfloops(net.physical) == 0


def test_commercial_graph_excludes_hubs(net):
    tiers = {a["tier_name"] for _, a in net.commercial.nodes(data=True)}
    assert Tier.LOGISTICS.name not in tiers


def test_hub_gates_carry_their_parent(net):
    gates = [
        (n, a) for n, a in net.physical.nodes(data=True)
        if a["tier_name"] == Tier.LOGISTICS.name
    ]
    assert gates, "physical projection should contain hub gates"
    for node_id, attrs in gates:
        assert attrs["parent_hub"], f"{node_id} has no parent hub"
        assert attrs["parent_hub"] in net.nodes
        assert "::" in node_id


def test_edges_respect_tier_order(net):
    """Material only ever flows downstream."""
    for u, v in net.commercial.edges():
        assert net.nodes[u].tier < net.nodes[v].tier


def test_every_relationship_matches_the_buyers_bom(net):
    """A supplier only sells a buyer something the buyer's recipe calls for."""
    for rel in net.relationships:
        buyer = net.nodes[rel.target]
        supplier = net.nodes[rel.source]
        assert supplier.category == rel.category
        recipe = {cat for cat, _ in cfg.BOM[buyer.category]}
        assert rel.category in recipe, (
            f"{buyer.category} does not use {rel.category}"
        )


# -- connectivity ----------------------------------------------------------

def test_all_finished_goods_trace_back_to_raw_materials(net):
    com = net.commercial
    raw = [n for n, a in com.nodes(data=True) if a["tier_name"] == Tier.RAW_MATERIAL.name]
    oem = {n for n, a in com.nodes(data=True) if a["tier_name"] == Tier.OEM.name}
    probe = com.copy()
    probe.add_edges_from(("__source__", n) for n in raw)
    assert oem <= nx.descendants(probe, "__source__")


def test_supply_chain_depth_is_five_hops(net):
    """RAW -> REFINED -> FAB -> OSAT -> EMS -> OEM."""
    assert nx.dag_longest_path_length(net.commercial, weight=None) == 5


# -- distributions match the configured rules ------------------------------

def test_single_sourcing_rate_is_near_configured(net):
    """Measured per BOM line, which is the only measure that means anything."""
    lines = {(r.target, r.category): r.qualified_alternatives for r in net.relationships}
    rate = sum(1 for alt in lines.values() if alt == 0) / len(lines)
    assert 0.15 <= rate <= 0.27, f"single-sourcing rate {rate:.1%} is off target"


def test_lithography_is_the_most_single_sourced_input(net):
    """The hardest input to second-source should behave that way in the data.

    This is a rule-check, not a planted answer: config says lithography is hard
    to requalify, so lithography lines should come out sole-sourced most often.
    Which lithography *supplier* ends up carrying the network is a separate
    question, and one this test deliberately says nothing about.
    """
    lines = {(r.target, r.category): r.qualified_alternatives for r in net.relationships}
    single: Counter = Counter()
    total: Counter = Counter()
    for (_, cat), alt in lines.items():
        total[cat] += 1
        if alt == 0:
            single[cat] += 1
    rates = {c: single[c] / n for c, n in total.items() if n >= 20}
    assert max(rates, key=rates.get) is cfg.C.LITHOGRAPHY


def test_fab_capacity_concentrates_in_taiwan(net):
    fabs = [n for n in net.nodes.values() if n.tier is Tier.FAB]
    counts = Counter(n.region for n in fabs)
    top_region, top_count = counts.most_common(1)[0]
    assert top_region is cfg.R.TAIWAN
    expected = cfg.REGION_WEIGHTS[Tier.FAB][cfg.R.TAIWAN]
    assert abs(top_count / len(fabs) - expected) < 0.10


def test_degree_distribution_is_heavy_tailed(net):
    """Not a power-law test -- that comes in the validation module.

    This only rules out the failure mode where attachment collapses and every
    supplier ends up with roughly the same number of customers, which would
    mean we had generated a random graph with extra steps.
    """
    degrees = sorted((d for _, d in net.commercial.out_degree() if d > 0), reverse=True)
    top_decile = degrees[: max(1, len(degrees) // 10)]
    assert sum(top_decile) / sum(degrees) > 0.35


def test_no_single_supplier_dominates(net):
    """The opposite failure: linear attachment producing one monopolist.

    An early version had a contract manufacturer holding 77% of all OEM
    relationships. Sublinear attachment is what keeps this in check.
    """
    degrees = [d for _, d in net.commercial.out_degree()]
    assert max(degrees) / net.commercial.number_of_edges() < 0.05


# -- reproducibility -------------------------------------------------------

def test_same_seed_reproduces_the_network(net):
    again = generate(seed=cfg.RANDOM_SEED)
    assert nx.utils.graphs_equal(net.commercial, again.commercial)
    assert len(again.relationships) == len(net.relationships)


def test_different_seeds_give_different_networks(net):
    other = generate(seed=cfg.RANDOM_SEED + 1)
    assert not nx.utils.graphs_equal(net.commercial, other.commercial)


# -- economics -------------------------------------------------------------

def test_equipment_has_the_longest_lead_times(net):
    """A new lithography tool is ordered 12-18 months ahead. That has to show."""
    by_pair: dict[tuple[Tier, Tier], list[int]] = {}
    for rel in net.relationships:
        key = (net.nodes[rel.source].tier, net.nodes[rel.target].tier)
        by_pair.setdefault(key, []).append(rel.lead_time_days)
    medians = {k: sorted(v)[len(v) // 2] for k, v in by_pair.items()}
    worst = max(medians, key=medians.get)
    assert worst[0] is Tier.EQUIPMENT


def test_all_relationships_have_positive_economics(net):
    for rel in net.relationships:
        assert rel.annual_volume_units > 0
        assert rel.unit_cost_usd > 0
        assert rel.lead_time_days > 0
        assert rel.qualified_alternatives >= 0
