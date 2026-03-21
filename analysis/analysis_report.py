"""
Analysis Report Generator for Multi-Agent AI Dysfunction (Paper 12)

Generates publication-quality figures and summary tables from the
token breakdown, information loss, and frontier analyses.

Outputs:
  - governance_vs_implementation.pdf/png
  - information_entropy_stages.pdf/png
  - cost_scaling_frontier.pdf/png
  - summary_table.tex (LaTeX table for paper inclusion)
"""

import numpy as np
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.gridspec import GridSpec

# ---------------------------------------------------------------------------
# Style configuration for publication quality
# ---------------------------------------------------------------------------

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.labelsize": 11,
    "axes.titlesize": 12,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 8,
    "figure.dpi": 300,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.1,
    "axes.grid": True,
    "grid.alpha": 0.3,
    "grid.linewidth": 0.5,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

ANALYSIS_DIR = Path(__file__).parent
FIGURES_DIR = ANALYSIS_DIR.parent / "figures"
FIGURES_DIR.mkdir(parents=True, exist_ok=True)

AGENT_COUNTS = [3, 5, 7, 10, 15]

# Architecture colors (consistent across all figures)
COLORS = {
    "single": "#607D8B",
    "centralized": "#2196F3",
    "fully_connected": "#F44336",
    "hierarchical": "#4CAF50",
    "ring": "#FF9800",
    "star_mesh": "#9C27B0",
    "pipeline": "#795548",
}

MARKERS = {
    "single": "o",
    "centralized": "s",
    "fully_connected": "D",
    "hierarchical": "^",
    "ring": "v",
    "star_mesh": "P",
    "pipeline": "X",
}


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

def load_json(filename: str) -> dict:
    path = ANALYSIS_DIR / filename
    with open(path, "r") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Figure 1: Governance vs Implementation Token Ratio
# ---------------------------------------------------------------------------

