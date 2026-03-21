"""
Frontier Analysis for Multi-Agent AI Dysfunction (Paper 12)

Demonstrates that scaling model capability does not fix coordination overhead.
Even with perfect agents (zero implementation error), governance overhead remains
because it is structural, not a capability issue.

Key finding from paper:
  "The cost of agreeing about what to build exceeded the budget for building it."
  "Coordination overhead is not implementation debt to be paid down with better
   prompting. It is an information-theoretic cost of distributed cognition."

Empirical calibration:
  Study 3 cost/point: $1.83 (single), $2.81 (hierarchical), $4.88 (emergence), inf (pipeline)
  Chen et al.: every multi-agent variant degraded sequential reasoning 39-70%
  Independent agents amplified errors 17.2x
  Centralized oversight contained to 4.4x but could not eliminate
"""

import numpy as np
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_COUNTS = np.array([1, 2, 3, 5, 7, 10, 15, 20, 25])
CAPABILITY_LEVELS = [0.5, 0.7, 0.85, 0.95, 1.0]  # 1.0 = perfect agent
RNG_SEED = 42

# Base implementation cost per agent per task unit (tokens)
BASE_IMPL_COST = 4096

# From Study 3 empirical data
EMPIRICAL_DATA = {
    "single": {"score": 28, "cost": 51.17, "n_agents": 1},
    "hierarchical": {"score": 18, "cost": 50.62, "n_agents": 2},  # collapsed to pseudo-single
    "emergence": {"score": 9, "cost": 43.92, "n_agents": 8},
    "pipeline": {"score": 0, "cost": 1.90, "n_agents": 11},  # budget exhausted on planning
}

# Cost per token (approximate from paper: $57.43 / 7.17M tokens)
COST_PER_TOKEN = 57.43 / 7_166_664


# ---------------------------------------------------------------------------
# Architecture cost models
# ---------------------------------------------------------------------------

class ArchitectureCostModel:
    """
    Models total token cost as: implementation + governance.

    Implementation cost = f(n, capability) -- scales linearly with n,
      inversely with capability (better agents need fewer tokens per task).

    Governance cost = g(n, topology) -- structural overhead that does NOT
      decrease with capability. This is the key insight.
    """

    def __init__(self, name: str, short_name: str, color: str):
        self.name = name
        self.short_name = short_name
        self.color = color

    def implementation_cost(self, n: int, capability: float) -> float:
        """
        Token cost for actual implementation work.
        Scales with n (more agents = more parallel work = more tokens),
        inversely with capability.
        """
        # Perfect agent needs BASE_IMPL_COST tokens per task unit
        # Less capable agents need more attempts/tokens
        cost_per_agent = BASE_IMPL_COST / max(0.1, capability)
        return cost_per_agent * n

    def governance_cost(self, n: int, capability: float) -> float:
        """
        Token cost for coordination overhead.
        KEY: this does NOT decrease with capability.
        It is a function of topology, not agent quality.
        """
        raise NotImplementedError

    def total_cost(self, n: int, capability: float) -> float:
        return self.implementation_cost(n, capability) + self.governance_cost(n, capability)

    def governance_fraction(self, n: int, capability: float) -> float:
        total = self.total_cost(n, capability)
        if total == 0:
            return 0
        return self.governance_cost(n, capability) / total

    def frontier_governance_cost(self, n: int) -> float:
        """
        Theoretical minimum governance overhead -- the frontier.
        Even with perfect agents and perfect prompts, this overhead remains.
        It is the information-theoretic minimum for coordination.
        """
        raise NotImplementedError

    def effective_output(self, n: int, capability: float) -> float:
        """
        Effective output quality, accounting for coordination loss.
        Based on empirical finding: performance inversely correlated
        with coordination complexity.
        """
        # Base output scales with n * capability
        raw_output = n * capability * BASE_IMPL_COST

        # Coordination loss: fraction of output degraded by governance
        gov_frac = self.governance_fraction(n, capability)

        # Data Processing Inequality: each coordination step loses information
        # Integration quality degrades with number of boundaries
        n_boundaries = self._integration_boundaries(n)
        integration_quality = 0.95 ** n_boundaries

        return raw_output * (1 - gov_frac * 0.5) * integration_quality

    def _integration_boundaries(self, n: int) -> int:
        """Number of integration boundaries between agents."""
        raise NotImplementedError


