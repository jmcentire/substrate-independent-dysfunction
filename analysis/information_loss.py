"""
Information Loss Analysis for Multi-Agent AI Dysfunction (Paper 12)

Models information loss through coordination stages using Shannon entropy
and the Data Processing Inequality. Each coordination stage is modeled as
a lossy channel with architecture-dependent compression ratios.

Stages:
  1. Task specification (initial high entropy)
  2. Task decomposition
  3. Agent assignment
  4. Execution (implementation)
  5. Integration (merge/reconciliation)
  6. Verification (test/quality)

Key finding: hierarchical architectures lose more information per stage
because each layer compresses. This connects to dysmemic pressure
(Paper 8) -- information loss through organizational hierarchy =
information loss through agent hierarchy.

Reference: Paper 12, Section 2 -- Data Processing Inequality:
  I(X;Z) <= I(X;Y) for any Markov chain X -> Y -> Z
  "No post-processing of a compressed signal can recover information
   lost at the compression stage."
"""

import numpy as np
import json
from pathlib import Path
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RNG_SEED = 42
INITIAL_ENTROPY = 10.0  # bits -- high-dimensional task specification

# Coordination stages in order
STAGES = [
    "Task Specification",
    "Task Decomposition",
    "Agent Assignment",
    "Execution",
    "Integration",
    "Verification",
]

STAGE_SHORT = [
    "Specification",
    "Decomposition",
    "Assignment",
    "Execution",
    "Integration",
    "Verification",
]

# Agent counts to model
AGENT_COUNTS = [3, 5, 7, 10, 15]


# ---------------------------------------------------------------------------
# Architecture-specific compression models
# ---------------------------------------------------------------------------

@dataclass
class CompressionProfile:
    """
    Per-stage compression ratios for an architecture.

    Each ratio is the fraction of information RETAINED at that stage.
    Retention < 1.0 means lossy compression (Data Processing Inequality).

    These are calibrated against the paper's empirical findings:
    - Gated pipeline: consumed entire budget on planning (stages 1-2)
    - Hierarchical: coordinator refused to delegate (stage 3 bottleneck)
    - Emergence: interface mismatch at integration (stage 5 loss)
    - Single agent: no coordination loss (baseline)
    """
    name: str
    short_name: str
    color: str
    # Per-stage retention ratios [specification, decomposition, assignment,
    #                             execution, integration, verification]
    base_retention: list  # for n=1 baseline
    # How retention degrades with n agents
    # retention(n) = base * (1 - degradation * log2(n))
    n_degradation: list

    def retention_at_stage(self, stage_idx: int, n: int) -> float:
        """Retention ratio at a given stage for n agents."""
        base = self.base_retention[stage_idx]
        deg = self.n_degradation[stage_idx]
        # Logarithmic degradation with agent count
        factor = 1.0 - deg * np.log2(max(1, n))
        return max(0.05, min(1.0, base * factor))

    def entropy_through_pipeline(self, n: int) -> list:
        """
        Compute Shannon entropy at each stage.
        Data Processing Inequality: entropy can only decrease.
        """
        entropies = [INITIAL_ENTROPY]
        current = INITIAL_ENTROPY
        for i in range(len(STAGES) - 1):  # specification is the starting point
            retention = self.retention_at_stage(i + 1, n)
            current = current * retention
            entropies.append(current)
        return entropies

    def mutual_information_with_original(self, n: int) -> list:
        """
        I(Original; Stage_k) -- how much of the original intent is
        preserved at each stage. Monotonically non-increasing by DPI.
        """
        entropies = self.entropy_through_pipeline(n)
        # Mutual information = entropy at that stage (assuming deterministic
        # processing with loss, not noise addition)
        return entropies

    def total_information_loss(self, n: int) -> float:
        """Total information lost from specification to verification."""
        entropies = self.entropy_through_pipeline(n)
        return entropies[0] - entropies[-1]

    def loss_per_stage(self, n: int) -> list:
        """Information lost at each transition."""
        entropies = self.entropy_through_pipeline(n)
        losses = []
        for i in range(1, len(entropies)):
            losses.append(entropies[i - 1] - entropies[i])
        return losses


