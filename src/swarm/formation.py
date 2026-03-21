"""Formation definitions + mechanical selector.

Five formations: quick, standard, thorough, epic, custom.
Plus COMPONENT_PIPELINE — template for per-component build loops.
Selection is post-diagnose, mechanical (no LLM call).
"""

from __future__ import annotations

from swarm.config import ProjectConfig
from swarm.schemas import DiagnoseResult, Formation, FormationStage


# ── Formation definitions ────────────────────────────────────────────

QUICK = Formation(
    name="quick",
    stages=[
        FormationStage(name="diagnose"),
        FormationStage(name="execute"),
        FormationStage(name="verify"),
        FormationStage(name="submit"),
    ],
    max_review_loops=0,
)

STANDARD = Formation(
    name="standard",
    stages=[
        FormationStage(name="diagnose"),
        FormationStage(name="architect", architect_sub_passes=["mvp", "simplicity"]),
        FormationStage(name="locate"),
        FormationStage(name="execute"),
        FormationStage(name="test"),
        FormationStage(name="verify"),
        FormationStage(name="review", mode="adversarial"),
        FormationStage(name="submit", break_point="pause"),
    ],
    max_review_loops=1,
    max_architect_review_loops=1,
)

THOROUGH = Formation(
    name="thorough",
    stages=[
        FormationStage(name="diagnose"),
        FormationStage(name="architect", break_point="pause",
                       architect_sub_passes=["mvp", "simplicity", "integrity", "reuse", "standards"]),
        FormationStage(name="locate"),
        FormationStage(name="execute", competitors=2),
        FormationStage(name="test"),
        FormationStage(name="verify"),
        FormationStage(name="review", mode="adversarial", break_point="pause",
                       review_sub_passes=["structural", "logic", "consistency", "blast_radius"]),
        FormationStage(name="integrate"),
        FormationStage(name="submit", break_point="pause"),
    ],
    max_review_loops=2,
    max_architect_review_loops=2,
)

EPIC = Formation(
    name="epic",
    stages=[
        FormationStage(name="diagnose"),
        FormationStage(name="decompose", break_point="pause"),
    ],
    max_review_loops=1,
)

COMPONENT_PIPELINE = Formation(
    name="_component",
    stages=[
        FormationStage(name="architect",
                       architect_sub_passes=["mvp", "simplicity", "reuse"]),
        FormationStage(name="locate"),
        FormationStage(name="execute"),
        FormationStage(name="test"),
        FormationStage(name="verify"),
        FormationStage(name="review", mode="adversarial"),
    ],
    max_review_loops=3,
    max_architect_review_loops=2,
)

FORMATIONS: dict[str, Formation] = {
    "quick": QUICK,
    "standard": STANDARD,
    "thorough": THOROUGH,
    "epic": EPIC,
}

# All valid stage names
VALID_STAGES = frozenset([
    "diagnose", "decompose", "architect", "locate", "execute",
    "test", "verify", "review", "integrate", "submit",
])


def build_custom_formation(
    stages: list[str],
    break_points: list[str] | None = None,
    max_review_loops: int = 0,
) -> Formation:
    """Build a custom formation from stage names."""
    bp_set = set(break_points or [])
    formation_stages = []
    for name in stages:
        if name not in VALID_STAGES:
            raise ValueError(f"Unknown stage: {name}. Valid: {sorted(VALID_STAGES)}")
        mode = "adversarial" if name == "review" else "cooperation"
        bp = "pause" if name in bp_set else "none"
        formation_stages.append(FormationStage(name=name, mode=mode, break_point=bp))

    return Formation(
        name="custom",
        stages=formation_stages,
        max_review_loops=max_review_loops,
    )


def select_formation(diagnose: DiagnoseResult, config: ProjectConfig) -> Formation:
    """Mechanically select a formation based on diagnose output.

    Rules:
    - Config override (formation field) takes precedence
    - Custom formation uses custom_stages from config
    - Epic complexity → epic formation (decomposition)
    - Trivial + high confidence (>= 0.85) → quick
    - Simple complexity → standard
    - Moderate/complex → thorough
    """
    if config.formation == "custom" and config.custom_stages:
        return build_custom_formation(
            config.custom_stages,
            break_points=config.break_points,
        )

    # Explicit formation override (non-empty, non-auto)
    if config.formation and config.formation != "auto" and config.formation in FORMATIONS:
        return FORMATIONS[config.formation]

    # Mechanical selection
    if diagnose.complexity == "epic":
        return EPIC

    if diagnose.complexity == "trivial" and diagnose.confidence >= 0.85:
        return QUICK

    if diagnose.complexity in ("trivial", "simple"):
        return STANDARD

    # moderate, complex
    return THOROUGH
