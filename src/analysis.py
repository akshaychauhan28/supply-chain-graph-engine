"""Finding what is actually critical, two different ways.

Centrality measures are *proxies*. They describe a node's position in a graph
and infer importance from it. That inference is usually right and always cheap,
which is why everyone uses it -- but a proxy is not a measurement, and this
module is built around the difference.

    Centrality asks:  where does this node sit?
    Simulation asks:  what breaks if it stops?

The second question is the one procurement actually cares about, and the two
answers do not always agree. A supplier can sit on a great many shortest paths
and still be trivially replaceable because every buyer it serves has three
other qualified vendors. Another can look unremarkable on every centrality
measure and take down a fifth of production, because it happens to be the sole
qualified source of one input that everything downstream needs.

So both are computed, and the *disagreement* between them is treated as the
finding rather than as noise.

The failure model
-----------------
Deleting a node and counting what is still reachable is the standard graph
answer and it is wrong for supply chains. Reachability says a fab survives if
any path still reaches it. Manufacturing says a fab survives only if it can
still source *every* input on its bill of materials. Those are different
claims, and only the second one is about production.

So failure here is BOM-aware and cascading:

    A firm fails when any single required input has no surviving qualified
    supplier. Its own customers are then re-checked, and the failure
    propagates downstream until nothing further changes.

That is why single-sourcing dominates the results, and it is why this model
finds things that pure topology does not. Losing one of four wafer suppliers
costs a fab nothing. Losing its only photoresist source stops it completely,
and then stops everyone who depended on that fab.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

from .schema import Tier

# ---------------------------------------------------------------------------
# Centrality
# ---------------------------------------------------------------------------


def centrality_table(
    g: nx.DiGraph,
    betweenness_samples: int | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    """Degree, betweenness and PageRank for every node in a projection.

    **PageRank runs on the reversed graph, and that is deliberate.** In the
    graph as built, edges point the way material flows: supplier -> buyer. Run
    plain PageRank on that and importance accumulates at the OEMs, because
    everything eventually flows to them -- which tells you finished goods
    exist, not what production depends on.

    Dependency runs the other way. Reversing the edges makes PageRank follow
    "who do I rely on", so a node scores highly when many firms depend on it,
    weighted by how much those firms are themselves depended upon. That
    recursive definition is the whole reason to use PageRank here rather than
    just counting customers.

    Betweenness is exact by default. On a graph this size it costs about a
    minute; `betweenness_samples` switches to the sampled estimator when that
    matters, at some cost in rank stability down the list.
    """
    nodes = list(g.nodes())
    out_deg = dict(g.out_degree())
    in_deg = dict(g.in_degree())

    betweenness = nx.betweenness_centrality(
        g, k=betweenness_samples, seed=seed, normalized=True
    )
    # Reversed: score flows toward the things others depend on.
    pagerank = nx.pagerank(g.reverse(copy=True), alpha=0.85)

    rows = []
    for n in nodes:
        attrs = g.nodes[n]
        rows.append({
            "node_id": n,
            "name": attrs.get("name", n),
            "tier": attrs.get("tier_name", ""),
            "region": attrs.get("region", ""),
            "category": attrs.get("category", ""),
            "customers": out_deg[n],
            "suppliers": in_deg[n],
            "betweenness": betweenness[n],
            "pagerank": pagerank[n],
        })
    df = pd.DataFrame(rows)
    for col in ("customers", "betweenness", "pagerank"):
        df[f"rank_{col}"] = df[col].rank(ascending=False, method="min").astype(int)
    return df


def physical_centrality_table(
    g: nx.DiGraph,
    betweenness_samples: int | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    """Centrality on the physical projection, with hub gates folded back together.

    Hubs exist in the physical graph as one gate per tier transition, which is
    what keeps that graph acyclic. For scoring, those gates have to be summed
    back into the facility they belong to -- a port's importance is the traffic
    across all of it, not across whichever transition happens to be busiest.

    This function exists because the comparison was otherwise unfair in a way
    that flattered the argument. Centrality computed on the *commercial*
    projection cannot rank logistics hubs at all, since they are not in that
    graph; scoring it against a simulation that does model them made
    centrality look far blinder than it is. Ports are legitimately visible to
    centrality -- provided you compute it on the graph that contains them.
    """
    betweenness = nx.betweenness_centrality(
        g, k=betweenness_samples, seed=seed, normalized=True
    )
    pagerank = nx.pagerank(g.reverse(copy=True), alpha=0.85)
    out_deg = dict(g.out_degree())
    in_deg = dict(g.in_degree())

    rows = []
    for n, attrs in g.nodes(data=True):
        parent = attrs.get("parent_hub") or n
        rows.append({
            "node_id": parent,
            "customers": out_deg[n],
            "suppliers": in_deg[n],
            "betweenness": betweenness[n],
            "pagerank": pagerank[n],
        })
    df = pd.DataFrame(rows).groupby("node_id", as_index=False).sum()
    for col in ("customers", "betweenness", "pagerank"):
        df[f"rank_{col}"] = df[col].rank(ascending=False, method="min").astype(int)
    return df


# ---------------------------------------------------------------------------
# Disruption simulation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DisruptionResult:
    """What happened when a set of nodes stopped shipping."""

    removed: tuple[str, ...]
    failed_firms: int            # excludes the removed nodes themselves
    failed_oems: int
    production_value_at_risk: float
    share_of_oem_value: float
    deepest_cascade: int         # how many propagation rounds it took

    @property
    def caused_cascade(self) -> bool:
        """Whether failure spread beyond the removed node's direct customers."""
        return self.deepest_cascade > 1


