"""Plot where centrality and measured disruption disagree.

    python scripts/plot_criticality.py

Produces docs/criticality.png -- two panels:

  Left   every node placed by how central it looks against how much damage it
         actually does. The top-right region is the finding: nodes that break
         real production while sitting hundreds of places down every centrality
         ranking.
  Right  worst single failure by tier, which contains the result that most
         needs explaining -- fabs come last.

Requires data/criticality_comparison.csv, written by scripts/analyze_network.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE, ORANGE = "#2a78d6", "#eb6834"

plt.rcParams.update({
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


def style(ax, title: str, subtitle: str) -> None:
    ax.set_title(title, fontsize=12, fontweight="bold", color=INK, loc="left", pad=30)
    ax.text(0, 1.015, subtitle, transform=ax.transAxes, fontsize=8.5,
            color=INK_2, va="bottom")
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_linewidth(0.8)


def panel_scatter(ax, df: pd.DataFrame) -> None:
    df = df.dropna(subset=["best_centrality_rank"]).copy()
    df["share_pct"] = df["share_of_oem_value"] * 100
    hubs = df[df["tier"] == "LOGISTICS"]
    firms = df[df["tier"] != "LOGISTICS"]

    ax.scatter(firms["best_centrality_rank"], firms["share_pct"],
               s=26, color=BLUE, alpha=0.55, linewidths=0, label="Firms", zorder=3)
    ax.scatter(hubs["best_centrality_rank"], hubs["share_pct"],
               s=52, color=ORANGE, alpha=0.9, marker="D", linewidths=0,
               label="Ports & air hubs", zorder=4)

    ax.set_xscale("log")
    ax.set_xlabel("Best centrality rank  (further right = centrality rates it lower)")
    ax.set_ylabel("Production value halted (%)")

    # Name the nodes that make the point: the most *damaging* of the ones
    # centrality rates poorly. Ranking by rank_gap instead picks nodes with a
    # spectacular gap but negligible impact, which labels the wrong story.
    missed = df[df["best_centrality_rank"] > 150].nlargest(3, "share_of_oem_value")
    offsets = [(-12, 12), (-12, -4), (14, 6)]
    aligns = ["right", "right", "left"]
    for (_, row), offset, align in zip(missed.iterrows(), offsets, aligns):
        label = f"{row['name'].split(' - ')[0]}  ({row['category'].lower()})"
        ax.annotate(
            label,
            xy=(row["best_centrality_rank"], row["share_pct"]),
            xytext=offset, textcoords="offset points",
            fontsize=8.5, color=INK_2, ha=align,
            arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.8),
        )

    ax.axvspan(150, df["best_centrality_rank"].max() * 1.3, ymin=0.30,
               color=BLUE, alpha=0.05, zorder=1)
    ax.text(0.97, 0.94, "centrality misses these", transform=ax.transAxes,
            fontsize=9, color=INK_2, fontweight="bold", ha="right")
    ax.text(0.03, 0.06, "every hub sits here:\nprominent, but freight reroutes",
            transform=ax.transAxes, fontsize=8.5, color=ORANGE, fontweight="bold")

    style(ax, "Centrality finds none of the ten most damaging nodes",
          "each point is one node · vertical position is measured, horizontal is predicted")
    ax.legend(frameon=False, fontsize=9, loc="upper left",
              labelcolor=INK_2, bbox_to_anchor=(0.0, 0.88))


def panel_tiers(ax, df: pd.DataFrame) -> None:
    order = ["FAB", "LOGISTICS", "EQUIPMENT", "RAW_MATERIAL",
             "REFINED_MATERIAL", "OSAT", "EMS"]
    worst = (df.groupby("tier")["share_of_oem_value"].max() * 100).reindex(order)

    bars = ax.barh(range(len(worst)), worst.values, height=0.62,
                   color=BLUE, zorder=3)
    ax.set_yticks(range(len(worst)))
    ax.set_yticklabels([t.replace("_", " ").title() for t in worst.index], fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Worst single failure in that tier (% of production halted)")

    for bar, value in zip(bars, worst.values):
        ax.text(value + 0.06, bar.get_y() + bar.get_height() / 2,
                f"{value:.1f}%", va="center", fontsize=9, color=INK_2)

    ax.set_xlim(0, worst.max() * 1.22)
    ax.grid(axis="y", visible=False)
    style(ax, "The structural waist is the safest tier",
          "fabs are the narrowest layer and the least damaging to lose — see README")


def main() -> int:
    path = ROOT / "data" / "criticality_comparison.csv"
    if not path.exists():
        print(f"missing {path} -- run scripts/analyze_network.py first")
        return 1
    df = pd.read_csv(path)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.0, 5.4),
                                   gridspec_kw={"width_ratios": [1.25, 1]})
    panel_scatter(ax1, df)
    panel_tiers(ax2, df)

    fig.suptitle("Predicted importance versus measured disruption",
                 fontsize=13.5, fontweight="bold", color=INK, x=0.008, ha="left", y=0.99)
    fig.tight_layout(rect=(0, 0.02, 1, 0.93))

    out = ROOT / "docs" / "criticality.png"
    out.parent.mkdir(exist_ok=True)
    fig.savefig(out, dpi=150, facecolor=SURFACE)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