class SingleAgent(ArchitectureCostModel):
    """No coordination overhead -- the control/baseline."""
    def __init__(self):
        super().__init__("Single Agent", "single", "#607D8B")

    def governance_cost(self, n: int, capability: float) -> float:
        return 0  # no coordination needed

    def frontier_governance_cost(self, n: int) -> float:
        return 0

    def _integration_boundaries(self, n: int) -> int:
        return 0


class CentralizedCost(ArchitectureCostModel):
    """Hub-and-spoke coordination."""
    def __init__(self):
        super().__init__("Centralized (Hub-Spoke)", "centralized", "#2196F3")

    def governance_cost(self, n: int, capability: float) -> float:
        # Coordinator must communicate with each agent: O(n)
        # Status broadcasts, task routing, conflict resolution
        links = 2 * (n - 1) if n > 1 else 0
        base = links * 192  # per-link overhead
        # Coordinator bottleneck: context scales with n
        bottleneck = min(n * 512, 32768)  # coordinator context window pressure
        # Conflict resolution: O(n) potential conflicts
        conflicts = n * 0.15 * 512
        return base + bottleneck + conflicts

    def frontier_governance_cost(self, n: int) -> float:
        # Minimum: just status messages (no conflicts, no bottleneck)
        links = 2 * (n - 1) if n > 1 else 0
        return links * 64  # minimal status tokens

    def _integration_boundaries(self, n: int) -> int:
        return n - 1 if n > 1 else 0


class FullyConnectedCost(ArchitectureCostModel):
    """All-to-all communication."""
    def __init__(self):
        super().__init__("Fully Connected", "fully_connected", "#F44336")

    def governance_cost(self, n: int, capability: float) -> float:
        # O(n^2) links, each requiring status synchronization
        links = n * (n - 1) if n > 1 else 0
        base = links * 192
        # Noise floor: with n^2 messages, signal drowns in noise
        # Chen et al.: independent agents amplified errors 17.2x
        noise_amplification = links * 32 * np.log2(max(2, n))
        # Conflict resolution: O(n^2) potential conflicts
        conflicts = links * 0.1 * 512
        return base + noise_amplification + conflicts

    def frontier_governance_cost(self, n: int) -> float:
        # Even the minimum: each pair must sync at least once
        links = n * (n - 1) if n > 1 else 0
        return links * 32

    def _integration_boundaries(self, n: int) -> int:
        return n * (n - 1) // 2 if n > 1 else 0


class HierarchicalCost(ArchitectureCostModel):
    """Tree structure coordination."""
    def __init__(self):
        super().__init__("Hierarchical (Tree)", "hierarchical", "#4CAF50")

    def governance_cost(self, n: int, capability: float) -> float:
        # Tree links: O(n)
        links = 2 * (n - 1) if n > 1 else 0
        depth = max(1, np.ceil(np.log(max(2, n)) / np.log(3)))
        base = links * 192

        # Crawford-Sobel degradation per layer
        # Each layer compresses, requiring retransmission/clarification
        cs_overhead = depth * n * 256

        # Non-decomposition tendency (from paper: Hi-Trust coordinator
        # refused to delegate). Modeled as probability of collapse
        # that wastes the coordination setup
        collapse_waste = 0.3 * depth * 1024  # wasted planning tokens

        return base + cs_overhead + collapse_waste

    def frontier_governance_cost(self, n: int) -> float:
        links = 2 * (n - 1) if n > 1 else 0
        return links * 64

    def _integration_boundaries(self, n: int) -> int:
        return n - 1 if n > 1 else 0