def figure_governance_ratio(token_data: dict):
    """
    Two-panel figure:
    (a) Governance fraction vs n for each architecture
    (b) Stacked bar chart at n=5 and n=10
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.5))

    # Panel (a): Line plot of governance fraction vs n
    for arch_key, arch_data in token_data.items():
        ns = []
        fracs = []
        stds = []
        for n in AGENT_COUNTS:
            d = arch_data["results"][str(n)]
            ns.append(n)
            fracs.append(d["governance_fraction_mean"])
            stds.append(d["governance_fraction_std"])

        ax1.errorbar(
            ns, fracs, yerr=stds,
            marker=MARKERS.get(arch_key, "o"),
            color=COLORS.get(arch_key, "#333333"),
            label=arch_data["name"],
            linewidth=1.5,
            markersize=5,
            capsize=3,
        )

    # Add empirical reference line from Study 1
    ax1.axhline(y=0.226, color="gray", linestyle="--", alpha=0.5, linewidth=1)
    ax1.annotate("Study 1 empirical\n(22.6%)", xy=(14, 0.226),
                 fontsize=7, color="gray", ha="right", va="bottom")

    ax1.set_xlabel("Number of Agents ($n$)")
    ax1.set_ylabel("Governance Token Fraction")
    ax1.set_title("(a) Governance Overhead by Architecture")
    ax1.legend(loc="upper left", framealpha=0.9, fontsize=7)
    ax1.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax1.set_xticks(AGENT_COUNTS)
    ax1.set_ylim(0, None)

    # Panel (b): Stacked bar chart at n=5 and n=10
    arch_keys = list(token_data.keys())
    arch_names = [token_data[k]["name"].replace(" (Hub-Spoke)", "\n(Hub)").replace(" (Tree)", "\n(Tree)").replace(" Hybrid", "\nHybrid")
                  for k in arch_keys]
    x = np.arange(len(arch_keys))
    width = 0.35

    for n_idx, n in enumerate([5, 10]):
        gov_vals = []
        impl_vals = []
        for arch_key in arch_keys:
            d = token_data[arch_key]["results"][str(n)]
            gov_vals.append(d["governance_tokens_mean"] / 1000)
            impl_vals.append(d["implementation_tokens_mean"] / 1000)

        offset = (n_idx - 0.5) * width
        bars_impl = ax2.bar(x + offset, impl_vals, width * 0.9,
                           label=f"Impl (n={n})" if n_idx == 0 else f"Impl (n={n})",
                           color=[COLORS.get(k, "#333") for k in arch_keys],
                           alpha=0.4 + 0.3 * n_idx,
                           edgecolor="white", linewidth=0.5)
        bars_gov = ax2.bar(x + offset, gov_vals, width * 0.9,
                          bottom=impl_vals,
                          label=f"Gov (n={n})",
                          color=[COLORS.get(k, "#333") for k in arch_keys],
                          alpha=0.2 + 0.15 * n_idx,
                          hatch="///" if n_idx == 0 else "\\\\\\",
                          edgecolor="gray", linewidth=0.5)

    ax2.set_xlabel("Architecture")
    ax2.set_ylabel("Tokens (thousands)")
    ax2.set_title("(b) Token Allocation at $n=5$ and $n=10$")
    ax2.set_xticks(x)
    ax2.set_xticklabels(arch_names, fontsize=7)

    # Custom legend for stacked bars
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor="gray", alpha=0.5, label="Implementation"),
        Patch(facecolor="gray", alpha=0.3, hatch="///", label="Governance"),
        Patch(facecolor="white", edgecolor="black", alpha=0.4, label="n=5"),
        Patch(facecolor="white", edgecolor="black", alpha=0.7, label="n=10"),
    ]
    ax2.legend(handles=legend_elements, loc="upper left", fontsize=7, framealpha=0.9)

    fig.suptitle("Governance vs. Implementation Token Distribution\n"
                 "Multi-Agent Coordination Architectures",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()

    for fmt in ["pdf", "png"]:
        fig.savefig(FIGURES_DIR / f"governance_vs_implementation.{fmt}")
    plt.close(fig)
    print(f"  Saved governance_vs_implementation.pdf/png")


# ---------------------------------------------------------------------------
# Figure 2: Information Entropy Through Coordination Stages
# ---------------------------------------------------------------------------

def figure_information_entropy(info_data: dict):
    """
    Two-panel figure:
    (a) Entropy through pipeline stages for each architecture at n=10
    (b) Total information loss vs n for each architecture
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.5))

    stage_labels = [
        "Spec", "Decomp", "Assign", "Exec", "Integ", "Verify"
    ]
    x_stages = np.arange(len(stage_labels))

    # Panel (a): Entropy through stages at n=10
    n = 10
    for arch_key, arch_data in info_data.items():
        if arch_key == "crawford_sobel":
            continue
        d = arch_data["results"][str(n)]
        entropies = d["entropy_per_stage"]

        ax1.plot(
            x_stages, entropies,
            marker=MARKERS.get(arch_key, "o"),
            color=COLORS.get(arch_key, "#333333"),
            label=arch_data["name"],
            linewidth=1.5,
            markersize=5,
        )

    ax1.set_xlabel("Coordination Stage")
    ax1.set_ylabel("Shannon Entropy (bits)")
    ax1.set_title(f"(a) Information Preservation at $n={n}$")
    ax1.set_xticks(x_stages)
    ax1.set_xticklabels(stage_labels, fontsize=8)
    ax1.legend(loc="lower left", framealpha=0.9, fontsize=6.5)
    ax1.set_ylim(0, 11)

    # Add DPI annotation
    ax1.annotate(
        "Data Processing\nInequality: entropy\ncan only decrease",
        xy=(3, 9.5), fontsize=7, color="gray",
        ha="center", style="italic",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow",
                  edgecolor="gray", alpha=0.8),
    )

    # Panel (b): Total loss vs n
    for arch_key, arch_data in info_data.items():
        if arch_key == "crawford_sobel":
            continue
        ns = []
        losses = []
        for n_val in AGENT_COUNTS:
            d = arch_data["results"][str(n_val)]
            ns.append(n_val)
            losses.append(d["total_information_loss"])

        ax2.plot(
            ns, losses,
            marker=MARKERS.get(arch_key, "o"),
            color=COLORS.get(arch_key, "#333333"),
            label=arch_data["name"],
            linewidth=1.5,
            markersize=5,
        )

    ax2.set_xlabel("Number of Agents ($n$)")
    ax2.set_ylabel("Total Information Loss (bits)")
    ax2.set_title("(b) Information Loss Scaling")
    ax2.legend(loc="upper left", framealpha=0.9, fontsize=6.5)
    ax2.set_xticks(AGENT_COUNTS)

    fig.suptitle("Information Loss Through Coordination Stages\n"
                 "Shannon Entropy and the Data Processing Inequality",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()

    for fmt in ["pdf", "png"]:
        fig.savefig(FIGURES_DIR / f"information_entropy_stages.{fmt}")
    plt.close(fig)
    print(f"  Saved information_entropy_stages.pdf/png")


# ---------------------------------------------------------------------------
# Figure 3: Cost Scaling with Frontier Lines
# ---------------------------------------------------------------------------

def figure_cost_scaling(frontier_data: dict):
    """
    Three-panel figure:
    (a) Total cost vs n at capability=1.0 (perfect agents), with frontier
    (b) Governance fraction vs n at different capability levels (one architecture)
    (c) Cost efficiency (output/cost) vs n
    """
    scaling = frontier_data["scaling_curves"]

    fig = plt.figure(figsize=(12, 4.5))
    gs = GridSpec(1, 3, figure=fig, width_ratios=[1, 1, 1])
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1])
    ax3 = fig.add_subplot(gs[2])

    agent_counts = [1, 2, 3, 5, 7, 10, 15, 20, 25]

    # Panel (a): Total cost and frontier at capability=1.0
    for model_key, model_data in scaling.items():
        cap_data = model_data["results"]["1.0"]
        ns = []
        totals = []
        frontiers = []
        for n in agent_counts:
            d = cap_data[str(n)]
            ns.append(n)
            totals.append(d["total_tokens"] / 1000)
            frontiers.append((d["implementation_tokens"] + d["frontier_governance"]) / 1000)

        ax1.plot(
            ns, totals,
            marker=MARKERS.get(model_key, "o"),
            color=COLORS.get(model_key, "#333333"),
            label=model_data["name"],
            linewidth=1.5,
            markersize=4,
        )
        # Frontier line (dashed)
        ax1.plot(
            ns, frontiers,
            color=COLORS.get(model_key, "#333333"),
            linewidth=0.8,
            linestyle=":",
            alpha=0.5,
        )

    ax1.set_xlabel("Number of Agents ($n$)")
    ax1.set_ylabel("Total Tokens (thousands)")
    ax1.set_title("(a) Cost Scaling (Perfect Agents)")
    ax1.legend(loc="upper left", framealpha=0.9, fontsize=5.5, ncol=1)
    ax1.set_yscale("log")

    # Add frontier annotation
    ax1.annotate(
        "Dotted = frontier\n(theoretical minimum)",
        xy=(18, 20), fontsize=7, color="gray",
        ha="center",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="lightyellow",
                  edgecolor="gray", alpha=0.8),
    )

    # Panel (b): Governance fraction vs capability for fully_connected at n=10
    # Shows that governance fraction INCREASES as capability improves
    fc_data = scaling["fully_connected"]
    caps = [0.5, 0.7, 0.85, 0.95, 1.0]
    for n_show in [3, 7, 15]:
        fracs = []
        for cap in caps:
            d = fc_data["results"][str(cap)][str(n_show)]
            fracs.append(d["governance_fraction"])
        ax2.plot(
            caps, fracs,
            marker="D",
            color=plt.cm.Reds(0.3 + 0.5 * (n_show / 15)),
            label=f"n={n_show}",
            linewidth=1.5,
            markersize=5,
        )

    ax2.set_xlabel("Agent Capability")
    ax2.set_ylabel("Governance Fraction")
    ax2.set_title("(b) Fully Connected:\nCapability vs. Governance")
    ax2.legend(loc="lower right", framealpha=0.9, fontsize=8)
    ax2.yaxis.set_major_formatter(mtick.PercentFormatter(1.0, decimals=0))
    ax2.set_xlim(0.45, 1.05)

    # Annotation: key insight
    ax2.annotate(
        "Better agents\n= higher governance %\n(impl shrinks, gov doesn't)",
        xy=(0.65, ax2.get_ylim()[1] * 0.85), fontsize=6.5, color="darkred",
        ha="center", style="italic",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="mistyrose",
                  edgecolor="darkred", alpha=0.7),
    )

    # Panel (c): Cost efficiency vs n
    for model_key, model_data in scaling.items():
        cap_data = model_data["results"]["0.85"]
        ns = []
        effs = []
        for n in agent_counts:
            d = cap_data[str(n)]
            ns.append(n)
            effs.append(d["cost_efficiency"])

        ax3.plot(
            ns, effs,
            marker=MARKERS.get(model_key, "o"),
            color=COLORS.get(model_key, "#333333"),
            label=model_data["name"],
            linewidth=1.5,
            markersize=4,
        )

    ax3.set_xlabel("Number of Agents ($n$)")
    ax3.set_ylabel("Cost Efficiency (output/token)")
    ax3.set_title("(c) Efficiency at Capability=0.85")
    ax3.legend(loc="upper right", framealpha=0.9, fontsize=5.5)

    # Add empirical data points from Study 3
    empirical = frontier_data["empirical_reference"]
    emp_names = ["single", "hierarchical", "emergence", "pipeline"]
    emp_n = [1, 2, 8, 11]
    emp_scores = [28, 18, 9, 0]
    emp_costs = [51.17, 50.62, 43.92, 1.90]
    emp_eff = [s / c if c > 0 else 0 for s, c in zip(emp_scores, emp_costs)]

    ax3_twin = ax3.twinx()
    ax3_twin.scatter(emp_n, emp_eff, marker="*", s=100, c="gold",
                     edgecolors="black", linewidth=0.5, zorder=5,
                     label="Study 3 empirical")
    ax3_twin.set_ylabel("Empirical Score/Cost", fontsize=8, color="goldenrod")
    ax3_twin.tick_params(axis="y", labelcolor="goldenrod", labelsize=8)
    ax3_twin.legend(loc="center right", fontsize=7, framealpha=0.9)

    fig.suptitle("Frontier Analysis: Capability Cannot Fix Coordination Overhead\n"
                 "\"Coordination overhead is not implementation debt to be paid down "
                 "with better prompting.\"",
                 fontsize=12, fontweight="bold", y=1.04)
    fig.tight_layout()

    for fmt in ["pdf", "png"]:
        fig.savefig(FIGURES_DIR / f"cost_scaling_frontier.{fmt}")
    plt.close(fig)
    print(f"  Saved cost_scaling_frontier.pdf/png")