class DisruptionSimulator:
    """Removes nodes and measures what stops producing.

    Everything is indexed into flat integer arrays up front. The naive version
    -- rebuilding a graph and recomputing reachability per candidate -- is
    around three thousand full traversals and takes minutes. Propagating from
    the removed node along a precomputed customer index touches only the part
    of the network that can actually be affected, which for most nodes is a
    handful of firms.
    """

    def __init__(self, net) -> None:
        self.net = net
        self.node_ids: list[str] = list(net.nodes.keys())
        self.index: dict[str, int] = {n: i for i, n in enumerate(self.node_ids)}
        n = len(self.node_ids)

        rels = net.relationships
        self.n_rel = len(rels)
        self.rel_supplier = np.fromiter(
            (self.index[r.source] for r in rels), dtype=np.int32, count=self.n_rel
        )
        self.rel_buyer = np.fromiter(
            (self.index[r.target] for r in rels), dtype=np.int32, count=self.n_rel
        )
        self.rel_value = np.fromiter(
            (r.annual_value_usd for r in rels), dtype=np.float64, count=self.n_rel
        )

        # Requirements: for each buyer, one group of relationship ids per input
        # category. A firm survives only if EVERY group still has a live member.
        groups: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
        for i, r in enumerate(rels):
            groups[self.index[r.target]][str(r.category)].append(i)
        self.requirements: dict[int, list[np.ndarray]] = {
            buyer: [np.array(ids, dtype=np.int32) for ids in by_cat.values()]
            for buyer, by_cat in groups.items()
        }

        # Who to re-check when a node goes down.
        customers: dict[int, set[int]] = defaultdict(set)
        for i, r in enumerate(rels):
            customers[self.index[r.source]].add(self.index[r.target])
            if r.via_hub is not None:
                customers[self.index[r.via_hub]].add(self.index[r.target])
        self.customers: dict[int, np.ndarray] = {
            k: np.fromiter(v, dtype=np.int32, count=len(v)) for k, v in customers.items()
        }

        # Freight routing, and which hubs could carry each shipment.
        #
        # The distinction between a lost supplier and a lost port is the whole
        # reason both are modelled. A sole-source supplier cannot be replaced
        # without 12-24 months of requalification -- that relationship is
        # simply gone. A port can be replaced next week by another port serving
        # the same region, at a cost in weeks and dollars.
        #
        # Collapsing the two makes hubs look apocalyptic: the first version of
        # this simulator had a single air cargo hub halting 63% of all
        # production, purely because freight had nowhere to go. That is a
        # blockade scenario, not a port closure, and conflating them buries
        # every supplier-side finding underneath the logistics layer.
        hub_assigned: dict[int, list[int]] = defaultdict(list)
        hub_eligible: dict[int, list[int]] = defaultdict(list)
        rel_eligible: list[np.ndarray | None] = [None] * self.n_rel

        hubs_by_region: dict[str, list[int]] = defaultdict(list)
        for nid, node in net.nodes.items():
            if node.tier is Tier.LOGISTICS:
                for region in node.serves_regions:
                    hubs_by_region[str(region)].append(self.index[nid])

        for i, r in enumerate(rels):
            if r.via_hub is None:
                continue
            hub_assigned[self.index[r.via_hub]].append(i)
            origin = str(net.nodes[r.source].region)
            eligible = set(hubs_by_region.get(origin, ()))
            eligible.add(self.index[r.via_hub])
            arr = np.fromiter(eligible, dtype=np.int32, count=len(eligible))
            rel_eligible[i] = arr
            for h in arr:
                hub_eligible[int(h)].append(i)

        self.hub_assigned = {k: np.array(v, dtype=np.int32) for k, v in hub_assigned.items()}
        self.hub_eligible = {k: np.array(v, dtype=np.int32) for k, v in hub_eligible.items()}
        self.rel_eligible = rel_eligible

        self.tier = np.array(
            [int(net.nodes[nid].tier) for nid in self.node_ids], dtype=np.int16
        )
        self.is_oem = self.tier == int(Tier.OEM)

        # An OEM's production value is what it spends on inputs -- the cost of
        # goods behind the products it ships. Using inbound value keeps the
        # figure grounded in generated data rather than an invented margin.
        self.oem_value = np.zeros(n, dtype=np.float64)
        np.add.at(self.oem_value, self.rel_buyer, self.rel_value)
        self.oem_value[~self.is_oem] = 0.0
        self.total_oem_value = float(self.oem_value.sum())

    # -- core ---------------------------------------------------------------

    def simulate(
        self,
        removed: str | list[str] | tuple[str, ...],
        reroute_freight: bool = True,
    ) -> DisruptionResult:
        """Remove nodes and propagate the consequences.

        `reroute_freight=True` (the default) models a port or air hub closing
        while the rest of the world keeps operating: shipments move to another
        gateway serving the same origin region, and only shipments with no
        surviving alternative are lost. This is the realistic case, and the one
        to quote.

        `reroute_freight=False` models the gateway being unavailable with no
        substitute -- a regional blockade or closed airspace rather than a
        single closed facility. Worth running as a stress case, but it is a
        much stronger assumption and should never be reported as "what happens
        if this port closes".
        """
        if isinstance(removed, str):
            removed = [removed]
        removed_idx = [self.index[r] for r in removed if r in self.index]

        failed = np.zeros(len(self.node_ids), dtype=bool)
        dead_rel = np.zeros(self.n_rel, dtype=bool)

        queue: deque[tuple[int, int]] = deque()
        for idx in removed_idx:
            failed[idx] = True

        # Sever freight only where no alternative gateway survives.
        for idx in removed_idx:
            if reroute_freight:
                for rel in self.hub_eligible.get(idx, ()):
                    options = self.rel_eligible[rel]
                    if options is not None and failed[options].all():
                        dead_rel[rel] = True
            else:
                for rel in self.hub_assigned.get(idx, ()):
                    dead_rel[rel] = True

        for idx in removed_idx:
            for cust in self.customers.get(idx, ()):
                queue.append((int(cust), 1))

        deepest = 0
        while queue:
            buyer, depth = queue.popleft()
            if failed[buyer]:
                continue
            if not self._still_viable(buyer, failed, dead_rel):
                failed[buyer] = True
                deepest = max(deepest, depth)
                for cust in self.customers.get(buyer, ()):
                    queue.append((int(cust), depth + 1))

        for idx in removed_idx:
            failed[idx] = False   # count consequences, not the removal itself
        failed_oems = int((failed & self.is_oem).sum())
        value = float(self.oem_value[failed & self.is_oem].sum())

        return DisruptionResult(
            removed=tuple(removed),
            failed_firms=int(failed.sum()),
            failed_oems=failed_oems,
            production_value_at_risk=value,
            share_of_oem_value=value / self.total_oem_value if self.total_oem_value else 0.0,
            deepest_cascade=deepest,
        )

    def _still_viable(self, buyer: int, failed: np.ndarray, dead_rel: np.ndarray) -> bool:
        """A firm survives only if every required input still has a live source."""
        for group in self.requirements.get(buyer, ()):
            alive = False
            for rel in group:
                if not dead_rel[rel] and not failed[self.rel_supplier[rel]]:
                    alive = True
                    break
            if not alive:
                return False
        return True

    # -- sweeps -------------------------------------------------------------

    def rank_single_failures(
        self,
        candidates: list[str] | None = None,
        reroute_freight: bool = True,
    ) -> pd.DataFrame:
        """Remove every node in turn and record the damage.

        This is the ground truth the centrality measures get scored against.
        """
        if candidates is None:
            candidates = [
                nid for nid, node in self.net.nodes.items()
                if node.tier is not Tier.OEM   # nothing downstream of a finished good
            ]

        rows = []
        for node_id in candidates:
            node = self.net.nodes[node_id]
            res = self.simulate(node_id, reroute_freight=reroute_freight)
            rows.append({
                "node_id": node_id,
                "name": node.name,
                "tier": node.tier.name,
                "region": str(node.region),
                "category": str(node.category),
                "failed_firms": res.failed_firms,
                "failed_oems": res.failed_oems,
                "production_value_at_risk": res.production_value_at_risk,
                "share_of_oem_value": res.share_of_oem_value,
                "deepest_cascade": res.deepest_cascade,
            })
        df = pd.DataFrame(rows)
        df["rank_impact"] = (
            df["share_of_oem_value"].rank(ascending=False, method="min").astype(int)
        )
        return df.sort_values("share_of_oem_value", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Where the proxy and the measurement disagree
# ---------------------------------------------------------------------------


def compare_rankings(
    centrality: pd.DataFrame, impact: pd.DataFrame, top_n: int = 25
) -> pd.DataFrame:
    """Join centrality ranks to measured impact and score the disagreement.

    `rank_gap` is (best centrality rank) - (impact rank). A large positive gap
    is a node that every centrality measure underrates and that simulation says
    is dangerous -- the interesting direction, because it is exactly what a
    topology-only analysis would have missed.
    """
    merged = impact.merge(
        centrality[["node_id", "rank_customers", "rank_betweenness",
                    "rank_pagerank", "betweenness", "pagerank", "customers"]],
        on="node_id",
        how="left",
    )
    merged["best_centrality_rank"] = merged[
        ["rank_customers", "rank_betweenness", "rank_pagerank"]
    ].min(axis=1)
    merged["rank_gap"] = merged["best_centrality_rank"] - merged["rank_impact"]
    return merged


def rank_correlation(merged: pd.DataFrame, measure: str) -> float:
    """Spearman correlation between a centrality rank and the impact rank."""
    sub = merged[[f"rank_{measure}", "rank_impact"]].dropna()
    if len(sub) < 3:
        return float("nan")
    return float(sub[f"rank_{measure}"].corr(sub["rank_impact"], method="spearman"))


__all__ = [
    "DisruptionResult",
    "DisruptionSimulator",
    "centrality_table",
    "compare_rankings",
    "physical_centrality_table",
    "rank_correlation",
]
