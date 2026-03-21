"""Config loading — SwarmConfig (global) + ProjectConfig (per-project).

SwarmConfig: global defaults from config.yaml.
ProjectConfig: per-project from swarm.yaml (repo, formation, io_mode, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class SwarmConfig:
    """Global swarm configuration — defaults for all projects."""
    model: str = "claude-opus-4-6"
    default_formation: str = "auto"
    default_io_mode: str = "interactive"
    default_budget: float = 10.00
    default_stage_models: dict[str, str] = field(default_factory=dict)
    default_stage_backends: dict[str, str] = field(default_factory=dict)

    # Thresholds
    diagnose_min_confidence: float = 0.6
    locate_min_confidence: float = 0.6
    locate_max_files: int = 30
    review_min_confidence: float = 0.7
    integrate_min_confidence: float = 0.7

    # Control plane
    control_plane_enabled: bool = False
    control_plane_model: str = "claude-haiku-4-5-20251001"
    health_check_interval: int = 5

    # Guardian
    max_diff_lines: int = 500
    max_files_changed: int = 20
    max_deletions: int = 200
    forbidden_paths: list[str] = field(default_factory=lambda: [
        "*.env*", "*.key", "*.pem", "*.lock",
    ])
    forbidden_patterns: list[str] = field(default_factory=lambda: [
        r"process\.exit", r"rm\s+-rf",
        r"DROP\s+TABLE", r"TRUNCATE",
        r"eval\(", r"exec\(",
    ])


@dataclass
class ProjectConfig:
    """Per-project configuration from swarm.yaml."""
    repo: str = ""
    formation: str = "auto"
    io_mode: str = "interactive"
    model: str = ""
    break_points: list[str] = field(default_factory=lambda: ["review", "submit"])
    budget: float = 10.00
    commands: dict[str, list[str]] = field(default_factory=dict)
    submit_mode: str = "none"
    branch_prefix: str = "swarm/"
    draft_pr: bool = True
    deploy_command: list[str] = field(default_factory=list)
    custom_stages: list[str] = field(default_factory=list)
    stage_models: dict[str, str] = field(default_factory=dict)
    stage_backends: dict[str, str] = field(default_factory=dict)
    backend: str = "anthropic"

    @property
    def repo_path(self) -> Path:
        return Path(self.repo).expanduser().resolve()

    @property
    def test_command(self) -> list[str]:
        return self.commands.get("test", [])

    @property
    def lint_command(self) -> list[str]:
        return self.commands.get("lint", [])

    @property
    def typecheck_command(self) -> list[str]:
        return self.commands.get("typecheck", [])


def load_swarm_config(config_path: str | Path | None = None) -> SwarmConfig:
    """Load global config from config.yaml."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        return SwarmConfig()

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    return SwarmConfig(
        model=raw.get("model", SwarmConfig.model),
        default_formation=raw.get("default_formation", SwarmConfig.default_formation),
        default_io_mode=raw.get("default_io_mode", SwarmConfig.default_io_mode),
        default_budget=raw.get("default_budget", SwarmConfig.default_budget),
        diagnose_min_confidence=raw.get("diagnose_min_confidence", SwarmConfig.diagnose_min_confidence),
        locate_min_confidence=raw.get("locate_min_confidence", SwarmConfig.locate_min_confidence),
        locate_max_files=raw.get("locate_max_files", SwarmConfig.locate_max_files),
        review_min_confidence=raw.get("review_min_confidence", SwarmConfig.review_min_confidence),
        integrate_min_confidence=raw.get("integrate_min_confidence", SwarmConfig.integrate_min_confidence),
        max_diff_lines=raw.get("max_diff_lines", SwarmConfig.max_diff_lines),
        max_files_changed=raw.get("max_files_changed", SwarmConfig.max_files_changed),
        max_deletions=raw.get("max_deletions", SwarmConfig.max_deletions),
        forbidden_paths=raw.get("forbidden_paths", SwarmConfig().forbidden_paths),
        forbidden_patterns=raw.get("forbidden_patterns", SwarmConfig().forbidden_patterns),
        default_stage_models=raw.get("stage_models", {}),
        default_stage_backends=raw.get("stage_backends", {}),
    )


def load_project_config(project_dir: str | Path) -> ProjectConfig:
    """Load per-project config from swarm.yaml."""
    project_dir = Path(project_dir)
    config_path = project_dir / "swarm.yaml"

    if not config_path.exists():
        return ProjectConfig()

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    commands = raw.get("commands", {})
    if isinstance(commands, dict):
        # Ensure all values are lists
        commands = {k: v if isinstance(v, list) else [str(v)] for k, v in commands.items()}

    submit = raw.get("submit", {})

    return ProjectConfig(
        repo=raw.get("repo", ""),
        formation=raw.get("formation", "auto"),
        io_mode=raw.get("io_mode", "interactive"),
        model=raw.get("model", ""),
        break_points=raw.get("break_points", ["review", "submit"]),
        budget=raw.get("budget", 10.00),
        commands=commands,
        submit_mode=submit.get("mode", raw.get("submit_mode", "none")),
        branch_prefix=submit.get("branch_prefix", "swarm/"),
        draft_pr=submit.get("draft_pr", True),
        deploy_command=submit.get("deploy_command", []),
        custom_stages=raw.get("custom_stages", []),
        stage_models=raw.get("stage_models", {}),
        stage_backends=raw.get("stage_backends", {}),
        backend=raw.get("backend", "anthropic"),
    )