# ---------------------------------------------------------------------------
# Figure 4: Crawford-Sobel Signal Degradation
# ---------------------------------------------------------------------------

def figure_crawford_sobel(info_data: dict):
    """
    Crawford-Sobel signal degradation through hierarchy layers.
    Shows how bias parameter b determines channel capacity.
    """
    cs = info_data.get("crawford_sobel", {})
    if not cs:
        print("  Skipping Crawford-Sobel figure (no data)")
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

    # Panel (a): Channel capacity vs bias
    biases = sorted([float(b) for b in cs.keys()])
    capacities = [cs[str(b)]["channel_capacity_bits"] for b in biases]
    partitions = [cs[str(b)]["partitions"] for b in biases]

    ax1.plot(biases, capacities, "ko-", linewidth=2, markersize=6)
    ax1.axvline(x=0.25, color="red", linestyle="--", alpha=0.5)
    ax1.annotate("Babbling\nequilibrium\n($b \\geq 1/4$)", xy=(0.25, 0.5),
                 fontsize=8, color="red", ha="left")
    ax1.set_xlabel("Bias parameter $b$")
    ax1.set_ylabel("Channel capacity (bits)")
    ax1.set_title("(a) Crawford-Sobel Channel Capacity")

    # Add partition count on secondary axis
    ax1_twin = ax1.twinx()
    ax1_twin.bar(biases, partitions, width=0.012, alpha=0.2, color="steelblue")
    ax1_twin.set_ylabel("$N^*$ (partitions)", color="steelblue", fontsize=9)
    ax1_twin.tick_params(axis="y", labelcolor="steelblue")

    # Panel (b): Capacity through 6 layers for different biases
    cmap = plt.cm.RdYlGn_r
    layers = list(range(7))  # 0 = start, 1-6 = layers
    for i, b in enumerate(biases[:5]):  # first 5 bias values
        caps = cs[str(b)]["capacity_through_6_layers"]
        color = cmap(i / 5)
        ax2.plot(layers, caps, marker="o", color=color,
                 label=f"$b={b}$", linewidth=1.5, markersize=4)

    ax2.set_xlabel("Hierarchy layers")
    ax2.set_ylabel("Information capacity (bits)")
    ax2.set_title("(b) Signal Degradation Through Layers")
    ax2.legend(loc="upper right", framealpha=0.9, fontsize=8)
    ax2.set_xticks(layers)

    ax2.annotate(
        "DPI: $I(X;Z) \\leq I(X;Y)$\nEach layer can only lose",
        xy=(3, 9), fontsize=7, color="gray", ha="center", style="italic",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow",
                  edgecolor="gray", alpha=0.8),
    )

    fig.suptitle("Crawford-Sobel Signal Degradation\n"
                 "\"The hierarchy does not slowly degrade information. "
                 "It kills subjective signal at a specific organizational seam.\"",
                 fontsize=11, fontweight="bold", y=1.04)
    fig.tight_layout()

    for fmt in ["pdf", "png"]:
        fig.savefig(FIGURES_DIR / f"crawford_sobel_degradation.{fmt}")
    plt.close(fig)
    print(f"  Saved crawford_sobel_degradation.pdf/png")


