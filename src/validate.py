"""Statistical validation of the generated topology.

The question this module exists to answer is the one an interviewer will ask
first: *how do you know your synthetic graph has real structure and isn't just
a layered random graph with plausible labels?*

Saying "the top 10% of suppliers hold 52% of relationships" is not an answer.
Concentration is consistent with a heavy tail, but it is also consistent with
plenty of other things, and eyeballing a skewed histogram has fooled better
people than us. So this module does three things properly:

  1. Fits a discrete power law by maximum likelihood, choosing x_min by
     Kolmogorov-Smirnov minimisation (Clauset, Shalizi & Newman 2009).
  2. Tests whether that fit is any good, via a bootstrap p-value -- because a
     fitted exponent means nothing on its own. You can fit a power law to
     anything; the question is whether the data could plausibly have come from
     one.
  3. Tests the power law against a lognormal by likelihood ratio. This is the
     honest step most portfolio projects skip. Heavy-tailed empirical data is
     very often better described by a lognormal, and claiming "scale-free"
     without ruling that out is exactly the error that got the original
     scale-free-networks literature into trouble.

Implemented directly rather than by calling the `powerlaw` package, because
being able to explain what the estimator does is the entire point of the
exercise.

Reference: A. Clauset, C.R. Shalizi, M.E.J. Newman, "Power-law distributions in
empirical data", SIAM Review 51(4), 2009.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import networkx as nx
import numpy as np
from scipy import optimize, special, stats

# ---------------------------------------------------------------------------
# Power-law fitting
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PowerLawFit:
    """Result of fitting a discrete power law to the tail of a sample."""

    alpha: float          # scaling exponent
    x_min: int            # value above which the power law is fitted
    n_tail: int           # observations at or above x_min
    n_total: int
    ks_distance: float    # KS distance between data and fit, at this x_min
    p_value: float | None = None   # bootstrap GOF; None if not run

    @property
    def tail_fraction(self) -> float:
        return self.n_tail / self.n_total

    @property
    def is_informative(self) -> bool:
        """Whether this fit says anything worth reporting.

        A very large alpha fitted to a handful of tail points is not evidence
        of a power law -- it is the estimator describing a bounded distribution
        that falls off far too fast to be scaling. The bootstrap test will
        happily return a high p-value in that situation, because with sixty
        points it lacks the power to reject anything. Reporting that as
        "power law plausible" would be actively misleading.
        """
        return self.alpha < 4.0 and self.n_tail >= 50 and self.tail_fraction >= 0.05

    @property
    def verdict(self) -> str:
        if self.p_value is None:
            return "not tested"
        if not self.is_informative:
            return "uninformative (tail too small or decay too steep)"
        return "plausible" if self.p_value > 0.10 else "rejected"

    def __str__(self) -> str:
        p = "not tested" if self.p_value is None else f"{self.p_value:.3f}"
        return (
            f"alpha={self.alpha:.3f}  x_min={self.x_min}  "
            f"n_tail={self.n_tail} ({self.tail_fraction:.0%})  "
            f"KS={self.ks_distance:.4f}  p={p}"
        )


def _log_likelihood_discrete(alpha: float, tail: np.ndarray, x_min: int) -> float:
    """Negative log-likelihood of a discrete power law.

        P(x) = x^-alpha / zeta(alpha, x_min)

    where zeta is the Hurwitz zeta function -- the normalising constant that
    makes the probabilities over {x_min, x_min+1, ...} sum to one.
    """
    if alpha <= 1.0:
        return np.inf
    n = len(tail)
    return n * math.log(special.zeta(alpha, x_min)) + alpha * np.log(tail).sum()


def _fit_alpha(tail: np.ndarray, x_min: int) -> float:
    """MLE for the exponent, given x_min.

    No closed form exists in the discrete case, so this optimises numerically.
    The continuous approximation
        alpha ~= 1 + n / sum(ln(x / (x_min - 0.5)))
    is commonly used instead, but it degrades badly for small x_min -- which is
    exactly where supply chain degree data lives.
    """
    result = optimize.minimize_scalar(
        _log_likelihood_discrete,
        bounds=(1.01, 12.0),
        args=(tail, x_min),
        method="bounded",
    )
    return float(result.x)


def _ks_distance(tail: np.ndarray, alpha: float, x_min: int) -> float:
    """Kolmogorov-Smirnov distance between the empirical and fitted CDFs."""
    values = np.unique(tail)
    # Discrete power-law CCDF: P(X >= x) = zeta(alpha, x) / zeta(alpha, x_min)
    fitted_ccdf = special.zeta(alpha, values) / special.zeta(alpha, x_min)
    empirical_ccdf = np.array([(tail >= v).mean() for v in values])
    return float(np.max(np.abs(empirical_ccdf - fitted_ccdf)))


def fit_power_law(
    data: np.ndarray,
    min_tail: int = 50,
    max_candidates: int = 60,
    x_min: int | None = None,
) -> PowerLawFit:
    """Fit a discrete power law, choosing x_min to minimise KS distance.

    Why x_min has to be estimated rather than assumed: power laws in real data
    almost never hold across the whole range, only above some threshold. Fixing
    x_min = 1 would force the fit to accommodate the bulk of small suppliers
    and produce a meaningless exponent. Scanning candidate thresholds and
    keeping the one whose fit tracks the data most closely is the standard
    treatment.

    `min_tail` stops the scan from choosing an absurdly high threshold where
    six points fit a line perfectly and mean nothing.

    Pass `x_min` to skip the scan and fit at a fixed threshold. Estimated x_min
    carries substantial variance -- Clauset et al. document this -- so pinning
    it is useful when comparing distributions on identical footing, or when
    domain knowledge already says where the tail begins.
    """
    data = np.asarray(data)
    data = data[data > 0].astype(int)
    if len(data) < min_tail:
        raise ValueError(f"need at least {min_tail} positive observations")

    if x_min is not None:
        tail = data[data >= x_min]
        if len(tail) < 2:
            raise ValueError(f"x_min={x_min} leaves fewer than 2 observations")
        alpha = _fit_alpha(tail, x_min)
        return PowerLawFit(
            alpha=alpha,
            x_min=int(x_min),
            n_tail=len(tail),
            n_total=len(data),
            ks_distance=_ks_distance(tail, alpha, x_min),
        )

    candidates = np.unique(data)
    candidates = candidates[candidates >= 1]
    # Only consider thresholds that leave a usable tail.
    viable = [int(c) for c in candidates if (data >= c).sum() >= min_tail]
    if not viable:
        raise ValueError("no x_min leaves a large enough tail")
    if len(viable) > max_candidates:
        step = len(viable) / max_candidates
        viable = [viable[int(i * step)] for i in range(max_candidates)]

    best: PowerLawFit | None = None
    for x_min in viable:
        tail = data[data >= x_min]
        alpha = _fit_alpha(tail, x_min)
        ks = _ks_distance(tail, alpha, x_min)
        if best is None or ks < best.ks_distance:
            best = PowerLawFit(
                alpha=alpha,
                x_min=x_min,
                n_tail=len(tail),
                n_total=len(data),
                ks_distance=ks,
            )
    assert best is not None
    return best


def _sample_discrete_power_law(
    rng: np.random.Generator,
    alpha: float,
    x_min: int,
    size: int,
    table: int = 20_000,
) -> np.ndarray:
    """Draw from the discrete power law by exact inverse CDF near x_min.

    The obvious shortcut -- x = (x_min - 0.5)(1-u)^(-1/(alpha-1)) rounded to an
    integer -- is not good enough here, and using it was a real bug. The
    bootstrap tests observed data against a *discrete* zeta model, so if its
    null datasets come from a continuous approximation instead, the two
    disagree systematically near x_min and the test mis-calibrates. It was
    rejecting samples drawn from the very distribution it was testing for, at
    p = 0.02.

    So: exact inversion over the first `table` integers, where discreteness
    actually matters, and the continuous form only out in the far tail where
    the two agree to well under one part in a thousand and only a handful of
    draws ever land.
    """
    xs = np.arange(x_min, x_min + table)
    ccdf = special.zeta(alpha, xs) / special.zeta(alpha, x_min)

    u = rng.random(size)
    # ccdf decreases from 1.0; X = max{x : P(X >= x) >= u}. Searching the
    # negated (increasing) array turns that into one vectorised lookup.
    idx = np.searchsorted(-ccdf, -u, side="right")
    out = np.empty(size, dtype=np.int64)

    inside = idx < len(xs)
    out[inside] = xs[np.maximum(idx[inside] - 1, 0)]

    far = ~inside
    if far.any():
        tail = (x_min - 0.5) * np.power(1.0 - u[far], -1.0 / (alpha - 1.0))
        out[far] = np.rint(tail).astype(np.int64)

    return np.maximum(out, x_min)


def bootstrap_gof(
    data: np.ndarray,
    fit: PowerLawFit,
    n_boot: int = 300,
    seed: int = 0,
    min_tail: int = 50,
) -> float:
    """Bootstrap p-value for the power-law fit.

    The logic is worth stating plainly, because a p-value here means the
    opposite of what people usually expect:

        - Build many synthetic datasets that genuinely *are* power-law in the
          tail and match the data below x_min.
        - Refit each from scratch, including re-estimating x_min.
        - p = the fraction whose KS distance is at least as bad as ours.

    A LARGE p means our data fits about as well as data we know came from a
    power law, so the hypothesis survives. A SMALL p means our data fits worse
    than genuine power-law data would, so we reject. Clauset et al. suggest
    ruling the power law out when p <= 0.1.

    Note this can only ever fail to reject. It never proves a power law, and it
    says nothing about whether some other distribution fits better -- which is
    what `compare_lognormal` is for.
    """
    rng = np.random.default_rng(seed)
    data = np.asarray(data)
    data = data[data > 0].astype(int)
    body = data[data < fit.x_min]
    p_tail = fit.n_tail / len(data)

    worse = 0
    for _ in range(n_boot):
        n_tail = rng.binomial(len(data), p_tail)
        n_body = len(data) - n_tail
        parts = [_sample_discrete_power_law(rng, fit.alpha, fit.x_min, n_tail)]
        if n_body > 0 and len(body) > 0:
            parts.append(rng.choice(body, size=n_body, replace=True))
        synthetic = np.concatenate(parts)
        try:
            synth_fit = fit_power_law(synthetic, min_tail=min_tail)
        except ValueError:
            continue
        if synth_fit.ks_distance >= fit.ks_distance:
            worse += 1
    return worse / n_boot


# ---------------------------------------------------------------------------
# Power law vs lognormal
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DistributionComparison:
    """Vuong-style likelihood ratio test between two candidate distributions."""

    log_likelihood_ratio: float   # >0 favours power law, <0 favours lognormal
    p_value: float                # significance of the difference
    mu: float
    sigma: float

    @property
    def verdict(self) -> str:
        if self.p_value > 0.10:
            return "inconclusive - neither fits significantly better"
        return "power law favoured" if self.log_likelihood_ratio > 0 else "lognormal favoured"


def compare_lognormal(data: np.ndarray, fit: PowerLawFit) -> DistributionComparison:
    """Test the fitted power law against a lognormal on the same tail.

    This is the step that separates a defensible claim from a fashionable one.
    Lognormal distributions arise naturally from multiplicative growth -- firms
    growing by percentages rather than absolute amounts -- and they produce
    curves that look convincingly straight on a log-log plot over one or two
    decades. Plenty of published "scale-free" networks turned out to be
    lognormal once anyone checked.

    Both distributions are fitted to the same tail (x >= x_min) and compared by
    normalised log-likelihood ratio. The p-value tests whether the difference
    between them is larger than sampling noise; a large p means the data cannot
    tell them apart, which is a perfectly respectable and quite common result.
    """
    data = np.asarray(data)
    tail = data[data >= fit.x_min].astype(float)
    n = len(tail)
    log_tail = np.log(tail)

    # Lognormal MLE, truncated below at x_min. Truncation matters: fitting an
    # untruncated lognormal to tail-only data biases the parameters and would
    # hand the comparison to the power law for free.
    def neg_ll(params: np.ndarray) -> float:
        mu, sigma = params
        if sigma <= 1e-6:
            return np.inf
        z = (math.log(fit.x_min) - mu) / sigma
        survival = 0.5 * special.erfc(z / math.sqrt(2.0))
        if survival <= 1e-12:
            return np.inf
        ll = (
            -log_tail
            - math.log(sigma)
            - 0.5 * math.log(2 * math.pi)
            - 0.5 * ((log_tail - mu) / sigma) ** 2
            - math.log(survival)
        )
        return -float(ll.sum())

    start = np.array([log_tail.mean(), max(log_tail.std(), 0.1)])
    result = optimize.minimize(neg_ll, start, method="Nelder-Mead")
    mu, sigma = float(result.x[0]), float(result.x[1])

    # Pointwise log-likelihoods under each model.
    z = (math.log(fit.x_min) - mu) / sigma
    survival = 0.5 * special.erfc(z / math.sqrt(2.0))
    ll_lognormal = (
        -log_tail
        - math.log(sigma)
        - 0.5 * math.log(2 * math.pi)
        - 0.5 * ((log_tail - mu) / sigma) ** 2
        - math.log(survival)
    )
    # Continuous power law, so both models are compared on the same footing.
    ll_powerlaw = (
        math.log(fit.alpha - 1.0)
        - math.log(fit.x_min)
        - fit.alpha * np.log(tail / fit.x_min)
    )

    diff = ll_powerlaw - ll_lognormal
    ratio = float(diff.sum())
    spread = float(diff.std())
    if spread < 1e-12:
        return DistributionComparison(ratio, 1.0, mu, sigma)
    p = float(special.erfc(abs(ratio) / (math.sqrt(2 * n) * spread)))
    return DistributionComparison(ratio, p, mu, sigma)


# ---------------------------------------------------------------------------
# Null models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TopologySummary:
    """Structural fingerprint used to compare a graph against null models."""

    label: str
    n_nodes: int
    n_edges: int
    max_out_degree: int
    gini_out_degree: float
    top_decile_share: float
    clustering: float
    mean_path_length: float
    p90_path_length: float
    reachable_pairs_frac: float


def gini(values: np.ndarray) -> float:
    """Gini coefficient. 0 = every supplier equal, 1 = one supplier has it all."""
    x = np.sort(np.asarray(values, dtype=float))
    n = len(x)
    if n == 0 or x.sum() == 0:
        return 0.0
    index = np.arange(1, n + 1)
    return float((2 * (index * x).sum()) / (n * x.sum()) - (n + 1) / n)


def sampled_path_lengths(
    g: nx.DiGraph, n_sources: int = 250, seed: int = 0
) -> tuple[float, float, float]:
    """Mean, 90th percentile, and reachable-pair fraction of shortest paths.

    Sampled rather than exhaustive: all-pairs on 2,600 nodes is 2,600 BFS
    traversals, which is minutes of wall clock for a number that stabilises
    after a couple of hundred sources. Sampling sources uniformly gives an
    unbiased estimate of the distance distribution.
    """
    rng = np.random.default_rng(seed)
    nodes = list(g.nodes())
    if not nodes:
        return 0.0, 0.0, 0.0
    picks = rng.choice(len(nodes), size=min(n_sources, len(nodes)), replace=False)

    distances: list[int] = []
    reachable = 0
    attempted = 0
    for i in picks:
        lengths = nx.single_source_shortest_path_length(g, nodes[i])
        lengths.pop(nodes[i], None)
        distances.extend(lengths.values())
        reachable += len(lengths)
        attempted += len(nodes) - 1

    if not distances:
        return 0.0, 0.0, 0.0
    arr = np.array(distances)
    return float(arr.mean()), float(np.percentile(arr, 90)), reachable / attempted


def summarise(g: nx.DiGraph, label: str, seed: int = 0) -> TopologySummary:
    out_deg = np.array([d for _, d in g.out_degree()], dtype=float)
    nonzero = out_deg[out_deg > 0]
    ordered = np.sort(nonzero)[::-1] if len(nonzero) else np.array([0.0])
    decile = max(1, len(ordered) // 10)

    mean_path, p90_path, reach = sampled_path_lengths(g, seed=seed)

    return TopologySummary(
        label=label,
        n_nodes=g.number_of_nodes(),
        n_edges=g.number_of_edges(),
        max_out_degree=int(out_deg.max()) if len(out_deg) else 0,
        gini_out_degree=gini(nonzero),
        top_decile_share=float(ordered[:decile].sum() / ordered.sum()),
        clustering=float(nx.average_clustering(g.to_undirected())),
        mean_path_length=mean_path,
        p90_path_length=p90_path,
        reachable_pairs_frac=reach,
    )


def erdos_renyi_like(g: nx.DiGraph, seed: int = 0) -> nx.DiGraph:
    """Random graph with the same node and edge count. The crudest null model.

    Answers "is this network distinguishable from wiring the same number of
    connections at random?" If the answer were no, there would be no project.
    """
    return nx.gnm_random_graph(
        g.number_of_nodes(), g.number_of_edges(), seed=seed, directed=True
    )


def configuration_like(g: nx.DiGraph, seed: int = 0) -> nx.DiGraph:
    """Random graph preserving the exact in- and out-degree of every node.

    A much sharper null model than Erdos-Renyi, and the one that actually
    matters. It reproduces our degree distribution perfectly by construction,
    so anything the real network has that this one does not cannot be explained
    by "some suppliers are big". Whatever survives this comparison is the part
    that comes from tier structure and geography.
    """
    in_seq = [d for _, d in g.in_degree()]
    out_seq = [d for _, d in g.out_degree()]
    multi = nx.directed_configuration_model(in_seq, out_seq, seed=seed)
    simple = nx.DiGraph(multi)          # collapses parallel edges
    simple.remove_edges_from(nx.selfloop_edges(simple))
    return simple


__all__ = [
    "DistributionComparison",
    "PowerLawFit",
    "TopologySummary",
    "bootstrap_gof",
    "compare_lognormal",
    "configuration_like",
    "erdos_renyi_like",
    "fit_power_law",
    "gini",
    "sampled_path_lengths",
    "summarise",
]
