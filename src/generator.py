"""Builds the synthetic semiconductor supply network.

The generator runs in four passes:

    1. Create firms   -- sample each tier's population, assign a product
                         category, a region, and a capacity, from the
                         distributions in config.py.
    2. Wire the BOM   -- for every firm, walk its bill of materials and pick
                         suppliers for each required input using preferential
                         attachment biased by geography.
    3. Route freight  -- decide which cross-border relationships physically
                         pass through a seaport or air hub.
    4. Project graphs -- turn the relationship list into two different graphs
                         (see below), because the answer to "what is critical"
                         depends on which question you are asking.

Two projections, and why it matters
-----------------------------------
`commercial` contains firms only, with a direct edge supplier -> buyer. It
answers *who depends on whom to do business*.

`physical` inserts hub nodes into routed relationships, so an edge becomes
supplier -> port -> buyer. It answers *what has to keep working for goods to
physically arrive*.

They disagree, and the disagreement is the interesting part. A hub can carry
enormous betweenness in the physical graph while being commercially irrelevant
-- you can reroute freight through another port at a cost in weeks and dollars,
but you cannot reroute a sole-source photoresist supplier at all without a
12-24 month requalification.

There is also a modelling artifact worth being honest about: because a hub is a
single node handling many tier transitions, the physical graph contains paths
like `raw material -> port -> OSAT` that no atom of material actually travels.
The port is a shared facility, not a conveyor belt. That is exactly why the
commercial projection exists alongside it, and why centrality figures from the
physical graph should never be quoted without saying which graph they came
from.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import networkx as nx
import numpy as np

from . import config as cfg
from .schema import (
    Category,
    EdgeKind,
    LogisticsKind,
    Node,
    Region,
    Relationship,
    Tier,
)

REGIONS: tuple[Region, ...] = tuple(Region)
_REGION_INDEX: dict[Region, int] = {r: i for i, r in enumerate(REGIONS)}


def _build_geo_matrix() -> np.ndarray:
    """Pairwise sourcing-affinity multiplier between regions.

    Buying at home is easiest, buying inside your trade bloc is next, buying
    across the world is hardest. This is the second concentrating force in the
    model: preferential attachment decides *how big* hubs get, geography
    decides *where* they form.
    """
    n = len(REGIONS)
    m = np.ones((n, n), dtype=float)
    for i, a in enumerate(REGIONS):
        for j, b in enumerate(REGIONS):
            if a == b:
                m[i, j] = cfg.SAME_REGION_BONUS
            elif any(a in bloc and b in bloc for bloc in cfg.BLOCS):
                m[i, j] = cfg.SAME_BLOC_BONUS
    return m


GEO_MATRIX = _build_geo_matrix()


@dataclass(slots=True)
class _Pool:
    """Every firm that sells one category, held in array form.

    Supplier selection is the hot loop -- roughly nine thousand draws over
    pools of a few hundred candidates each. Keeping capacity, region and the
    running customer count as parallel numpy arrays lets each draw compute its
    weight vector in one vectorised expression instead of a Python loop.
    """

    nodes: list[Node] = field(default_factory=list)
    capacity_w: np.ndarray = field(default_factory=lambda: np.empty(0))
    region_idx: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))
    customers: np.ndarray = field(default_factory=lambda: np.empty(0))

    def finalize(self) -> None:
        capacity = np.array([n.capacity_index for n in self.nodes], dtype=float)
        # Pre-raise to its exponent once rather than on every draw.
        self.capacity_w = np.power(capacity, cfg.CAPACITY_EXP)
        self.region_idx = np.array(
            [_REGION_INDEX[n.region] for n in self.nodes], dtype=int
        )
        self.customers = np.zeros(len(self.nodes), dtype=float)


@dataclass(slots=True)
class GeneratedNetwork:
    """Everything one generator run produces."""

    nodes: dict[str, Node]
    relationships: list[Relationship]
    commercial: nx.DiGraph
    physical: nx.DiGraph
    seed: int

    @property
    def firms(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.tier is not Tier.LOGISTICS]

    @property
    def hubs(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.tier is Tier.LOGISTICS]


class SupplyNetworkGenerator:
    """Generates a supply network from the rules in config.py.

    Reproducible: the same seed always produces the same network. That matters
    more than it sounds -- being able to hand someone a seed and have them
    reproduce a finding exactly is the difference between an analysis and an
    anecdote.
    """

    def __init__(
        self,
        seed: int = cfg.RANDOM_SEED,
        tier_sizes: dict[Tier, int] | None = None,
    ) -> None:
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.tier_sizes = dict(tier_sizes or cfg.TIER_SIZES)
        self.nodes: dict[str, Node] = {}
        self.pools: dict[Category, _Pool] = defaultdict(_Pool)
        self._used_names: set[str] = set()

    # -- public ------------------------------------------------------------

    def build(self) -> GeneratedNetwork:
        self._create_firms()
        self._create_hubs()
        for pool in self.pools.values():
            pool.finalize()

        relationships = self._wire_bom()
        self._route_freight(relationships)

        return GeneratedNetwork(
            nodes=self.nodes,
            relationships=relationships,
            commercial=self._project_commercial(relationships),
            physical=self._project_physical(relationships),
            seed=self.seed,
        )

    # -- pass 1: population ------------------------------------------------

    def _create_firms(self) -> None:
        for tier, count in self.tier_sizes.items():
            if tier is Tier.LOGISTICS:
                continue
            categories = cfg.TIER_CATEGORIES[tier]
            cat_p = self._normalise([cfg.CATEGORY_SHARE[c] for c in categories])
            regions = list(cfg.REGION_WEIGHTS[tier])
            reg_p = self._normalise([cfg.REGION_WEIGHTS[tier][r] for r in regions])

            cat_draw = self.rng.choice(len(categories), size=count, p=cat_p)
            reg_draw = self.rng.choice(len(regions), size=count, p=reg_p)
            # Lognormal firm size: a handful of giants, a long tail of small
            # specialists. This becomes the "fitness" term in attachment, so
            # that being big -- not merely being early -- attracts customers.
            capacity = self.rng.lognormal(
                cfg.CAPACITY_LOG_MEAN, cfg.CAPACITY_LOG_SIGMA, size=count
            )

            for i in range(count):
                category = categories[cat_draw[i]]
                region = regions[reg_draw[i]]
                node = Node(
                    node_id=f"{tier.name}_{i:04d}",
                    name=self._firm_name(tier, region),
                    tier=tier,
                    region=region,
                    category=category,
                    capacity_index=float(capacity[i]),
                )
                self.nodes[node.node_id] = node
                self.pools[category].nodes.append(node)

    def _create_hubs(self) -> None:
        """Place seaports and air hubs at real gateway cities.

        Sampled without replacement from the universe of (region, city, kind)
        so no city appears twice. An earlier version drew cities independently
        and produced "Houston Port" alongside "Houston Port 2", which is both
        ugly and analytically misleading -- two synthetic ports splitting what
        should be one chokepoint understates how concentrated freight really is.
        """
        count = self.tier_sizes[Tier.LOGISTICS]
        weights_by_region = cfg.REGION_WEIGHTS[Tier.LOGISTICS]

        universe: list[tuple[Region, str, LogisticsKind]] = []
        weights: list[float] = []
        for kind, city_map, share in (
            (LogisticsKind.SEAPORT, cfg.SEAPORT_CITIES, cfg.SEAPORT_SHARE),
            (LogisticsKind.AIR_HUB, cfg.AIR_HUB_CITIES, 1.0 - cfg.SEAPORT_SHARE),
        ):
            for region, cities in city_map.items():
                region_w = weights_by_region.get(region, 0.0)
                if not region_w or not cities:
                    continue
                for city in cities:
                    universe.append((region, city, kind))
                    weights.append(region_w * share / len(cities))

        count = min(count, len(universe))
        picks = self.rng.choice(
            len(universe), size=count, replace=False, p=self._normalise(weights)
        )
        capacity = self.rng.lognormal(0.0, 0.9, size=count)

        for i, pick in enumerate(picks):
            region, city, kind = universe[pick]

            # A hub serves its home region plus a couple of neighbours -- most
            # freight through Singapore is not Singaporean -- but only
            # neighbours that make geographic sense.
            extra = int(self.rng.choice(cfg.HUB_EXTRA_REGIONS, p=cfg.HUB_EXTRA_REGION_P))
            served = {region}
            neighbours = cfg.REGION_NEIGHBOURS.get(region, ())
            if extra and neighbours:
                take = min(extra, len(neighbours))
                served.update(
                    neighbours[j]
                    for j in self.rng.choice(len(neighbours), size=take, replace=False)
                )

            node = Node(
                node_id=f"HUB_{i:03d}",
                name=self._unique(f"{city} {cfg.LOGISTICS_SUFFIX[kind]}"),
                tier=Tier.LOGISTICS,
                region=region,
                category=Category.FREIGHT,
                capacity_index=float(capacity[i]),
                logistics_kind=kind,
                serves_regions=frozenset(served),
            )
            self.nodes[node.node_id] = node

    # -- pass 2: wiring ----------------------------------------------------

    def _wire_bom(self) -> list[Relationship]:
        """Walk every firm's bill of materials and pick its suppliers."""
        relationships: list[Relationship] = []

        for node in self.nodes.values():
            recipe = cfg.BOM.get(node.category)
            if not recipe:
                continue  # raw materials and hubs buy nothing inside this model

            for input_category, required_p in recipe:
                if self.rng.random() > required_p:
                    continue  # this firm's process does not use that input
                pool = self.pools.get(input_category)
                if not pool or not pool.nodes:
                    continue

                k = self._supplier_count(input_category)
                chosen = self._choose_suppliers(pool, node, k)
                # Redundancy is per input: with k suppliers on this BOM line,
                # each of them has k-1 alternatives. k == 1 means single-source.
                alternatives = len(chosen) - 1

                for supplier in chosen:
                    relationships.append(
                        self._make_relationship(
                            supplier, node, input_category, alternatives
                        )
                    )
        return relationships

    def _supplier_count(self, category: Category) -> int:
        p_single = cfg.SINGLE_SOURCE_PROB.get(category, cfg.DEFAULT_SINGLE_SOURCE_PROB)
        if self.rng.random() < p_single:
            return 1
        n = 2 + int(self.rng.poisson(cfg.MULTI_SOURCE_LAMBDA))
        return min(n, cfg.MAX_SUPPLIERS_PER_INPUT)

    def _choose_suppliers(self, pool: _Pool, buyer: Node, k: int) -> list[Node]:
        """Sublinear preferential attachment, weighted by firm size and geography.

            weight = (customers + alpha)**gamma * capacity**exp * geo_bonus

        The degree term is what produces a heavy tail: every customer a
        supplier wins makes the next one more likely. Raising it to gamma < 1
        keeps that from running away into a single winner, and stands in for
        the fact that plants have finite capacity -- the hundredth customer is
        harder to win than the tenth. Capacity means size, not merely
        seniority, attracts business. Geography means the winners cluster
        regionally, which is what turns a heavy-tailed degree distribution into
        something that looks like a supply chain rather than a generic
        scale-free graph.
        """
        k = min(k, len(pool.nodes))
        if k <= 0:
            return []

        geo = GEO_MATRIX[pool.region_idx, _REGION_INDEX[buyer.region]]
        weights = (
            np.power(pool.customers + cfg.ATTACH_ALPHA, cfg.ATTACH_GAMMA)
            * pool.capacity_w
            * geo
        )
        total = weights.sum()
        if total <= 0 or not np.isfinite(total):
            return []

        idx = self.rng.choice(
            len(pool.nodes), size=k, replace=False, p=weights / total
        )
        pool.customers[idx] += 1.0
        return [pool.nodes[i] for i in idx]

    def _make_relationship(
        self,
        supplier: Node,
        buyer: Node,
        category: Category,
        alternatives: int,
    ) -> Relationship:
        lo, hi = cfg.LEAD_TIME_DAYS.get(
            (supplier.tier, buyer.tier), cfg.DEFAULT_LEAD_TIME
        )
        lead = int(self.rng.integers(lo, hi + 1))

        # Volume scales with the supplier's capacity: bigger firms ship more
        # per relationship, not just to more customers.
        median = cfg.VOLUME_MEDIAN.get(category, 100_000)
        scale = supplier.capacity_index ** 0.45
        volume = max(1, int(median * scale * self.rng.lognormal(0, cfg.VOLUME_LOG_SIGMA)))
        cost = cfg.UNIT_COST_USD.get(category, 10.0) * float(
            self.rng.lognormal(0, 0.22)
        )

        return Relationship(
            source=supplier.node_id,
            target=buyer.node_id,
            category=category,
            lead_time_days=lead,
            annual_volume_units=volume,
            unit_cost_usd=cost,
            qualified_alternatives=alternatives,
        )

    # -- pass 3: freight routing -------------------------------------------

    def _route_freight(self, relationships: list[Relationship]) -> None:
        """Decide which cross-border shipments pass through a modelled hub."""
        hubs = [n for n in self.nodes.values() if n.tier is Tier.LOGISTICS]
        by_region: dict[Region, list[Node]] = defaultdict(list)
        for hub in hubs:
            for region in hub.serves_regions:
                by_region[region].append(hub)

        # Hubs accrue traffic preferentially too: busy ports attract more
        # sailings, more customs capacity, more forwarders. Same rich-get-
        # richer dynamic, applied to infrastructure.
        usage: dict[str, float] = {hub.node_id: 0.0 for hub in hubs}

        for rel in relationships:
            src = self.nodes[rel.source]
            dst = self.nodes[rel.target]
            if src.region == dst.region:
                continue
            if self.rng.random() > cfg.CROSS_REGION_ROUTE_PROB:
                continue  # direct air freight, no modelled hub

            prefer_air = self.rng.random() < cfg.AIR_FREIGHT_PROB
            candidates = by_region.get(src.region) or hubs
            wanted = LogisticsKind.AIR_HUB if prefer_air else LogisticsKind.SEAPORT
            typed = [h for h in candidates if h.logistics_kind is wanted]
            candidates = typed or candidates

            weights = np.array(
                [(usage[h.node_id] + cfg.ATTACH_ALPHA) * h.capacity_index
                 for h in candidates],
                dtype=float,
            )
            hub = candidates[int(self.rng.choice(len(candidates), p=weights / weights.sum()))]
            usage[hub.node_id] += 1.0

            rel.via_hub = hub.node_id
            transit = (
                cfg.AIR_TRANSIT_DAYS
                if hub.logistics_kind is LogisticsKind.AIR_HUB
                else cfg.SEA_TRANSIT_DAYS
            )
            rel.lead_time_days += int(self.rng.integers(transit[0], transit[1] + 1))

    # -- pass 4: projections -----------------------------------------------

    def _project_commercial(self, relationships: list[Relationship]) -> nx.DiGraph:
        """Firms only, direct supplier -> buyer edges.

        Multiple BOM lines between the same pair collapse into one edge, so the
        edge carries aggregate economics plus the *tightest* redundancy
        constraint across those lines. Taking the minimum matters: a supplier
        selling you two well-covered inputs and one sole-source input is a
        sole-source dependency, and averaging would hide that.
        """
        g = nx.DiGraph(projection="commercial", seed=self.seed)
        for node in self.nodes.values():
            if node.tier is not Tier.LOGISTICS:
                g.add_node(node.node_id, **node.to_attrs())

        lanes: dict[tuple[str, str], dict] = {}
        for rel in relationships:
            self._accumulate(lanes, rel.source, rel.target, rel, EdgeKind.SUPPLIES)
        self._write_lanes(g, lanes)
        return g

    def _project_physical(self, relationships: list[Relationship]) -> nx.DiGraph:
        """Firms plus hubs, with routed shipments passing through the hub.

        A hub is split into one *gate* per tier transition it handles, so
        "Kaohsiung Port" becomes `HUB_004::FAB>OSAT`, `HUB_004::EMS>OEM` and so
        on. Every gate carries `parent_hub`, so closing the port means removing
        all of its gates together.

        This exists because collapsing a port into a single node breaks the
        graph. A port that receives from an EMS firm and also ships to a
        materials firm creates an edge sequence running *backwards* up the
        chain, and the first version of this generator produced a physical
        graph that was not acyclic at all -- topological order was undefined,
        and any path-based measure was counting routes along which no material
        could ever travel. Splitting by transition keeps the port a single
        point of failure while keeping the graph a DAG.
        """
        g = nx.DiGraph(projection="physical", seed=self.seed)
        for node in self.nodes.values():
            if node.tier is not Tier.LOGISTICS:
                g.add_node(node.node_id, parent_hub="", transition="", **node.to_attrs())

        lanes: dict[tuple[str, str], dict] = {}
        for rel in relationships:
            if rel.via_hub is None:
                self._accumulate(lanes, rel.source, rel.target, rel, EdgeKind.SUPPLIES)
                continue

            src_tier = self.nodes[rel.source].tier
            dst_tier = self.nodes[rel.target].tier
            transition = f"{src_tier.name}>{dst_tier.name}"
            gate = f"{rel.via_hub}::{transition}"
            if gate not in g:
                hub = self.nodes[rel.via_hub]
                g.add_node(
                    gate,
                    parent_hub=hub.node_id,
                    transition=transition,
                    **hub.to_attrs(),
                )
            self._accumulate(lanes, rel.source, gate, rel, EdgeKind.SHIPS_VIA)
            self._accumulate(lanes, gate, rel.target, rel, EdgeKind.SHIPS_VIA)

        self._write_lanes(g, lanes)
        return g

    @staticmethod
    def _accumulate(
        lanes: dict[tuple[str, str], dict],
        u: str,
        v: str,
        rel: Relationship,
        kind: EdgeKind,
    ) -> None:
        lane = lanes.get((u, v))
        if lane is None:
            lanes[(u, v)] = {
                "kind": str(kind),
                "categories": {str(rel.category)},
                "n_relationships": 1,
                "annual_volume_units": rel.annual_volume_units,
                "annual_value_usd": rel.annual_value_usd,
                "lead_time_days": rel.lead_time_days,
                "min_qualified_alternatives": rel.qualified_alternatives,
            }
            return
        lane["categories"].add(str(rel.category))
        lane["n_relationships"] += 1
        lane["annual_volume_units"] += rel.annual_volume_units
        lane["annual_value_usd"] += rel.annual_value_usd
        lane["lead_time_days"] = max(lane["lead_time_days"], rel.lead_time_days)
        lane["min_qualified_alternatives"] = min(
            lane["min_qualified_alternatives"], rel.qualified_alternatives
        )

    @staticmethod
    def _write_lanes(g: nx.DiGraph, lanes: dict[tuple[str, str], dict]) -> None:
        for (u, v), lane in lanes.items():
            g.add_edge(
                u,
                v,
                kind=lane["kind"],
                categories=",".join(sorted(lane["categories"])),
                n_relationships=lane["n_relationships"],
                annual_volume_units=int(lane["annual_volume_units"]),
                annual_value_usd=round(lane["annual_value_usd"], 2),
                lead_time_days=int(lane["lead_time_days"]),
                min_qualified_alternatives=int(lane["min_qualified_alternatives"]),
                is_single_source=int(lane["min_qualified_alternatives"] == 0),
                # Traversal cost for shortest-path work: slow lanes are "long".
                weight=float(lane["lead_time_days"]),
            )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _normalise(values: list[float]) -> np.ndarray:
        arr = np.array(values, dtype=float)
        return arr / arr.sum()

    def _firm_name(self, tier: Tier, region: Region) -> str:
        stem = cfg.NAME_STEMS[self.rng.integers(len(cfg.NAME_STEMS))]
        suffix_options = cfg.TIER_SUFFIX[tier]
        suffix = suffix_options[self.rng.integers(len(suffix_options))]
        cities = cfg.CITIES[region]
        city = cities[self.rng.integers(len(cities))]
        return self._unique(f"{stem} {suffix} - {city}")

    def _unique(self, name: str) -> str:
        if name not in self._used_names:
            self._used_names.add(name)
            return name
        n = 2
        while f"{name} {n}" in self._used_names:
            n += 1
        final = f"{name} {n}"
        self._used_names.add(final)
        return final


def generate(
    seed: int = cfg.RANDOM_SEED,
    tier_sizes: dict[Tier, int] | None = None,
) -> GeneratedNetwork:
    """Convenience entry point."""
    return SupplyNetworkGenerator(seed=seed, tier_sizes=tier_sizes).build()


__all__ = ["GeneratedNetwork", "SupplyNetworkGenerator", "generate"]
