"""
Token Breakdown Analysis for Multi-Agent AI Dysfunction (Paper 12)

Models and analyzes token usage across coordination architectures.
Simulates governance vs. implementation token allocation for:
  - Centralized (hub-and-spoke)
  - Fully connected
  - Hierarchical (tree)
  - Ring
  - Star-mesh hybrid

Key claim: governance fraction increases with n, especially for fully-connected
topologies, because coordination overhead scales with communication links (n^2),
not with raw capability.

Reference: Paper 12, Section 5 (Study 3) -- controlled architecture comparison
showed performance inversely correlated with coordination complexity:
  Unary 28/28, Hi-Trust 18/28, Emergence 9/28, Org Swarm 0/28
"""

import numpy as np
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_COUNTS = [3, 5, 7, 10, 15]
TOKEN_BUDGET_PER_AGENT = 4096          # context window per agent per step
TASK_STEPS = 8                         # decompose, assign, implement x3, integrate, verify, report
RNG_SEED = 42

# Based on empirical data from paper:
#   Study 1: 7.17M tokens, 89 stages, ~22.6% wasted on rejections
#   Study 3: cost/point monotonically increasing with coordination complexity
#   Execution stages passed 100%, review/verify stages rejected 87%/67%
EMPIRICAL_GOVERNANCE_BASELINE = 0.226  # minimum governance fraction (Study 1)
EMPIRICAL_REJECTION_RATE = 0.875       # review rejection rate (Study 1)


# ---------------------------------------------------------------------------
# Architecture definitions
# ---------------------------------------------------------------------------