# Architecture definitions calibrated to paper findings
ARCHITECTURES = [
    CompressionProfile(
        name="Single Agent (Control)",
        short_name="single",
        color="#607D8B",
        # Single agent: minimal loss at each stage (no coordination overhead)
        base_retention=[1.0, 0.98, 0.99, 0.95, 0.97, 0.96],
        # No degradation with n (it's always 1 agent)
        n_degradation=[0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    ),
    CompressionProfile(
        name="Centralized (Hub-Spoke)",
        short_name="centralized",
        color="#2196F3",
        # Good at decomposition (coordinator sees everything),
        # moderate loss at execution (workers have partial context)
        base_retention=[1.0, 0.95, 0.92, 0.88, 0.90, 0.93],
        # Moderate degradation -- coordinator bottleneck
        n_degradation=[0.0, 0.01, 0.015, 0.02, 0.02, 0.01],
    ),
    CompressionProfile(
        name="Fully Connected",
        short_name="fully_connected",
        color="#F44336",
        # Good information preservation in theory (direct links),
        # but n^2 links create noise that drowns signal
        base_retention=[1.0, 0.93, 0.90, 0.85, 0.82, 0.88],
        # High degradation -- noise scales with n^2
        n_degradation=[0.0, 0.02, 0.03, 0.03, 0.04, 0.025],
    ),
    CompressionProfile(
        name="Hierarchical (Tree)",
        short_name="hierarchical",
        color="#4CAF50",
        # Each layer compresses -- the key finding
        # Good at decomposition, bad at preserving through layers
        # Paper: "Liberti and Mian: decision sensitivity to soft information
        #  collapses at structural break between 2nd and 3rd hierarchical levels"
        base_retention=[1.0, 0.92, 0.85, 0.82, 0.80, 0.90],
        # Strong degradation -- each layer adds a compression step
        n_degradation=[0.0, 0.025, 0.035, 0.04, 0.045, 0.02],
    ),
    CompressionProfile(
        name="Ring",
        short_name="ring",
        color="#FF9800",
        # Serial propagation: good local context, poor global
        # Assignment is good (sequential), integration is worst
        base_retention=[1.0, 0.90, 0.93, 0.87, 0.78, 0.85],
        # Moderate degradation -- path length grows with n
        n_degradation=[0.0, 0.02, 0.015, 0.025, 0.04, 0.025],
    ),
    CompressionProfile(
        name="Star-Mesh Hybrid",
        short_name="star_mesh",
        color="#9C27B0",
        # Best of centralized (good decomposition) and mesh (good local execution)
        # Integration is the weak point (cross-cluster coordination)
        base_retention=[1.0, 0.94, 0.91, 0.90, 0.85, 0.92],
        # Moderate degradation -- better than fully connected, worse than centralized
        n_degradation=[0.0, 0.015, 0.02, 0.02, 0.03, 0.015],
    ),
]


# ---------------------------------------------------------------------------
# Crawford-Sobel Signal Degradation Model
# ---------------------------------------------------------------------------

def crawford_sobel_partitions(bias: float) -> int:
    """
    Maximum distinguishable partitions in Crawford-Sobel communication.
    Eq. 1 from paper: N* = ceil(-1/2 + 1/2 * sqrt(1 + 2/b))
    At b >= 1/4, N* = 1 (babbling equilibrium).
    """
    if bias <= 0:
        return float('inf')
    if bias >= 0.25:
        return 1
    return int(np.ceil(-0.5 + 0.5 * np.sqrt(1 + 2.0 / bias)))


def information_capacity_cs(bias: float) -> float:
    """
    Information capacity (bits) of a Crawford-Sobel channel.
    With N* partitions, can transmit at most log2(N*) bits.
    """
    n_star = crawford_sobel_partitions(bias)
    if n_star == float('inf'):
        return INITIAL_ENTROPY
    if n_star <= 1:
        return 0.0
    return np.log2(n_star)


def model_cs_degradation_through_hierarchy(n_layers: int, bias_per_layer: float) -> list:
    """
    Model cumulative Crawford-Sobel degradation through hierarchy layers.
    Each layer introduces a sender-receiver interface with bias b.

    Returns list of information capacity at each layer.
    """
    capacities = [INITIAL_ENTROPY]
    current = INITIAL_ENTROPY
    for _ in range(n_layers):
        channel_capacity = information_capacity_cs(bias_per_layer)
        current = min(current, channel_capacity)
        # Actually: mutual information through chain is bounded by
        # the minimum channel capacity in the chain
        capacities.append(current)
    return capacities


# ---------------------------------------------------------------------------
# Dysmemic Pressure Connection (Paper 8)
# ---------------------------------------------------------------------------

def dysmemic_pressure_index(arch: CompressionProfile, n: int) -> float:
    """
    Dysmemic Pressure Index (DPI) for an architecture.

    From Paper 8: dysmemic pressure = compound selection force from
    (1) strategic communication degradation
    (2) adverse selection in idea markets
    (3) transmission bias

    We model this as the product of:
    - Total information loss (compression)
    - Number of selection interfaces (where Goodhart can operate)
    - Average path length (transmission bias accumulation)
    """
    total_loss = arch.total_information_loss(n)
    n_interfaces = len(STAGES) - 1  # transitions between stages

    # Effective path length varies by architecture
    if arch.short_name == "single":
        path_length = 1.0
    elif arch.short_name == "centralized":
        path_length = 2.0
    elif arch.short_name == "fully_connected":
        path_length = 1.0 + 0.1 * n  # noise from many paths
    elif arch.short_name == "hierarchical":
        path_length = np.ceil(np.log(max(2, n)) / np.log(3))
    elif arch.short_name == "ring":
        path_length = n / 4.0
    elif arch.short_name == "star_mesh":
        path_length = 2.5
    else:
        path_length = 2.0

    return total_loss * n_interfaces * path_length / INITIAL_ENTROPY


# ---------------------------------------------------------------------------
# Run analysis
# ---------------------------------------------------------------------------

def run_analysis() -> dict:
    """Run full information loss analysis across architectures and agent counts."""
    results = {}

    for arch in ARCHITECTURES:
        arch_results = {}
        for n in AGENT_COUNTS:
            entropies = arch.entropy_through_pipeline(n)
            losses = arch.loss_per_stage(n)
            mi = arch.mutual_information_with_original(n)
            dpi = dysmemic_pressure_index(arch, n)

            arch_results[n] = {
                "n_agents": n,
                "entropy_per_stage": [round(e, 4) for e in entropies],
                "loss_per_stage": [round(l, 4) for l in losses],
                "mutual_info_with_original": [round(m, 4) for m in mi],
                "total_information_loss": round(arch.total_information_loss(n), 4),
                "retention_fraction": round(entropies[-1] / entropies[0], 4),
                "dysmemic_pressure_index": round(dpi, 4),
            }

        results[arch.short_name] = {
            "name": arch.name,
            "color": arch.color,
            "results": arch_results,
        }

    # Crawford-Sobel hierarchy analysis
    cs_results = {}
    for bias in [0.01, 0.02, 0.05, 0.10, 0.15, 0.20, 0.25]:
        caps = model_cs_degradation_through_hierarchy(6, bias)
        cs_results[str(bias)] = {
            "bias": bias,
            "partitions": crawford_sobel_partitions(bias),
            "channel_capacity_bits": round(information_capacity_cs(bias), 4),
            "capacity_through_6_layers": [round(c, 4) for c in caps],
        }
    results["crawford_sobel"] = cs_results

    return results


def print_summary(results: dict):
    """Print readable summary."""
    print("\n" + "=" * 90)
    print("INFORMATION LOSS ANALYSIS: Shannon Entropy Through Coordination Stages")
    print("=" * 90)

    for arch_key in [a.short_name for a in ARCHITECTURES]:
        arch_data = results[arch_key]
        print(f"\n--- {arch_data['name']} ---")
        print(f"{'n':>4} | {'Spec':>6} {'Decomp':>6} {'Assign':>6} "
              f"{'Exec':>6} {'Integ':>6} {'Verify':>6} | "
              f"{'Loss':>6} {'Ret%':>6} {'DPI':>6}")
        print("-" * 82)
        for n in AGENT_COUNTS:
            d = arch_data["results"][str(n)] if str(n) in arch_data["results"] else arch_data["results"][n]
            ents = d["entropy_per_stage"]
            print(f"{d['n_agents']:4d} | "
                  + " ".join(f"{e:6.2f}" for e in ents) + " | "
                  f"{d['total_information_loss']:6.2f} "
                  f"{d['retention_fraction']:5.1%} "
                  f"{d['dysmemic_pressure_index']:6.2f}")

    # Crawford-Sobel analysis
    print("\n" + "=" * 90)
    print("CRAWFORD-SOBEL SIGNAL DEGRADATION")
    print("Eq. 1: N* = ceil(-1/2 + 1/2 * sqrt(1 + 2/b))")
    print("=" * 90)
    print(f"{'Bias b':>8} {'N*':>4} {'Capacity':>10} {'After 6 layers':>16}")
    print("-" * 42)
    cs = results["crawford_sobel"]
    for bias_str, data in cs.items():
        caps = data["capacity_through_6_layers"]
        print(f"{data['bias']:8.2f} {data['partitions']:4d} "
              f"{data['channel_capacity_bits']:10.2f} "
              f"{caps[-1]:16.2f}")

    # Key findings
    print("\n" + "=" * 90)
    print("KEY FINDINGS")
    print("=" * 90)

    # Compare architectures at n=10
    n = 10
    print(f"\nAt n={n} agents:")
    rankings = []
    for arch_key in [a.short_name for a in ARCHITECTURES]:
        d = results[arch_key]["results"]
        data = d[str(n)] if str(n) in d else d[n]
        rankings.append((results[arch_key]["name"], data["retention_fraction"],
                         data["dysmemic_pressure_index"]))

    rankings.sort(key=lambda x: -x[1])
    for name, ret, dpi in rankings:
        print(f"  {name:<30s}: {ret:.1%} retained, DPI={dpi:.2f}")

    print("\n  Hierarchical loses most per stage (each layer compresses)")
    print("  Fully connected: noise from n^2 links drowns signal")
    print("  Ring: serial propagation degrades with path length")
    print("  Single agent: minimal loss (no coordination overhead)")


def save_results(results: dict, output_dir: Path):
    """Save results as JSON."""
    output_path = output_dir / "information_loss_results.json"

    def convert(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, dict):
            return {str(k): convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [convert(v) for v in obj]
        return obj

    with open(output_path, "w") as f:
        json.dump(convert(results), f, indent=2)
    print(f"\nResults saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    results = run_analysis()
    print_summary(results)
    save_results(results, Path(__file__).parent)
    print("\nInformation loss analysis complete.")