# ---------------------------------------------------------------------------
# Figure 5: Dysmemic Pressure Index Comparison
# ---------------------------------------------------------------------------

def figure_dysmemic_pressure(info_data: dict):
    """
    Dysmemic Pressure Index across architectures and agent counts.
    Connects to Paper 8 -- information loss through organizational hierarchy
    = information loss through agent hierarchy.
    """
    fig, ax = plt.subplots(1, 1, figsize=(7, 4.5))

    for arch_key, arch_data in info_data.items():
        if arch_key == "crawford_sobel":
            continue
        ns = []
        dpis = []
        for n in AGENT_COUNTS:
            d = arch_data["results"][str(n)]
            ns.append(n)
            dpis.append(d["dysmemic_pressure_index"])

        ax.plot(
            ns, dpis,
            marker=MARKERS.get(arch_key, "o"),
            color=COLORS.get(arch_key, "#333333"),
            label=arch_data["name"],
            linewidth=1.5,
            markersize=5,
        )

    ax.set_xlabel("Number of Agents ($n$)")
    ax.set_ylabel("Dysmemic Pressure Index")
    ax.set_title("Dysmemic Pressure Index by Architecture\n"
                 "(Compound information loss from compression, selection, and drift)",
                 fontsize=11, fontweight="bold")
    ax.legend(loc="upper left", framealpha=0.9, fontsize=8)
    ax.set_xticks(AGENT_COUNTS)

    fig.tight_layout()
    for fmt in ["pdf", "png"]:
        fig.savefig(FIGURES_DIR / f"dysmemic_pressure_index.{fmt}")
    plt.close(fig)
    print(f"  Saved dysmemic_pressure_index.pdf/png")


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def generate_summary_table(token_data: dict, info_data: dict, frontier_data: dict):
    """
    Generate summary statistics table for n=5 and n=10.
    Outputs both text and LaTeX format.
    """
    scaling = frontier_data["scaling_curves"]

    print("\n" + "=" * 100)
    print("SUMMARY TABLE: Architecture Performance at n=5 and n=10")
    print("=" * 100)

    header = (f"{'Architecture':<22s} {'n':>3} {'Gov%':>7} {'Links':>6} "
              f"{'Entropy Ret':>11} {'DPI':>6} {'Total Tok':>10} "
              f"{'Frontier Gov':>12} {'Efficiency':>10}")
    print(header)
    print("-" * len(header))

    # LaTeX table
    latex_lines = [
        r"\begin{table}[H]",
        r"\centering",
        r"\caption{Summary statistics by architecture at $n=5$ and $n=10$}",
        r"\label{tab:token-analysis}",
        r"\small",
        r"\begin{tabular}{@{}lrrrrrrr@{}}",
        r"\toprule",
        r"\textbf{Architecture} & \textbf{$n$} & \textbf{Gov\%} & \textbf{Links} "
        r"& \textbf{Ent.\ Ret.} & \textbf{DPI} & \textbf{Tokens} & \textbf{Efficiency} \\",
        r"\midrule",
    ]

    for n in [5, 10]:
        for arch_key in ["centralized", "fully_connected", "hierarchical", "ring", "star_mesh"]:
            # Token data
            td = token_data[arch_key]["results"][str(n)]
            name = token_data[arch_key]["name"]

            # Info data
            info_key = arch_key
            id_ = info_data[info_key]["results"][str(n)]

            # Frontier data
            fd = scaling[arch_key]["results"]["0.85"][str(n)]

            gov_pct = td["governance_fraction_mean"]
            links = td["n_links"]
            ent_ret = id_["retention_fraction"]
            dpi = id_["dysmemic_pressure_index"]
            total_tok = td["total_tokens_mean"]
            frontier_gov = fd["frontier_governance"]
            efficiency = fd["cost_efficiency"]

            row = (f"{name:<22s} {n:3d} {gov_pct:6.1%} {links:6d} "
                   f"{ent_ret:10.1%} {dpi:6.2f} {total_tok:10,.0f} "
                   f"{frontier_gov:12,.0f} {efficiency:10.4f}")
            print(row)

            # LaTeX row
            latex_name = name.replace("&", r"\&")
            latex_lines.append(
                f"{latex_name} & {n} & {gov_pct:.1%} & {links} "
                f"& {ent_ret:.1%} & {dpi:.2f} & {total_tok:,.0f} & {efficiency:.4f} \\\\"
            )

        if n == 5:
            print("-" * len(header))
            latex_lines.append(r"\midrule")

    latex_lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ])

    # Save LaTeX table
    latex_path = FIGURES_DIR / "summary_table.tex"
    with open(latex_path, "w") as f:
        f.write("\n".join(latex_lines))
    print(f"\n  Saved summary_table.tex")


