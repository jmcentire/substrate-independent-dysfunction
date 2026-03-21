"""Tests for LearningsStore and ContextCascade."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from swarm.memory import (
    AGENT_CATEGORY_MAP,
    VALID_AGENTS,
    ContextCascade,
    LearningsStore,
)
from swarm.schemas import LearningEntry


class TestLearningsStore:
    def test_add_learning(self, tmp_path):
        store = LearningsStore(tmp_path)
        entry = store.add_learning(
            lesson="Use Option.getOrElse",
            category="code_pattern",
            confidence=0.8,
            source_project_ids=["p1"],
        )
        assert entry.lesson == "Use Option.getOrElse"
        assert entry.id

    def test_load_all(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("Lesson 1", "code_pattern", 0.8, ["p1"])
        store.add_learning("Lesson 2", "test_strategy", 0.7, ["p2"])
        entries = store._load_all()
        assert len(entries) == 2

    def test_for_agent_returns_relevant(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("Use pattern X", "code_pattern", 0.8, ["p1"])
        store.add_learning("Test tip Y", "test_strategy", 0.7, ["p2"])

        text = store.for_agent("execute")
        assert "Use pattern X" in text

        text = store.for_agent("test")
        assert "Test tip Y" in text

    def test_for_agent_empty(self, tmp_path):
        store = LearningsStore(tmp_path)
        text = store.for_agent("execute")
        assert text == ""

    def test_for_agent_excludes_decayed(self, tmp_path):
        store = LearningsStore(tmp_path)
        entry = store.add_learning("Bad pattern", "code_pattern", 0.5, ["p1"])
        # Manually decay it
        entries = store._load_all()
        entries[0].times_contradicted = 10
        store._save_all(entries)

        text = store.for_agent("execute")
        assert "Bad pattern" not in text

    def test_for_agent_cross_role(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning(
            "Diagnose should check this",
            "review_feedback", 0.8, ["p1"],
            target_agents=["diagnose"],
        )
        text = store.for_agent("diagnose")
        assert "Diagnose should check this" in text

    def test_record_validation(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("Test lesson", "code_pattern", 0.8, ["p1"])
        updated = store.record_validation("p1", success=True)
        assert updated == 1
        entries = store._load_all()
        assert entries[0].times_validated == 1

    def test_record_contradiction(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("Test lesson", "code_pattern", 0.8, ["p1"])
        store.record_validation("p1", success=False)
        entries = store._load_all()
        assert entries[0].times_contradicted == 1

    def test_promote_validated_learnings(self, tmp_path):
        store = LearningsStore(tmp_path)
        entry = store.add_learning(
            "Scoped lesson", "code_pattern", 0.8, ["p1"],
            scope="src/auth",
        )
        # Validate enough times
        entries = store._load_all()
        entries[0].times_validated = 5
        store._save_all(entries)

        promoted = store.promote_validated_learnings(min_validations=3)
        assert len(promoted) == 1
        assert promoted[0].scope == ""  # Promoted to repo-level
        assert promoted[0].lesson == "Scoped lesson"

    def test_promote_skips_already_promoted(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("Lesson", "code_pattern", 0.8, ["p1"], scope="src/auth")
        store.add_learning("Lesson", "code_pattern", 0.8, ["p1"], scope="")  # Already global

        entries = store._load_all()
        entries[0].times_validated = 5
        store._save_all(entries)

        promoted = store.promote_validated_learnings(min_validations=3)
        assert len(promoted) == 0

    def test_repo_filter(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("Repo-specific", "code_pattern", 0.8, ["p1"], repo="pricing")
        store.add_learning("Global lesson", "code_pattern", 0.7, ["p2"])

        text = store.for_agent("execute", repo="pricing")
        assert "Repo-specific" in text
        assert "Global lesson" in text

        text = store.for_agent("execute", repo="payment")
        assert "Repo-specific" not in text
        assert "Global lesson" in text


class TestLearningsStaleness:
    def test_new_entry_is_not_stale(self, tmp_path):
        store = LearningsStore(tmp_path)
        entry = store.add_learning("Fresh lesson", "code_pattern", 0.8, ["p1"])
        entries = store._load_all()
        assert not entries[0].is_stale

    def test_old_entry_is_stale(self, tmp_path):
        from datetime import timedelta
        store = LearningsStore(tmp_path)
        entry = store.add_learning("Old lesson", "code_pattern", 0.8, ["p1"])
        entries = store._load_all()
        entries[0].created_at = entries[0].created_at - timedelta(days=60)
        store._save_all(entries)
        entries = store._load_all()
        assert entries[0].is_stale

    def test_recently_validated_not_stale(self, tmp_path):
        from datetime import datetime, timedelta
        store = LearningsStore(tmp_path)
        entry = store.add_learning("Lesson", "code_pattern", 0.8, ["p1"])
        entries = store._load_all()
        entries[0].created_at = entries[0].created_at - timedelta(days=60)
        entries[0].last_validated = datetime.now()
        store._save_all(entries)
        entries = store._load_all()
        assert not entries[0].is_stale

    def test_for_agent_excludes_stale(self, tmp_path):
        from datetime import timedelta
        store = LearningsStore(tmp_path)
        store.add_learning("Stale lesson", "code_pattern", 0.8, ["p1"])
        entries = store._load_all()
        entries[0].created_at = entries[0].created_at - timedelta(days=60)
        store._save_all(entries)
        text = store.for_agent("execute")
        assert "Stale lesson" not in text

    def test_prune_stale_learnings(self, tmp_path):
        from datetime import timedelta
        store = LearningsStore(tmp_path)
        store.add_learning("Stale", "code_pattern", 0.8, ["p1"])
        store.add_learning("Fresh", "code_pattern", 0.8, ["p2"])
        entries = store._load_all()
        entries[0].created_at = entries[0].created_at - timedelta(days=60)
        store._save_all(entries)
        pruned = store.prune_stale_learnings()
        assert pruned == 1
        entries = store._load_all()
        assert entries[0].times_contradicted == 1
        assert entries[1].times_contradicted == 0

    def test_promote_at_5_validations(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("Lesson", "code_pattern", 0.8, ["p1"], scope="src/auth")
        entries = store._load_all()
        entries[0].times_validated = 4
        store._save_all(entries)
        promoted = store.promote_validated_learnings(min_validations=5)
        assert len(promoted) == 0  # Not enough

        entries = store._load_all()
        entries[0].times_validated = 5
        store._save_all(entries)
        promoted = store.promote_validated_learnings(min_validations=5)
        assert len(promoted) == 1


class TestContextCascade:
    def test_empty_repo(self, tmp_path):
        cascade = ContextCascade()
        result = cascade.resolve(tmp_path)
        assert result == ""

    def test_repo_level_context(self, tmp_path):
        cascade = ContextCascade()
        cascade.write_context(tmp_path, "", "Use Effect-TS patterns")
        result = cascade.resolve(tmp_path)
        assert "Effect-TS" in result

    def test_module_level_context(self, tmp_path):
        cascade = ContextCascade()
        cascade.write_context(tmp_path, "", "Repo-level context")
        cascade.write_context(tmp_path, "src/auth", "Auth-specific context")

        result = cascade.resolve(tmp_path, file_paths=["src/auth/login.py"])
        assert "Auth-specific" in result
        assert "Repo-level" in result

    def test_most_specific_first(self, tmp_path):
        cascade = ContextCascade()
        cascade.write_context(tmp_path, "", "Repo level")
        cascade.write_context(tmp_path, "src", "Src level")
        cascade.write_context(tmp_path, "src/auth", "Auth level")

        result = cascade.resolve(tmp_path, file_paths=["src/auth/login.py"])
        auth_pos = result.find("Auth level")
        repo_pos = result.find("Repo level")
        assert auth_pos < repo_pos  # Most specific first

    def test_no_file_paths(self, tmp_path):
        cascade = ContextCascade()
        cascade.write_context(tmp_path, "", "Repo context")
        cascade.write_context(tmp_path, "src/auth", "Auth context")

        result = cascade.resolve(tmp_path)
        assert "Repo context" in result
        assert "Auth context" not in result  # Not included without file_paths


class TestAgentCategoryMap:
    def test_all_agents_covered(self):
        for agent in VALID_AGENTS:
            assert agent in AGENT_CATEGORY_MAP

    def test_valid_categories(self):
        valid_categories = {
            "architecture_pattern", "code_pattern", "file_location",
            "test_strategy", "review_feedback", "repo_convention", "failure_mode",
        }
        for agent, categories in AGENT_CATEGORY_MAP.items():
            for cat in categories:
                assert cat in valid_categories, f"{agent} has unknown category: {cat}"
