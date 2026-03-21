"""Learnings store + context cascade — durable memory between runs.

Two components:
  LearningsStore    — accumulated lessons from project outcomes (JSONL + LEARNINGS.md)
                      with cross-role routing (backward feedback paths)
  ContextCascade    — hierarchical .swarm/context.md files scoped by directory
                      (repo root → module, most-specific wins)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from swarm.schemas import LearningEntry, LearningExtraction

logger = logging.getLogger(__name__)

# Maps agent names → relevant learning categories (default routing)
AGENT_CATEGORY_MAP: dict[str, list[str]] = {
    "diagnose": ["failure_mode", "repo_convention"],
    "architect": ["architecture_pattern", "repo_convention"],
    "locate": ["file_location", "repo_convention"],
    "execute": ["code_pattern", "repo_convention", "review_feedback"],
    "test": ["test_strategy", "repo_convention"],
    "review": ["review_feedback", "failure_mode", "code_pattern"],
    "integrate": ["architecture_pattern", "failure_mode"],
}

# Valid agent names for target_agents cross-role routing
VALID_AGENTS = {"diagnose", "architect", "locate", "execute", "test", "review", "integrate"}

# Context cascade file name
CASCADE_FILENAME = "context.md"
CASCADE_DIR = ".swarm"


# ── LearningsStore ──────────────────────────────────────────────────


class LearningsStore:
    """Read/write learning entries from JSONL + render LEARNINGS.md."""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._jsonl_path = base_dir / "learnings.jsonl"
        self._md_path = base_dir / "LEARNINGS.md"

    def _load_all(self) -> list[LearningEntry]:
        if not self._jsonl_path.exists():
            return []
        entries = []
        with open(self._jsonl_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(LearningEntry.model_validate_json(line))
        return entries

    def _save_all(self, entries: list[LearningEntry]) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        with open(self._jsonl_path, "w") as f:
            for entry in entries:
                f.write(entry.model_dump_json() + "\n")

    def _append(self, entry: LearningEntry) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        with open(self._jsonl_path, "a") as f:
            f.write(entry.model_dump_json() + "\n")

    def add_learning(
        self,
        lesson: str,
        category: str,
        confidence: float,
        source_project_ids: list[str],
        repo: str = "",
        scope: str = "",
        target_agents: list[str] | None = None,
    ) -> LearningEntry:
        entry = LearningEntry(
            id=uuid4().hex[:12],
            lesson=lesson,
            category=category,
            confidence=confidence,
            source_project_ids=source_project_ids,
            repo=repo,
            scope=scope,
            target_agents=target_agents or [],
            created_at=datetime.now(),
        )
        self._append(entry)
        return entry

    def record_validation(self, project_id: str, success: bool) -> int:
        entries = self._load_all()
        updated = 0
        for entry in entries:
            if project_id in entry.source_project_ids:
                if success:
                    entry.times_validated += 1
                    entry.last_validated = datetime.now()
                else:
                    entry.times_contradicted += 1
                updated += 1
        if updated:
            self._save_all(entries)
        return updated

    def for_agent(self, agent_name: str, repo: str = "", scope: str = "", max_tokens: int = 800) -> str:
        categories = AGENT_CATEGORY_MAP.get(agent_name, [])
        entries = self._load_all()
        if not entries:
            return ""

        relevant = []
        for e in entries:
            if e.is_decayed or e.is_stale:
                continue
            if e.repo and e.repo != repo:
                continue
            if e.scope and scope and not scope.startswith(e.scope):
                continue
            category_match = e.category in categories if categories else False
            target_match = agent_name in e.target_agents
            if category_match or target_match:
                relevant.append(e)

        if not relevant:
            return ""

        relevant.sort(
            key=lambda e: (e.times_validated - e.times_contradicted, e.confidence),
            reverse=True,
        )

        char_budget = max_tokens * 4
        lines = ["## Learnings (validated across prior projects)", ""]

        by_category: dict[str, list[LearningEntry]] = {}
        for entry in relevant:
            by_category.setdefault(entry.category, []).append(entry)

        total_chars = sum(len(l) for l in lines)
        for category, cat_entries in by_category.items():
            header = f"**{category.replace('_', ' ').title()}:**"
            total_chars += len(header) + 1
            if total_chars > char_budget:
                break
            lines.append(header)
            for entry in cat_entries:
                validated_tag = f" [validated {entry.times_validated}x]" if entry.times_validated > 0 else ""
                scope_tag = f" (scope: {entry.scope})" if entry.scope else ""
                line = f"- {entry.lesson}{scope_tag}{validated_tag}"
                total_chars += len(line) + 1
                if total_chars > char_budget:
                    break
                lines.append(line)
            lines.append("")

        return "\n".join(lines)

    def prune_stale_learnings(self, stale_days: int = 30) -> int:
        """Bump contradiction count for stale entries. Returns count pruned."""
        entries = self._load_all()
        pruned = 0
        for entry in entries:
            if entry.is_stale and not entry.is_decayed:
                entry.times_contradicted += 1
                pruned += 1
        if pruned:
            self._save_all(entries)
        return pruned

    def promote_validated_learnings(self, min_validations: int = 5) -> list[LearningEntry]:
        entries = self._load_all()
        promoted = []

        for entry in entries:
            if not entry.scope:
                continue
            if entry.is_decayed:
                continue
            if entry.times_validated < min_validations:
                continue

            already_promoted = any(
                e.lesson == entry.lesson and e.scope == "" and e.repo == entry.repo
                and e.category == entry.category
                for e in entries
            )
            if already_promoted:
                continue

            new_entry = self.add_learning(
                lesson=entry.lesson,
                category=entry.category,
                confidence=entry.confidence,
                source_project_ids=entry.source_project_ids,
                repo=entry.repo,
                scope="",
                target_agents=entry.target_agents or None,
            )
            new_entry.times_validated = entry.times_validated
            new_entry.last_validated = entry.last_validated
            promoted.append(new_entry)

        if promoted:
            all_entries = self._load_all()
            self._save_all(all_entries)

        return promoted


# ── ContextCascade ──────────────────────────────────────────────────


class ContextCascade:
    """Hierarchical .swarm/context.md files scoped by directory.

    Resolution: walk from the most specific path upward to repo root.
    Merge all files found, most-specific-first.
    """

    def resolve(self, repo_path: Path, file_paths: list[str] | None = None) -> str:
        contexts: list[tuple[int, str, str]] = []

        repo_ctx = self._read_context(repo_path)
        if repo_ctx:
            contexts.append((0, "", repo_ctx))

        if file_paths:
            dirs_seen: set[str] = set()
            for fp in file_paths:
                parts = Path(fp).parts
                for depth in range(len(parts), 0, -1):
                    dir_path = "/".join(parts[:depth])
                    if dir_path in dirs_seen:
                        continue
                    dirs_seen.add(dir_path)
                    full_path = repo_path / dir_path
                    ctx = self._read_context(full_path)
                    if ctx:
                        contexts.append((depth, dir_path, ctx))

        if not contexts:
            return ""

        contexts.sort(key=lambda c: c[0], reverse=True)

        lines = ["## Codebase Context (scoped by directory)", ""]
        for depth, scope, content in contexts:
            scope_label = scope if scope else "(repo root)"
            lines.append(f"### {scope_label}")
            lines.append(content.strip())
            lines.append("")

        return "\n".join(lines)

    def write_context(self, repo_path: Path, scope: str, content: str) -> Path:
        if scope:
            target = repo_path / scope / CASCADE_DIR / CASCADE_FILENAME
        else:
            target = repo_path / CASCADE_DIR / CASCADE_FILENAME

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def _read_context(self, dir_path: Path) -> str | None:
        ctx_file = dir_path / CASCADE_DIR / CASCADE_FILENAME
        if ctx_file.is_file():
            return ctx_file.read_text()
        return None