@dataclass
class Architecture:
    name: str
    short_name: str
    color: str

    def communication_links(self, n: int) -> int:
        """Number of directed communication links for n agents."""
        raise NotImplementedError

    def coordination_hops(self, n: int) -> float:
        """Average number of hops for a message to traverse the network."""
        raise NotImplementedError

    def governance_token_fraction(self, n: int) -> float:
        """
        Fraction of total tokens consumed by governance (coordination,
        status updates, conflict resolution) vs implementation.

        Model: each communication link requires a base overhead of tokens
        for synchronization. Governance fraction =
            link_overhead * links / (link_overhead * links + impl_tokens)

        Where impl_tokens scale linearly with n (each agent does work)
        and link_overhead includes:
          - Status broadcast per link
          - Conflict resolution probability * resolution cost
          - Task routing overhead
        """
        links = self.communication_links(n)
        hops = self.coordination_hops(n)

        # Base tokens per agent for implementation (constant)
        impl_per_agent = TOKEN_BUDGET_PER_AGENT * 0.7  # 70% of budget is usable

        # Governance cost per link per step
        status_cost = 128          # status message tokens
        routing_cost = 64          # task routing tokens
        conflict_prob = 0.15       # probability of conflict per link per step
        conflict_cost = 512        # tokens to resolve a conflict

        # Crawford-Sobel degradation: information loss per hop
        # From paper eq. 1: N* = ceil(-1/2 + 1/2 * sqrt(1 + 2/b))
        # With small bias b ~ 0.05 per hop, signal degrades
        # This causes retransmission overhead
        retransmit_prob = 1 - (0.92 ** hops)  # ~8% loss per hop compounds
        retransmit_cost = 256 * retransmit_prob

        gov_per_link = status_cost + routing_cost + conflict_prob * conflict_cost + retransmit_cost

        total_gov = gov_per_link * links * TASK_STEPS
        total_impl = impl_per_agent * n * TASK_STEPS

        return total_gov / (total_gov + total_impl)

    def simulate_task(self, n: int, rng: np.random.Generator) -> dict:
        """
        Simulate a multi-step feature implementation task.
        Returns token allocation breakdown.
        """
        links = self.communication_links(n)
        hops = self.coordination_hops(n)

        results = {
            "architecture": self.name,
            "n_agents": n,
            "n_links": links,
            "avg_hops": round(hops, 2),
            "steps": [],
            "total_governance_tokens": 0,
            "total_implementation_tokens": 0,
            "total_tokens": 0,
            "governance_fraction": 0.0,
            "rejection_cycles": 0,
            "effective_output_tokens": 0,
        }

        step_names = [
            "decompose", "assign", "implement_1", "implement_2",
            "implement_3", "integrate", "verify", "report"
        ]

        for step_idx, step_name in enumerate(step_names):
            # Implementation tokens: each agent works on their piece
            is_impl_step = "implement" in step_name or step_name == "integrate"
            active_agents = n if is_impl_step else max(1, n // 3)

            impl_tokens = int(active_agents * TOKEN_BUDGET_PER_AGENT * 0.7)

            # Governance tokens scale with links
            base_gov = links * 128  # status broadcast
            routing_gov = links * 64 if step_name in ("decompose", "assign") else 0

            # Conflict resolution -- more likely in later steps
            conflict_rate = 0.1 + 0.05 * step_idx  # increases through pipeline
            n_conflicts = rng.binomial(links, conflict_rate)
            conflict_gov = int(n_conflicts * 512)

            # Rejection cycles (Goodhart optimization of review proxies)
            # From paper: review stages rejected 87.5%, verify rejected 67%
            if step_name == "verify":
                rejection_prob = 0.4 + 0.03 * links  # more links = more review friction
                rejection_prob = min(rejection_prob, 0.9)
                if rng.random() < rejection_prob:
                    results["rejection_cycles"] += 1
                    # Rejection wastes the implementation tokens from previous step
                    conflict_gov += int(impl_tokens * 0.5)

            # Crawford-Sobel retransmission
            retransmit = int(256 * links * (1 - 0.92 ** hops))

            gov_tokens = base_gov + routing_gov + conflict_gov + retransmit

            step_result = {
                "step": step_name,
                "implementation_tokens": impl_tokens,
                "governance_tokens": gov_tokens,
                "active_agents": active_agents,
                "conflicts": int(n_conflicts),
            }
            results["steps"].append(step_result)
            results["total_governance_tokens"] += gov_tokens
            results["total_implementation_tokens"] += impl_tokens

        results["total_tokens"] = (
            results["total_governance_tokens"] + results["total_implementation_tokens"]
        )
        results["governance_fraction"] = (
            results["total_governance_tokens"] / results["total_tokens"]
            if results["total_tokens"] > 0 else 0.0
        )
        # Effective output discounts rejected work
        results["effective_output_tokens"] = int(
            results["total_implementation_tokens"]
            * (1 - 0.226 * results["rejection_cycles"])  # 22.6% waste per rejection (Study 1)
        )

        return results


class Centralized(Architecture):
    """Hub-and-spoke: 1 coordinator + n-1 workers."""
    def __init__(self):
        super().__init__("Centralized (Hub-Spoke)", "centralized", "#2196F3")

    def communication_links(self, n: int) -> int:
        # Coordinator talks to each worker (bidirectional)
        return 2 * (n - 1)

    def coordination_hops(self, n: int) -> float:
        # All communication goes through hub: max 2 hops
        return 2.0


class FullyConnected(Architecture):
    """All agents communicate with all others."""
    def __init__(self):
        super().__init__("Fully Connected", "fully_connected", "#F44336")

    def communication_links(self, n: int) -> int:
        return n * (n - 1)  # directed

    def coordination_hops(self, n: int) -> float:
        return 1.0  # direct communication


class Hierarchical(Architecture):
    """Tree structure: 1 -> 3 -> 9 (branching factor ~3)."""
    def __init__(self):
        super().__init__("Hierarchical (Tree)", "hierarchical", "#4CAF50")

    def communication_links(self, n: int) -> int:
        # Tree with branching factor ~3: each non-leaf has 3 children
        # Links = 2 * (n - 1) for a tree (bidirectional parent-child)
        return 2 * (n - 1)

    def coordination_hops(self, n: int) -> float:
        # Average depth of a balanced tree with branching factor 3
        if n <= 1:
            return 0
        depth = max(1, np.ceil(np.log(n) / np.log(3)))
        # Average path length ~ depth (messages go up then down)
        return float(depth)


class Ring(Architecture):
    """Circular communication topology."""
    def __init__(self):
        super().__init__("Ring", "ring", "#FF9800")

    def communication_links(self, n: int) -> int:
        return 2 * n  # bidirectional ring

    def coordination_hops(self, n: int) -> float:
        return n / 4.0  # average shortest path in ring


class StarMeshHybrid(Architecture):
    """Coordinator + clustered workers (clusters of ~3)."""
    def __init__(self):
        super().__init__("Star-Mesh Hybrid", "star_mesh", "#9C27B0")

    def communication_links(self, n: int) -> int:
        # 1 coordinator + ceil((n-1)/3) cluster heads + workers
        n_workers = n - 1
        n_clusters = max(1, int(np.ceil(n_workers / 3)))
        # Coordinator <-> cluster heads
        coord_links = 2 * n_clusters
        # Within each cluster: fully connected among ~3 members
        cluster_size = min(3, n_workers)
        intra_links = n_clusters * cluster_size * (cluster_size - 1)
        return coord_links + intra_links

    def coordination_hops(self, n: int) -> float:
        # Coordinator -> cluster head -> worker = 3 hops max, avg ~2
        n_workers = n - 1
        n_clusters = max(1, int(np.ceil(n_workers / 3)))
        if n_clusters <= 1:
            return 2.0
        # Cross-cluster: 4 hops (worker->head->coord->head->worker)
        # Intra-cluster: 1-2 hops
        cross_frac = 1 - 1.0 / n_clusters
        return 1.5 * (1 - cross_frac) + 4.0 * cross_frac


ARCHITECTURES = [
    Centralized(),
    FullyConnected(),
    Hierarchical(),
    Ring(),
    StarMeshHybrid(),
]


# ---------------------------------------------------------------------------
# Run simulation
# ---------------------------------------------------------------------------

def run_all_simulations(n_runs: int = 20) -> dict:
    """
    Run simulations across all architectures and agent counts.
    Multiple runs to get stable statistics.
    """
    rng = np.random.default_rng(RNG_SEED)
    all_results = {}

    for arch in ARCHITECTURES:
        arch_results = {}
        for n in AGENT_COUNTS:
            runs = []
            for _ in range(n_runs):
                result = arch.simulate_task(n, rng)
                runs.append(result)

            # Aggregate across runs
            gov_fracs = [r["governance_fraction"] for r in runs]
            total_tokens = [r["total_tokens"] for r in runs]
            gov_tokens = [r["total_governance_tokens"] for r in runs]
            impl_tokens = [r["total_implementation_tokens"] for r in runs]
            rejections = [r["rejection_cycles"] for r in runs]
            effective = [r["effective_output_tokens"] for r in runs]

            arch_results[n] = {
                "n_agents": n,
                "n_links": arch.communication_links(n),
                "avg_hops": arch.coordination_hops(n),
                "governance_fraction_mean": float(np.mean(gov_fracs)),
                "governance_fraction_std": float(np.std(gov_fracs)),
                "governance_fraction_analytical": arch.governance_token_fraction(n),
                "total_tokens_mean": float(np.mean(total_tokens)),
                "total_tokens_std": float(np.std(total_tokens)),
                "governance_tokens_mean": float(np.mean(gov_tokens)),
                "implementation_tokens_mean": float(np.mean(impl_tokens)),
                "rejection_cycles_mean": float(np.mean(rejections)),
                "effective_output_mean": float(np.mean(effective)),
                "efficiency": float(np.mean(effective)) / float(np.mean(total_tokens)),
            }

        all_results[arch.short_name] = {
            "name": arch.name,
            "color": arch.color,
            "results": arch_results,
        }

    return all_results


def compute_theoretical_governance_fraction(arch: Architecture, n: int) -> float:
    """
    Theoretical governance fraction based on communication complexity.

    For a system with L communication links and n agents:
      G(n) = alpha * L / (alpha * L + beta * n)

    Where alpha = per-link overhead, beta = per-agent implementation budget.
    """
    return arch.governance_token_fraction(n)


def print_summary(results: dict):
    """Print a readable summary table."""
    print("\n" + "=" * 90)
    print("TOKEN BREAKDOWN ANALYSIS: Governance vs Implementation")
    print("=" * 90)

    for arch_key, arch_data in results.items():
        print(f"\n--- {arch_data['name']} ---")
        print(f"{'n':>4} {'Links':>6} {'Hops':>5} {'Gov%':>7} {'Gov%(A)':>8} "
              f"{'TotalTok':>10} {'Rej':>4} {'Eff%':>6}")
        print("-" * 60)
        for n, data in arch_data["results"].items():
            print(f"{data['n_agents']:4d} {data['n_links']:6d} {data['avg_hops']:5.1f} "
                  f"{data['governance_fraction_mean']:6.1%} "
                  f"{data['governance_fraction_analytical']:7.1%} "
                  f"{data['total_tokens_mean']:10,.0f} "
                  f"{data['rejection_cycles_mean']:4.1f} "
                  f"{data['efficiency']:5.1%}")

    # Key finding
    print("\n" + "=" * 90)
    print("KEY FINDING: Governance fraction scaling with n")
    print("=" * 90)
    for arch_key, arch_data in results.items():
        fracs = [
            arch_data["results"][n]["governance_fraction_mean"]
            for n in AGENT_COUNTS
        ]
        slope = (fracs[-1] - fracs[0]) / (AGENT_COUNTS[-1] - AGENT_COUNTS[0])
        print(f"  {arch_data['name']:<30s}: "
              f"{fracs[0]:.1%} (n=3) -> {fracs[-1]:.1%} (n=15), "
              f"slope={slope:.4f}/agent")

    # Empirical validation
    print("\n--- Empirical validation against Paper 12 data ---")
    print(f"  Study 1 governance overhead:  22.6% (measured)")
    print(f"  Study 1 review rejection:     87.5% (measured)")
    print(f"  Study 3 cost/point scaling:   $1.83, $2.81, $4.88, inf")
    print(f"  Model prediction (centralized, n=5): "
          f"{results['centralized']['results'][5]['governance_fraction_mean']:.1%}")
    print(f"  Model prediction (fully_connected, n=5): "
          f"{results['fully_connected']['results'][5]['governance_fraction_mean']:.1%}")


def save_results(results: dict, output_dir: Path):
    """Save results as JSON for use by analysis_report.py."""
    output_path = output_dir / "token_breakdown_results.json"

    # Convert numpy types to native Python for JSON serialization
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

    serializable = convert(results)

    with open(output_path, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"\nResults saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    output_dir = Path(__file__).parent.parent / "figures"
    output_dir.mkdir(parents=True, exist_ok=True)

    results = run_all_simulations(n_runs=50)
    print_summary(results)
    save_results(results, Path(__file__).parent)

    print("\nToken breakdown analysis complete.")
