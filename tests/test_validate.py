"""Tests for the statistical machinery in src/validate.py.

The validation module is what licenses every structural claim the project
makes, so it needs checking against data whose answer is already known. A
power-law estimator that silently returns 2.5 for everything would make the
network look beautifully scale-free and be worth nothing.

Each test here feeds the estimator a sample drawn from a distribution we chose,
and asks whether it recovers what we put in -- or correctly refuses to.
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import numpy as np
import pytest
from scipy import special

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import validate as V     # noqa: E402


def synthetic_power_law(alpha: float, x_min: int, n: int, seed: int = 1) -> np.ndarray:
    """Draw genuine discrete power-law data using the module's own sampler.

    Using the sampler under test to build the test data would be circular on
    its own, so `test_sampler_matches_the_discrete_model` below pins the
    sampler independently against the closed-form zeta CCDF. With that anchored,
    everything here is testing the estimator rather than the generator.
    """
    rng = np.random.default_rng(seed)
    return V._sample_discrete_power_law(rng, alpha, x_min, n)


def test_sampler_matches_the_discrete_model():
    """The null-data generator must actually be the distribution being tested.

    This is the anchor for every goodness-of-fit result in the module, and it
    exists because the first implementation failed it. The sampler used a
    continuous approximation while the fit used the discrete zeta model; they
    disagreed near x_min, and the bootstrap rejected data drawn from its own
    null hypothesis at p = 0.02.
    """
    alpha, x_min = 2.5, 10
    sample = V._sample_discrete_power_law(
        np.random.default_rng(0), alpha, x_min, 40_000
    )
    values = np.unique(sample)
    theoretical = special.zeta(alpha, values) / special.zeta(alpha, x_min)
    empirical = np.array([(sample >= v).mean() for v in values])
    assert np.max(np.abs(empirical - theoretical)) < 0.01


# -- recovering a known exponent -------------------------------------------

@pytest.mark.parametrize("true_alpha", [2.0, 2.5, 3.0])
def test_recovers_known_exponent(true_alpha):
    data = synthetic_power_law(true_alpha, x_min=10, n=6000, seed=7)
    fit = V.fit_power_law(data)
    assert abs(fit.alpha - true_alpha) < 0.2, (
        f"fitted {fit.alpha:.3f} against a true {true_alpha}"
    )


def test_x_min_excludes_the_non_power_law_body():
    """x_min must land above the contaminated region, not on top of it.

    Note what this does *not* assert. Estimated x_min carries large variance --
    Clauset et al. are explicit about it -- so demanding recovery of the exact
    threshold would be testing for an accuracy the method does not claim. What
    matters is that the estimator refuses to fit a power law across the uniform
    body it was handed, which is the failure that would corrupt the exponent.
    """
    body = np.random.default_rng(3).integers(1, 20, size=4000)
    data = np.concatenate([synthetic_power_law(2.5, x_min=20, n=4000, seed=3), body])
    fit = V.fit_power_law(data)
    assert fit.x_min >= 15, f"x_min={fit.x_min} reaches down into the uniform body"
    assert abs(fit.alpha - 2.5) < 0.35


def test_alpha_is_not_a_constant():
    """Guards against an estimator that ignores its input."""
    shallow = V.fit_power_law(synthetic_power_law(2.0, 10, 5000, seed=11))
    steep = V.fit_power_law(synthetic_power_law(3.5, 10, 5000, seed=11))
    assert steep.alpha > shallow.alpha + 1.0


# -- goodness of fit -------------------------------------------------------

def test_genuine_power_law_is_not_rejected():
    data = synthetic_power_law(2.5, x_min=10, n=3000, seed=5)
    fit = V.fit_power_law(data)
    p = V.bootstrap_gof(data, fit, n_boot=60, seed=5)
    assert p > 0.10, f"rejected real power-law data at p={p:.3f}"


def test_poisson_data_is_rejected_or_flagged():
    """A bounded, light-tailed sample must not pass as a power law."""
    data = np.random.default_rng(9).poisson(8, size=3000) + 1
    fit = V.fit_power_law(data)
    p = V.bootstrap_gof(data, fit, n_boot=60, seed=9)
    assert p <= 0.10 or not fit.is_informative


def test_gof_returns_a_probability():
    data = synthetic_power_law(2.5, 10, 1500, seed=2)
    fit = V.fit_power_law(data)
    assert 0.0 <= V.bootstrap_gof(data, fit, n_boot=40, seed=2) <= 1.0


# -- distinguishing power law from lognormal -------------------------------

def test_lognormal_data_favours_lognormal():
    """The comparison must be able to rule against the power law, or it proves nothing.

    Fitted at a low x_min so the comparison sees the body of the distribution.
    Deep in the tail of a lognormal a power law fits perfectly well -- the two
    families genuinely converge there -- so a test run only on the far tail
    would be asking the method to distinguish something that is not
    distinguishable, and would fail for the right reason.
    """
    rng = np.random.default_rng(4)
    data = np.maximum(1, np.rint(rng.lognormal(2.0, 1.1, size=6000))).astype(int)
    fit = V.fit_power_law(data, x_min=2)
    cmp = V.compare_lognormal(data, fit)
    assert cmp.log_likelihood_ratio < 0
    assert cmp.p_value < 0.10, "should confidently prefer lognormal on lognormal data"


def test_far_tail_comparison_is_honestly_inconclusive():
    """The flip side, asserted deliberately rather than discovered by surprise."""
    rng = np.random.default_rng(4)
    data = np.maximum(1, np.rint(rng.lognormal(2.0, 1.1, size=6000))).astype(int)
    fit = V.fit_power_law(data)          # estimator picks a high x_min here
    cmp = V.compare_lognormal(data, fit)
    assert cmp.p_value > 0.10
    assert "inconclusive" in cmp.verdict


def test_power_law_data_does_not_favour_lognormal():
    data = synthetic_power_law(2.5, x_min=10, n=6000, seed=6)
    fit = V.fit_power_law(data)
    cmp = V.compare_lognormal(data, fit)
    assert cmp.log_likelihood_ratio > 0 or cmp.p_value > 0.10


# -- inequality ------------------------------------------------------------

def test_gini_bounds():
    assert V.gini(np.ones(100)) == pytest.approx(0.0, abs=1e-9)
    winner_takes_all = np.concatenate([np.zeros(999), [1000.0]])
    assert V.gini(winner_takes_all) > 0.99


def test_gini_increases_with_inequality():
    even = np.full(500, 10.0)
    skewed = np.concatenate([np.full(490, 1.0), np.full(10, 500.0)])
    assert V.gini(skewed) > V.gini(even)


# -- null models -----------------------------------------------------------

def test_erdos_renyi_preserves_size():
    g = nx.gnm_random_graph(400, 2000, seed=1, directed=True)
    null = V.erdos_renyi_like(g, seed=2)
    assert null.number_of_nodes() == g.number_of_nodes()
    assert null.number_of_edges() == g.number_of_edges()


def test_configuration_model_tracks_degree_far_better_than_random():
    """What the configuration null actually guarantees -- and what it does not.

    It does not guarantee an exact degree sequence. Collapsing the multigraph
    to a simple graph and dropping self-loops costs edges, and the loss is
    concentrated on hubs whose parallel edges merge: on a heavily skewed graph
    a hub's degree can fall by half. Asserting exact preservation would be
    asserting something untrue.

    What it does guarantee is the property the analysis leans on -- that
    concentration is reproduced closely enough that any *remaining* difference
    from the real network cannot be attributed to degree. That is what this
    checks: the null tracks inequality far more closely than random wiring.
    """
    # Built with an explicitly heavy-tailed out-degree sequence. networkx's
    # scale_free_graph was tried first and is a poor stand-in here: it leaves
    # most nodes at out-degree 0 or 1, so its Erdos-Renyi equivalent has almost
    # the same inequality and the comparison has nothing to detect.
    rng = np.random.default_rng(0)
    n = 2000
    degrees = np.minimum(V._sample_discrete_power_law(rng, 2.3, 1, n), 200)
    g = nx.DiGraph()
    g.add_nodes_from(range(n))
    for u, k in enumerate(degrees):
        for v in rng.choice(n, size=int(min(k, n - 1)), replace=False):
            if v != u:
                g.add_edge(u, int(v))

    conf = V.configuration_like(g, seed=1)
    er = V.erdos_renyi_like(g, seed=1)

    def out_gini(graph):
        d = np.array([x for _, x in graph.out_degree()], dtype=float)
        return V.gini(d[d > 0])

    real, conf_g, er_g = out_gini(g), out_gini(conf), out_gini(er)
    assert abs(real - conf_g) < abs(real - er_g) / 3
    assert abs(sum(d for _, d in g.out_degree())
               - sum(d for _, d in conf.out_degree())) / g.number_of_edges() < 0.10


def test_path_length_sampling_is_stable():
    """Sampled means should agree across seeds, or the sample is too small."""
    g = nx.gnm_random_graph(800, 4000, seed=1, directed=True)
    a, _, _ = V.sampled_path_lengths(g, n_sources=200, seed=1)
    b, _, _ = V.sampled_path_lengths(g, n_sources=200, seed=99)
    assert abs(a - b) < 0.15


# -- guards ----------------------------------------------------------------

def test_rejects_samples_that_are_too_small():
    with pytest.raises(ValueError):
        V.fit_power_law(np.array([1, 2, 3, 4, 5]))


def test_steep_fit_on_a_thin_tail_is_flagged_uninformative():
    fit = V.PowerLawFit(
        alpha=7.9, x_min=17, n_tail=62, n_total=2098, ks_distance=0.04, p_value=0.54
    )
    assert not fit.is_informative
    assert "uninformative" in fit.verdict
