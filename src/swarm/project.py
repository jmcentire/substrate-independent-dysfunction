"""Project directory management — init, load, save, resume.

The project directory is the unit of work:
  proj/
  ├── task.md          # Human-written task
  ├── swarm.yaml       # Config
  ├── .swarm/
  │   ├── state.json   # ProjectRun state
  │   ├── signals.jsonl
  │   ├── audit.jsonl
  │   ├── context/
  │   │   └── cascade.md
  │   └── stages/
  │       ├── diagnose.json
  │       └── ...
  ├── questions/
  │   ├── pending/
  │   └── answered/
  └── artifacts/
      └── patches/
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import yaml

from swarm.config import ProjectConfig, load_project_config
from swarm.schemas import ProjectRun, StageRecord

logger = logging.getLogger(__name__)

SWARM_DIR = ".swarm"
STATE_FILE = "state.json"
SIGNALS_FILE = "signals.jsonl"
AUDIT_FILE = "audit.jsonl"


class ProjectManager:
    """Manages project directory lifecycle — init, load state, save state."""

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir).resolve()
        self._swarm_dir = self.project_dir / SWARM_DIR
        self._stages_dir = self._swarm_dir / "stages"
        self._context_dir = self._swarm_dir / "context"

    @property
    def task_path(self) -> Path:
        return self.project_dir / "task.md"

    @property
    def config_path(self) -> Path:
        return self.project_dir / "swarm.yaml"

    @property
    def state_path(self) -> Path:
        return self._swarm_dir / STATE_FILE

    @property
    def audit_path(self) -> Path:
        return self._swarm_dir / AUDIT_FILE

    @property
    def signals_path(self) -> Path:
        return self._swarm_dir / SIGNALS_FILE

    def init(
        self,
        repo: str = "",
        formation: str = "standard",
        io_mode: str = "interactive",
        break_points: list[str] | None = None,
        budget: float = 5.00,
    ) -> None:
        """Scaffold a new project directory."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self._swarm_dir.mkdir(exist_ok=True)
        self._stages_dir.mkdir(exist_ok=True)
        self._context_dir.mkdir(exist_ok=True)
        (self.project_dir / "questions" / "pending").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "questions" / "answered").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "artifacts" / "patches").mkdir(parents=True, exist_ok=True)

        # Write task.md template
        if not self.task_path.exists():
            self.task_path.write_text(
                "# Task\n\n"
                "Describe your programming task here.\n\n"
                "## Context\n\n"
                "Any relevant context, constraints, or requirements.\n"
            )

        # Write swarm.yaml
        if not self.config_path.exists():
            config = {
                "repo": repo,
                "formation": formation,
                "io_mode": io_mode,
                "break_points": break_points or ["review", "submit"],
                "budget": budget,
                "commands": {},
                "submit": {"mode": "none"},
            }
            with open(self.config_path, "w") as f:
                yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        logger.info("Initialized project: %s", self.project_dir)

    def load_task(self) -> str:
        """Read the task.md file."""
        if not self.task_path.exists():
            raise FileNotFoundError(f"No task.md found in {self.project_dir}")
        return self.task_path.read_text()

    def load_config(self) -> ProjectConfig:
        """Load the project config from swarm.yaml."""
        return load_project_config(self.project_dir)

    def has_state(self) -> bool:
        """Check if there's a saved pipeline state."""
        return self.state_path.exists()

    def load_state(self) -> ProjectRun:
        """Load the pipeline state from .swarm/state.json."""
        if not self.state_path.exists():
            raise FileNotFoundError(f"No state file found: {self.state_path}")
        return ProjectRun.model_validate_json(self.state_path.read_text())

    def save_state(self, run: ProjectRun) -> None:
        """Save pipeline state to .swarm/state.json."""
        self._swarm_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(run.model_dump_json(indent=2))

    def clear_state(self) -> None:
        """Remove saved pipeline state, stage outputs, and component outputs."""
        import shutil

        if self.state_path.exists():
            self.state_path.unlink()
        if self.audit_path.exists():
            self.audit_path.unlink()
        if self.signals_path.exists():
            self.signals_path.unlink()
        if self._stages_dir.exists():
            shutil.rmtree(self._stages_dir)
            self._stages_dir.mkdir()
        components_dir = self._components_dir()
        if components_dir.exists():
            shutil.rmtree(components_dir)

    def create_run(self) -> ProjectRun:
        """Create a new ProjectRun for this project."""
        return ProjectRun(
            id=uuid4().hex[:12],
            project_dir=str(self.project_dir),
            status="active",
            created_at=datetime.now(),
        )

    def save_stage_output(self, stage_name: str, data: dict | object) -> Path:
        """Save a stage's output to .swarm/stages/{stage}.json."""
        self._stages_dir.mkdir(parents=True, exist_ok=True)
        path = self._stages_dir / f"{stage_name}.json"
        if hasattr(data, "model_dump_json"):
            path.write_text(data.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(data, indent=2, default=str))
        return path

    def load_stage_output(self, stage_name: str) -> dict | None:
        """Load a stage's output from .swarm/stages/{stage}.json."""
        path = self._stages_dir / f"{stage_name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def append_audit(self, stage: str, passed: bool, detail: str = "") -> None:
        """Append an entry to the audit trail."""
        self._swarm_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "stage": stage,
            "passed": passed,
            "detail": detail,
        }
        with open(self.audit_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def load_audit(self) -> list[dict]:
        """Load audit trail entries."""
        if not self.audit_path.exists():
            return []
        entries = []
        with open(self.audit_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def list_stage_outputs(self) -> list[str]:
        """List available stage outputs."""
        if not self._stages_dir.exists():
            return []
        return sorted(p.stem for p in self._stages_dir.glob("*.json"))

    # ── Component-level persistence (for epic pipelines) ────────────

    def _components_dir(self) -> Path:
        return self._swarm_dir / "components"

    def save_component_output(self, component_id: str, stage: str, data: dict | object) -> Path:
        """Save a component's stage output to .swarm/components/{id}/{stage}.json."""
        comp_dir = self._components_dir() / component_id
        comp_dir.mkdir(parents=True, exist_ok=True)
        path = comp_dir / f"{stage}.json"
        if hasattr(data, "model_dump_json"):
            path.write_text(data.model_dump_json(indent=2))
        else:
            path.write_text(json.dumps(data, indent=2, default=str))
        return path

    def load_component_output(self, component_id: str, stage: str) -> dict | None:
        """Load a component's stage output from .swarm/components/{id}/{stage}.json."""
        path = self._components_dir() / component_id / f"{stage}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def save_decomposition_plan(self, plan: dict | object) -> Path:
        """Save the decomposition plan to .swarm/stages/decompose.json."""
        return self.save_stage_output("decompose", plan)

    def list_components(self) -> list[dict]:
        """List all components and their build status.

        Returns list of {id, status, stages_completed, issues, files_changed}.
        """
        components_dir = self._components_dir()
        if not components_dir.exists():
            return []

        results = []
        for comp_dir in sorted(components_dir.iterdir()):
            if not comp_dir.is_dir():
                continue
            build_path = comp_dir / "build_result.json"
            if build_path.exists():
                data = json.loads(build_path.read_text())
                results.append(data)
            else:
                # Component dir exists but no build result — in progress or incomplete
                results.append({
                    "component_id": comp_dir.name,
                    "status": "incomplete",
                    "stages_completed": [],
                    "issues": [],
                    "files_changed": [],
                })
        return results

    def clear_components(self, component_ids: list[str]) -> list[str]:
        """Clear specific component outputs so they get rebuilt.

        Returns list of component IDs that were actually cleared.
        """
        import shutil
        cleared = []
        components_dir = self._components_dir()
        for cid in component_ids:
            comp_dir = components_dir / cid
            if comp_dir.exists():
                shutil.rmtree(comp_dir)
                cleared.append(cid)
        return cleared