class RingCost(ArchitectureCostModel):
    """Circular communication."""
    def __init__(self):
        super().__init__("Ring", "ring", "#FF9800")

    def governance_cost(self, n: int, capability: float) -> float:
        links = 2 * n if n > 1 else 0
        avg_hops = n / 4.0
        base = links * 192

        # Serial propagation delay: information degrades over hops
        propagation_cost = avg_hops * n * 128

        # Latency overhead: round-trip through ring for global decisions
        latency_cost = n * 256

        return base + propagation_cost + latency_cost

    def frontier_governance_cost(self, n: int) -> float:
        links = 2 * n if n > 1 else 0
        return links * 64

    def _integration_boundaries(self, n: int) -> int:
        return n if n > 1 else 0


class StarMeshCost(ArchitectureCostModel):
    """Coordinator + clustered workers."""
    def __init__(self):
        super().__init__("Star-Mesh Hybrid", "star_mesh", "#9C27B0")

    def governance_cost(self, n: int, capability: float) -> float:
        n_workers = max(0, n - 1)
        n_clusters = max(1, int(np.ceil(n_workers / 3)))
        cluster_size = min(3, n_workers)

        # Coordinator <-> cluster head links
        coord_links = 2 * n_clusters
        # Intra-cluster links
        intra_links = n_clusters * cluster_size * (cluster_size - 1)
        total_links = coord_links + intra_links

        base = total_links * 192

        # Cross-cluster coordination (coordinator relays)
        cross_cluster = n_clusters * (n_clusters - 1) * 256

        # Intra-cluster conflict resolution
        intra_conflicts = intra_links * 0.08 * 512

        return base + cross_cluster + intra_conflicts

    def frontier_governance_cost(self, n: int) -> float:
        n_workers = max(0, n - 1)
        n_clusters = max(1, int(np.ceil(n_workers / 3)))
        cluster_size = min(3, n_workers)
        coord_links = 2 * n_clusters
        intra_links = n_clusters * cluster_size * (cluster_size - 1)
        return (coord_links + intra_links) * 32

    def _integration_boundaries(self, n: int) -> int:
        n_workers = max(0, n - 1)
        n_clusters = max(1, int(np.ceil(n_workers / 3)))
        return n_clusters + n_workers


class GatedPipelineCost(ArchitectureCostModel):
    """
    Sequential gated pipeline (Org Swarm from paper).
    Included for completeness -- this is the architecture that
    consumed its entire budget on planning.
    """
    def __init__(self):
        super().__init__("Gated Pipeline", "pipeline", "#795548")

    def governance_cost(self, n: int, capability: float) -> float:
        # Pipeline stages: each stage is a gate
        n_stages = n  # approximately 1 stage per agent role
        # Each gate requires: input processing + evaluation + verdict
        gate_cost = n_stages * 2048

        # Rejection cycles: 87.5% rejection rate (Study 1)
        # Each rejection restarts from a previous stage
        expected_rejections = n_stages * 0.5  # conservative
        rejection_cost = expected_rejections * n_stages * 1024

        # Backward pipeline oscillation (paper Section 5.1.4)
        oscillation = n_stages * 512

        # Goodhart optimization of gate metrics
        goodhart_waste = n_stages * 768

        return gate_cost + rejection_cost + oscillation + goodhart_waste

    def frontier_governance_cost(self, n: int) -> float:
        # Minimum: just pass through each gate once
        return n * 512

    def _integration_boundaries(self, n: int) -> int:
        return n - 1 if n > 1 else 0


COST_MODELS = [
    SingleAgent(),
    CentralizedCost(),
    FullyConnectedCost(),
    HierarchicalCost(),
    RingCost(),
    StarMeshCost(),
    GatedPipelineCost(),
]


# ---------------------------------------------------------------------------
# Frontier computation
# ---------------------------------------------------------------------------

