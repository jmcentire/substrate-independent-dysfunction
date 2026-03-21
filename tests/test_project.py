"""Tests for ProjectManager — init, load, save, resume."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm.project import ProjectManager
from swarm.schemas import ProjectRun, StageRecord


class TestProjectInit:
    def test_scaffolds_directory(self, tmp_path):
        proj = tmp_path / "myproj"
        pm = ProjectManager(proj)
        pm.init(repo="/tmp/repo", formation="thorough")

        assert proj.exists()
        assert (proj / "task.md").exists()
        assert (proj / "swarm.yaml").exists()
        assert (proj / ".swarm").is_dir()
        assert (proj / ".swarm" / "stages").is_dir()
        assert (proj / ".swarm" / "context").is_dir()
        assert (proj / "questions" / "pending").is_dir()
        assert (proj / "questions" / "answered").is_dir()
        assert (proj / "artifacts" / "patches").is_dir()

    def test_task_md_has_template(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()
        content = pm.load_task()
        assert "# Task" in content
        assert "Describe" in content

    def test_swarm_yaml_has_config(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init(repo="/tmp/repo", formation="thorough", budget=10.0)
        config = pm.load_config()
        assert config.repo == "/tmp/repo"
        assert config.formation == "thorough"
        assert config.budget == 10.0

    def test_init_idempotent(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()
        # Write custom task
        pm.task_path.write_text("Custom task")
        # Re-init should not overwrite
        pm.init()
        assert pm.load_task() == "Custom task"


class TestProjectState:
    def test_no_state_initially(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()
        assert pm.has_state() is False

    def test_save_and_load_state(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        run = pm.create_run()
        run.formation = "standard"
        run.current_stage_index = 3
        pm.save_state(run)

        assert pm.has_state() is True
        loaded = pm.load_state()
        assert loaded.id == run.id
        assert loaded.formation == "standard"
        assert loaded.current_stage_index == 3

    def test_load_missing_state_raises(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()
        with pytest.raises(FileNotFoundError):
            pm.load_state()

    def test_load_missing_task_raises(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        with pytest.raises(FileNotFoundError):
            pm.load_task()

    def test_clear_state(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        # Create state, stage outputs, audit, components
        run = pm.create_run()
        run.formation = "epic"
        pm.save_state(run)
        pm.save_stage_output("diagnose", {"x": 1})
        pm.save_stage_output("decompose", {"y": 2})
        pm.append_audit("diagnose", True, "ok")
        pm.save_component_output("db", "architect", {"z": 3})

        assert pm.has_state()

        pm.clear_state()

        assert not pm.has_state()
        assert pm.load_stage_output("diagnose") is None
        assert pm.load_stage_output("decompose") is None
        assert pm.load_audit() == []
        assert pm.load_component_output("db", "architect") is None
        # Task and config should NOT be cleared
        assert pm.task_path.exists()
        assert pm.config_path.exists()

    def test_state_round_trip_with_stages(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        run = pm.create_run()
        run.record_stage(StageRecord(
            stage="diagnose", passed=True, confidence=0.9,
            tokens_in=100, tokens_out=50, cost_usd=0.01,
        ))
        run.pause("Break point after diagnose")
        pm.save_state(run)

        loaded = pm.load_state()
        assert loaded.status == "paused"
        assert loaded.pause_reason == "Break point after diagnose"
        assert len(loaded.stages) == 1
        assert loaded.stages[0].stage == "diagnose"
        assert loaded.total_tokens == 150


class TestStageOutputs:
    def test_save_and_load(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        data = {"confidence": 0.9, "summary": "Test task"}
        pm.save_stage_output("diagnose", data)

        loaded = pm.load_stage_output("diagnose")
        assert loaded["confidence"] == 0.9
        assert loaded["summary"] == "Test task"

    def test_save_pydantic_model(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        from swarm.schemas import DiagnoseResult
        result = DiagnoseResult(
            task_type="feature", complexity="simple", confidence=0.9,
            language="python", summary="Add auth", reasoning="Standard",
        )
        pm.save_stage_output("diagnose", result)

        loaded = pm.load_stage_output("diagnose")
        assert loaded["task_type"] == "feature"
        assert loaded["confidence"] == 0.9

    def test_load_missing_returns_none(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()
        assert pm.load_stage_output("nonexistent") is None

    def test_list_stage_outputs(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        pm.save_stage_output("diagnose", {"x": 1})
        pm.save_stage_output("architect", {"x": 2})

        outputs = pm.list_stage_outputs()
        assert "architect" in outputs
        assert "diagnose" in outputs


class TestAudit:
    def test_append_and_load(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        pm.append_audit("diagnose", True, "Task analyzed")
        pm.append_audit("architect", True, "Plan created")

        entries = pm.load_audit()
        assert len(entries) == 2
        assert entries[0]["stage"] == "diagnose"
        assert entries[1]["stage"] == "architect"
        assert entries[0]["passed"] is True

    def test_empty_audit(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()
        assert pm.load_audit() == []


class TestComponents:
    def test_list_components_empty(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()
        assert pm.list_components() == []

    def test_list_components_with_results(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        pm.save_component_output("auth", "build_result", {
            "component_id": "auth", "status": "completed",
            "stages_completed": ["architect", "locate", "execute"],
            "issues": [], "files_changed": ["src/auth.py"],
        })
        pm.save_component_output("db", "build_result", {
            "component_id": "db", "status": "failed",
            "stages_completed": ["architect"],
            "issues": ["locate: too many files"],
            "files_changed": [],
        })

        results = pm.list_components()
        assert len(results) == 2
        ids = {r["component_id"] for r in results}
        assert ids == {"auth", "db"}

        auth = next(r for r in results if r["component_id"] == "auth")
        assert auth["status"] == "completed"

        db = next(r for r in results if r["component_id"] == "db")
        assert db["status"] == "failed"

    def test_list_components_incomplete(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        # Component dir exists but no build_result
        comp_dir = pm._components_dir() / "partial"
        comp_dir.mkdir(parents=True)
        (comp_dir / "architect.json").write_text('{"x": 1}')

        results = pm.list_components()
        assert len(results) == 1
        assert results[0]["component_id"] == "partial"
        assert results[0]["status"] == "incomplete"

    def test_clear_components(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        pm.save_component_output("auth", "build_result", {
            "component_id": "auth", "status": "failed",
            "stages_completed": [], "issues": [], "files_changed": [],
        })
        pm.save_component_output("db", "build_result", {
            "component_id": "db", "status": "completed",
            "stages_completed": [], "issues": [], "files_changed": [],
        })
        pm.save_component_output("api", "build_result", {
            "component_id": "api", "status": "completed",
            "stages_completed": [], "issues": [], "files_changed": [],
        })

        cleared = pm.clear_components(["auth", "api"])
        assert set(cleared) == {"auth", "api"}

        # auth and api should be gone, db should remain
        results = pm.list_components()
        assert len(results) == 1
        assert results[0]["component_id"] == "db"

    def test_clear_components_nonexistent(self, tmp_path):
        pm = ProjectManager(tmp_path / "p")
        pm.init()

        cleared = pm.clear_components(["doesnt_exist"])
        assert cleared == []
