"""Plot the supplier degree distribution against its fits and its null models.

    python scripts/plot_degree_distribution.py

Produces docs/degree_distribution.png -- two panels:

  Left   the observed distribution with the fitted power law and lognormal,
         so the reader can see how little separates them.
  Right  the same distribution against two null models, which is where the
         real evidence lives.

Why CCDF and not a histogram: binning a heavy-tailed sample destroys exactly
the part you care about. The tail lands in a handful of sparse bins whose
heights depend on where you happened to put the bin edges, and a log-log
histogram of a power law is visibly noisy at the top end even when the fit is
good. The complementary CDF uses every observation at its own value, needs no
binning choice, and its slope on log-log axes is the exponent itself.
"""

from __future__ import annotations

import math
import pickle
import sys
from pathlib import Path

import matplotlib
import numpy as np
from scipy import special

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config as cfg          # noqa: E402
from src import validate as V          # noqa: E402
from src.generator import generate     # noqa: E402

# Chart chrome, light surface.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

# Categorical slots 1-3. Validated all-pairs on this surface: worst CVD
# separation 9.2, worst normal-vision 24.0. Aqua sits below 3:1 contrast, so
# every series is direct-labelled as well as legended -- identity never rests
# on colour alone.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"

plt.rcParams.update({
    # matplotlib resolves real font names only -- "system-ui" is a CSS
    # keyword and emits a warning per glyph run if listed here.
    "font.family": ["Segoe UI", "DejaVu Sans"],
    "font.size": 10,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": AXIS,
    "axes.labelcolor": INK_2,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.7,
})


def ccdf(data: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Empirical P(X >= x) at every observed value."""
    values = np.unique(data)
    survival = np.array([(data >= v).mean() for v in values])
    return values, survival


def style(ax, title: str, subtitle: str) -> None:
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Customers per supplier")
    ax.set_ylabel("P(X ≥ x)")
    ax.set_title(title, fontsize=12, fontweight="bold", color=INK, loc="left", pad=30)
    ax.text(0, 1.015, subtitle, transform=ax.transAxes, fontsize=8.5,
            color=INK_2, va="bottom")
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(0.8)


def panel_fits(ax, degrees: np.ndarray, fit: V.PowerLawFit,
               cmp: V.DistributionComparison) -> None:
    x, y = ccdf(degrees)
    ax.plot(x, y, "o", color=BLUE, markersize=4.5, alpha=0.75,
            markeredgewidth=0, label="Observed", zorder=3)

    tail_x = np.arange(fit.x_min, degrees.max() + 1)
    anchor = (degrees >= fit.x_min).mean()

    pl = anchor * special.zeta(fit.alpha, tail_x) / special.zeta(fit.alpha, fit.x_min)
    ax.plot(tail_x, pl, "-", color=ORANGE, linewidth=2.0, zorder=4,
            label=f"Power law  α = {fit.alpha:.2f}")

    z = (np.log(tail_x) - cmp.mu) / cmp.sigma
    z0 = (math.log(fit.x_min) - cmp.mu) / cmp.sigma
    ln = anchor * (0.5 * special.erfc(z / math.sqrt(2))) / (0.5 * special.erfc(z0 / math.sqrt(2)))
    ax.plot(tail_x, ln, "--", color=AQUA, linewidth=2.0, zorder=4,
            label=f"Lognormal  σ = {cmp.sigma:.2f}")

    ax.axvline(fit.x_min, color=MUTED, linewidth=1.0, linestyle=":", zorder=1)
    ax.text(fit.x_min * 1.08, 0.7, f"x_min = {fit.x_min}", fontsize=8.5,
            color=MUTED, rotation=90, va="top")

    # Direct labels, placed at the far end where the two curves have separated.
    # The aqua slot sits below 3:1 on this surface, so identity cannot rest on
    # a legend swatch alone -- and the lognormal label takes secondary ink
    # rather than its series colour for the same reason.
    far = int(len(tail_x) * 0.82)
    ax.annotate("Power law", xy=(tail_x[far], pl[far]),
                xytext=(8, 10), textcoords="offset points",
                fontsize=9, color=ORANGE, fontweight="bold")
    ax.annotate("Lognormal", xy=(tail_x[far], ln[far]),
                xytext=(8, -16), textcoords="offset points",
                fontsize=9, color=INK_2, fontweight="bold")

    style(ax, "The tail cannot distinguish the two",
          f"bootstrap p = {fit.p_value:.2f} (not rejected)  ·  "
          f"likelihood ratio p = {cmp.p_value:.2f} (inconclusive)")
    ax.legend(frameon=False, fontsize=9, loc="lower left", labelcolor=INK_2)


def panel_nulls(ax, degrees: np.ndarray, er: np.ndarray, conf: np.ndarray) -> None:
    for data, color, label, marker in (
        (degrees, BLUE, "Generated", "o"),
        (conf, ORANGE, "Configuration model", "s"),
        (er, AQUA, "Erdős–Rényi", "^"),
    ):
        x, y = ccdf(data)
        ax.plot(x, y, marker, color=color, markersize=4.0, alpha=0.75,
                markeredgewidth=0, label=label, zorder=3)

    ax.annotate("Erdős–Rényi\n(random wiring)", xy=(4.2, 6e-3),
                fontsize=9, color=INK_2, fontweight="bold", ha="center")
    ax.annotate("Generated &\nconfiguration model", xy=(75, 8e-2),
                fontsize=9, color=INK_2, fontweight="bold", ha="center")

    style(ax, "Nothing like a random graph",
          "configuration model matches degree by construction — "
          "differences elsewhere are tier structure")
    ax.legend(frameon=False, fontsize=9, loc="lower left", labelcolor=INK_2)


def main() -> int:
    cached = ROOT / "data" / "network.pkl"
    if cached.exists():
        with open(cached, "rb") as fh:
            net = pickle.load(fh)
    else:
        net = generate()

    com = net.commercial
    degrees = np.array([d for _, d in com.out_degree() if d > 0], dtype=int)

    fit = V.fit_power_law(degrees)
    fit.p_value = V.bootstrap_gof(degrees, fit, n_boot=300, seed=cfg.RANDOM_SEED)
    cmp = V.compare_lognormal(degrees, fit)
    print(f"power law: {fit}")
    print(f"vs lognormal: LR={cmp.log_likelihood_ratio:+.2f} p={cmp.p_value:.3f}")

    er_g = V.erdos_renyi_like(com, seed=cfg.RANDOM_SEED)
    conf_g = V.configuration_like(com, seed=cfg.RANDOM_SEED)
    er = np.array([d for _, d in er_g.out_degree() if d > 0], dtype=int)
    conf = np.array([d for _, d in conf_g.out_degree() if d > 0], dtype=int)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.2))
    panel_fits(ax1, degrees, fit, cmp)
    panel_nulls(ax2, degrees, er, conf)

    fig.suptitle(
        "Supplier concentration in the generated semiconductor network",
        fontsize=13.5, fontweight="bold", color=INK, x=0.008, ha="left", y=0.99,
    )
    fig.tight_layout(rect=(0, 0.02, 1, 0.94))

    out = ROOT / "docs"
    out.mkdir(exist_ok=True)
    path = out / "degree_distribution.png"
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