def compute_scaling_curves() -> dict:
    """
    Compute total cost, implementation cost, governance cost, and frontier
    for each architecture across agent counts and capability levels.
    """
    results = {}

    for model in COST_MODELS:
        model_results = {}
        for cap in CAPABILITY_LEVELS:
            cap_results = {}
            for n in AGENT_COUNTS:
                n = int(n)
                impl = model.implementation_cost(n, cap)
                gov = model.governance_cost(n, cap)
                total = impl + gov
                frontier = model.frontier_governance_cost(n)
                eff_output = model.effective_output(n, cap)

                cap_results[n] = {
                    "n_agents": n,
                    "capability": cap,
                    "implementation_tokens": round(impl, 1),
                    "governance_tokens": round(gov, 1),
                    "total_tokens": round(total, 1),
                    "frontier_governance": round(frontier, 1),
                    "governance_fraction": round(gov / total if total > 0 else 0, 4),
                    "frontier_fraction": round(frontier / (impl + frontier) if (impl + frontier) > 0 else 0, 4),
                    "effective_output": round(eff_output, 1),
                    "cost_efficiency": round(eff_output / total if total > 0 else 0, 4),
                }
            model_results[str(cap)] = cap_results

        results[model.short_name] = {
            "name": model.name,
            "color": model.color,
            "results": model_results,
        }

    return results


def compute_capability_independence() -> dict:
    """
    Demonstrate that governance overhead is independent of capability.

    For each architecture, show that the governance fraction at n=10 agents
    is essentially the same regardless of capability level.
    This is the key finding: coordination overhead is structural.
    """
    n = 10
    results = {}

    for model in COST_MODELS:
        caps = []
        gov_fracs = []
        gov_tokens_list = []
        for cap in np.linspace(0.3, 1.0, 15):
            gov = model.governance_cost(n, float(cap))
            impl = model.implementation_cost(n, float(cap))
            total = gov + impl
            caps.append(float(cap))
            gov_fracs.append(gov / total if total > 0 else 0)
            gov_tokens_list.append(gov)

        # Check: governance tokens should be constant across capabilities
        gov_std = np.std(gov_tokens_list)
        gov_mean = np.mean(gov_tokens_list)

        results[model.short_name] = {
            "name": model.name,
            "capabilities": caps,
            "governance_fractions": [round(f, 4) for f in gov_fracs],
            "governance_tokens": [round(t, 1) for t in gov_tokens_list],
            "governance_tokens_cv": round(gov_std / gov_mean if gov_mean > 0 else 0, 6),
            "is_capability_independent": gov_std / gov_mean < 0.01 if gov_mean > 0 else True,
        }

    return results


def compute_crossover_points() -> dict:
    """
    Find the agent count where governance exceeds implementation.
    This is the practical coordination ceiling.

    From paper: "Multi-agent system designers should default to the fewest
    agents capable of completing the task."
    """
    results = {}

    for model in COST_MODELS:
        if model.short_name == "single":
            results[model.short_name] = {
                "name": model.name,
                "crossover_n": None,
                "note": "No governance overhead",
            }
            continue

        cap = 0.85  # typical capability
        crossover = None
        for n in range(2, 50):
            gov = model.governance_cost(n, cap)
            impl = model.implementation_cost(n, cap)
            if gov > impl and crossover is None:
                crossover = n
                break

        results[model.short_name] = {
            "name": model.name,
            "crossover_n": crossover,
            "governance_at_crossover": round(model.governance_cost(crossover, cap), 1) if crossover else None,
            "implementation_at_crossover": round(model.implementation_cost(crossover, cap), 1) if crossover else None,
        }

    return results


# ---------------------------------------------------------------------------
# Print and save
# ---------------------------------------------------------------------------