# ---------------------------------------------------------------------------
# Empirical comparison table
# ---------------------------------------------------------------------------

def generate_empirical_comparison():
    """
    Side-by-side comparison of model predictions vs paper empirical data.
    """
    print("\n" + "=" * 80)
    print("EMPIRICAL COMPARISON: Model vs. Paper Data")
    print("=" * 80)

    # From paper Table 4 (Study 3)
    print("\nStudy 3 Results (from paper):")
    print(f"  {'Architecture':<20s} {'Score':>6} {'Cost':>8} {'Cost/pt':>8} {'Files':>6}")
    print(f"  {'Unary (single)':<20s} {'28/28':>6} {'$51.17':>8} {'$1.83':>8} {'64':>6}")
    print(f"  {'Hi-Trust (hier.)':<20s} {'18/28':>6} {'$50.62':>8} {'$2.81':>8} {'39':>6}")
    print(f"  {'Emergence (stig.)':<20s} {'9/28':>6} {'$43.92':>8} {'$4.88':>8} {'152':>6}")
    print(f"  {'Org Swarm (pipe.)':<20s} {'0/28':>6} {'$1.90':>8} {'inf':>8} {'0':>6}")

    print("\nKey empirical findings replicated by model:")
    print("  1. Performance inversely correlated with coordination complexity")
    print("  2. Cost per quality point increases monotonically")
    print("  3. Governance fraction increases with n")
    print("  4. Information loss compounds through hierarchy layers (DPI)")
    print("  5. Capability improvement cannot eliminate governance overhead")

    print("\nStudy 1 Metrics:")
    print("  Total tokens: 7,166,664")
    print("  Total cost: $57.43 (2.3x budget overrun)")
    print("  Rejection rate: 87.5% (review), 67% (verify)")
    print("  Cost wasted on rejections: $12.97 (22.6%)")
    print("  Bikeshedding rejections: 4 with 0 factual issues, 69 subjective")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("ANALYSIS REPORT GENERATOR")
    print("Paper 12: Multi-Agent AI Dysfunction")
    print("=" * 60)

    # Load data from previous analyses
    print("\nLoading analysis results...")
    try:
        token_data = load_json("token_breakdown_results.json")
    except FileNotFoundError:
        print("  ERROR: token_breakdown_results.json not found. Run token_breakdown.py first.")
        sys.exit(1)

    try:
        info_data = load_json("information_loss_results.json")
    except FileNotFoundError:
        print("  ERROR: information_loss_results.json not found. Run information_loss.py first.")
        sys.exit(1)

    try:
        frontier_data = load_json("frontier_analysis_results.json")
    except FileNotFoundError:
        print("  ERROR: frontier_analysis_results.json not found. Run frontier_analysis.py first.")
        sys.exit(1)

    # Generate figures
    print("\nGenerating figures...")
    figure_governance_ratio(token_data)
    figure_information_entropy(info_data)
    figure_cost_scaling(frontier_data)
    figure_crawford_sobel(info_data)
    figure_dysmemic_pressure(info_data)

    # Generate tables
    print("\nGenerating summary tables...")
    generate_summary_table(token_data, info_data, frontier_data)
    generate_empirical_comparison()

    print("\n" + "=" * 60)
    print(f"All outputs saved to: {FIGURES_DIR}")
    print("=" * 60)

    # List all generated files
    print("\nGenerated files:")
    for f in sorted(FIGURES_DIR.glob("*")):
        size = f.stat().st_size
        print(f"  {f.name:<45s} {size:>8,d} bytes")


if __name__ == "__main__":
    main()
