"""Program management — cross-task coherence via explicit management.

Not self-organization — explicit conflict detection and signal propagation
managed by control plane agents.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaskScope:
    """Scope of a single task in the program."""
    task_id: str
    project_dir: str
    affected_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)


@dataclass
class ConflictReport:
    """Detected conflict between two tasks."""
    task_a: str
    task_b: str
    shared_files: list[str]
    conflict_type: str  # "same_file", "shared_module"
    severity: str       # "critical", "warning", "info"
    recommendation: str = ""


class ProgramManager:
    """Manages cross-task coherence for concurrent/sequential tasks."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskScope] = {}

    def register_task(self, scope: TaskScope) -> None:
        """Register a task scope."""
        self._tasks[scope.task_id] = scope

    def update_task_files(
        self,
        task_id: str,
        affected: list[str] | None = None,
        modified: list[str] | None = None,
    ) -> None:
        """Update affected/modified files for a task."""
        if task_id not in self._tasks:
            return
        if affected is not None:
            self._tasks[task_id].affected_files = affected
        if modified is not None:
            self._tasks[task_id].modified_files = modified

    def detect_conflicts(self) -> list[ConflictReport]:
        """Pairwise file overlap detection.

        same_file: two tasks modify the same file → critical
        shared_module: two tasks affect files in the same module → warning
        """
        conflicts = []
        task_ids = list(self._tasks.keys())

        for i in range(len(task_ids)):
            for j in range(i + 1, len(task_ids)):
                a = self._tasks[task_ids[i]]
                b = self._tasks[task_ids[j]]

                # Same-file conflicts (modified in both)
                shared_modified = set(a.modified_files) & set(b.modified_files)
                if shared_modified:
                    conflicts.append(ConflictReport(
                        task_a=a.task_id,
                        task_b=b.task_id,
                        shared_files=sorted(shared_modified),
                        conflict_type="same_file",
                        severity="critical",
                        recommendation="Serialize these tasks or coordinate changes",
                    ))
                    continue  # Don't report shared_module if same_file already found

                # Shared-module conflicts (affected files in same directory)
                a_modules = set(_module_of(f) for f in a.affected_files)
                b_modules = set(_module_of(f) for f in b.affected_files)
                shared_modules = a_modules & b_modules - {""}
                if shared_modules:
                    shared = sorted(shared_modules)
                    conflicts.append(ConflictReport(
                        task_a=a.task_id,
                        task_b=b.task_id,
                        shared_files=shared,
                        conflict_type="shared_module",
                        severity="warning",
                        recommendation="Review for potential interactions",
                    ))

        return conflicts

    def propagate_signal(self, source_task_id: str, signal: str) -> list[str]:
        """Propagate a signal from one task to all others.

        Returns list of task IDs that were notified.
        """
        notified = []
        source = self._tasks.get(source_task_id)
        if not source:
            return notified

        source.signals.append(signal)

        for task_id, scope in self._tasks.items():
            if task_id == source_task_id:
                continue
            # Notify tasks that share files with the source
            source_files = set(source.affected_files + source.modified_files)
            target_files = set(scope.affected_files + scope.modified_files)
            if source_files & target_files:
                notified.append(task_id)

        return notified


def _module_of(filepath: str) -> str:
    """Extract module directory from a file path."""
    parts = Path(filepath).parts
    if len(parts) <= 1:
        return ""
    return str(Path(*parts[:-1]))