def print_summary(scaling: dict, independence: dict, crossovers: dict):
    """Print readable summary."""
    print("\n" + "=" * 90)
    print("FRONTIER ANALYSIS: Capability Scaling vs Coordination Overhead")
    print("=" * 90)

    # Scaling curves at capability = 1.0 (perfect agents)
    print("\n--- Total cost scaling with PERFECT agents (capability=1.0) ---")
    print(f"{'Architecture':<25s} ", end="")
    for n in AGENT_COUNTS:
        print(f"{'n=' + str(n):>10s}", end="")
    print()
    print("-" * (25 + 10 * len(AGENT_COUNTS)))

    for model_key in [m.short_name for m in COST_MODELS]:
        data = scaling[model_key]
        cap_data = data["results"]["1.0"]
        print(f"{data['name']:<25s} ", end="")
        for n in AGENT_COUNTS:
            n = int(n)
            total = cap_data[str(n)] if str(n) in cap_data else cap_data[n]
            print(f"{total['total_tokens']:>10,.0f}", end="")
        print()

    # Governance fraction at perfect capability
    print("\n--- Governance fraction with PERFECT agents ---")
    print(f"{'Architecture':<25s} ", end="")
    for n in AGENT_COUNTS:
        print(f"{'n=' + str(n):>10s}", end="")
    print()
    print("-" * (25 + 10 * len(AGENT_COUNTS)))

    for model_key in [m.short_name for m in COST_MODELS]:
        data = scaling[model_key]
        cap_data = data["results"]["1.0"]
        print(f"{data['name']:<25s} ", end="")
        for n in AGENT_COUNTS:
            n = int(n)
            d = cap_data[str(n)] if str(n) in cap_data else cap_data[n]
            print(f"{d['governance_fraction']:>9.1%} ", end="")
        print()

    # Capability independence
    print("\n--- Capability independence test (n=10) ---")
    print(f"{'Architecture':<25s} {'Gov CV':>8} {'Independent?':>14}")
    print("-" * 50)
    for model_key, data in independence.items():
        cv = data["governance_tokens_cv"]
        indep = data["is_capability_independent"]
        print(f"{data['name']:<25s} {cv:8.4f} {'YES' if indep else 'NO':>14s}")

    # Crossover points
    print("\n--- Governance > Implementation crossover ---")
    print(f"{'Architecture':<25s} {'Crossover n':>12}")
    print("-" * 40)
    for model_key, data in crossovers.items():
        n = data["crossover_n"]
        print(f"{data['name']:<25s} {str(n) if n else 'N/A':>12s}")

    # Key finding
    print("\n" + "=" * 90)
    print("KEY FINDING: Coordination overhead is structural, not a capability issue")
    print("=" * 90)
    print("  Even with perfect agents (zero implementation error):")
    print("  - Fully connected governance grows O(n^2)")
    print("  - Governance fraction INCREASES as capability increases")
    print("    (because implementation gets cheaper, but governance does not)")
    print("  - Frontier (theoretical minimum) is non-zero for all multi-agent architectures")
    print("\n  From paper: 'Coordination overhead is not implementation debt")
    print("  to be paid down with better prompting. It is an information-theoretic")
    print("  cost of distributed cognition.'")


def save_results(scaling: dict, independence: dict, crossovers: dict, output_dir: Path):
    """Save results as JSON."""
    output_path = output_dir / "frontier_analysis_results.json"

    def convert(obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (bool,)):
            return obj
        if isinstance(obj, dict):
            return {str(k): convert(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [convert(v) for v in obj]
        return obj

    combined = {
        "scaling_curves": convert(scaling),
        "capability_independence": convert(independence),
        "crossover_points": convert(crossovers),
        "empirical_reference": EMPIRICAL_DATA,
    }

    with open(output_path, "w") as f:
        json.dump(combined, f, indent=2)
    print(f"\nResults saved to {output_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    output_dir = Path(__file__).parent

    scaling = compute_scaling_curves()
    independence = compute_capability_independence()
    crossovers = compute_crossover_points()

    print_summary(scaling, independence, crossovers)
    save_results(scaling, independence, crossovers, output_dir)

    print("\nFrontier analysis complete.")
