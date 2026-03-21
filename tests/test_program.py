"""Tests for program.py — cross-task coherence management."""

from __future__ import annotations

import pytest

from swarm.program import ConflictReport, ProgramManager, TaskScope


class TestTaskScope:
    def test_creation(self):
        scope = TaskScope(task_id="t1", project_dir="/tmp/t1")
        assert scope.task_id == "t1"
        assert scope.affected_files == []
        assert scope.modified_files == []

    def test_with_files(self):
        scope = TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/a.py"], modified_files=["src/a.py"],
        )
        assert len(scope.affected_files) == 1


class TestProgramManager:
    def test_register_task(self):
        pm = ProgramManager()
        scope = TaskScope(task_id="t1", project_dir="/tmp/t1")
        pm.register_task(scope)
        assert "t1" in pm._tasks

    def test_update_task_files(self):
        pm = ProgramManager()
        scope = TaskScope(task_id="t1", project_dir="/tmp/t1")
        pm.register_task(scope)
        pm.update_task_files("t1", affected=["a.py"], modified=["a.py"])
        assert pm._tasks["t1"].affected_files == ["a.py"]

    def test_update_nonexistent_task(self):
        pm = ProgramManager()
        pm.update_task_files("missing", affected=["a.py"])  # No error

    def test_no_conflicts(self):
        pm = ProgramManager()
        pm.register_task(TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/auth/login.py"], modified_files=["src/auth/login.py"],
        ))
        pm.register_task(TaskScope(
            task_id="t2", project_dir="/tmp/t2",
            affected_files=["src/billing/invoice.py"], modified_files=["src/billing/invoice.py"],
        ))
        conflicts = pm.detect_conflicts()
        assert len(conflicts) == 0

    def test_same_file_conflict(self):
        pm = ProgramManager()
        pm.register_task(TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/a.py"], modified_files=["src/a.py"],
        ))
        pm.register_task(TaskScope(
            task_id="t2", project_dir="/tmp/t2",
            affected_files=["src/a.py"], modified_files=["src/a.py"],
        ))
        conflicts = pm.detect_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "same_file"
        assert conflicts[0].severity == "critical"

    def test_shared_module_conflict(self):
        pm = ProgramManager()
        pm.register_task(TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/auth/login.py"],
        ))
        pm.register_task(TaskScope(
            task_id="t2", project_dir="/tmp/t2",
            affected_files=["src/auth/register.py"],
        ))
        conflicts = pm.detect_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "shared_module"
        assert conflicts[0].severity == "warning"

    def test_three_way_conflict(self):
        pm = ProgramManager()
        for i in range(3):
            pm.register_task(TaskScope(
                task_id=f"t{i}", project_dir=f"/tmp/t{i}",
                affected_files=["src/shared.py"], modified_files=["src/shared.py"],
            ))
        conflicts = pm.detect_conflicts()
        assert len(conflicts) == 3  # t0-t1, t0-t2, t1-t2

    def test_propagate_signal_to_overlapping_tasks(self):
        pm = ProgramManager()
        pm.register_task(TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/a.py"],
        ))
        pm.register_task(TaskScope(
            task_id="t2", project_dir="/tmp/t2",
            affected_files=["src/a.py"],
        ))
        pm.register_task(TaskScope(
            task_id="t3", project_dir="/tmp/t3",
            affected_files=["src/b.py"],
        ))
        notified = pm.propagate_signal("t1", "escalation: conflict detected")
        assert "t2" in notified
        assert "t3" not in notified

    def test_propagate_signal_nonexistent_source(self):
        pm = ProgramManager()
        notified = pm.propagate_signal("missing", "signal")
        assert notified == []

    def test_propagate_signal_no_overlap(self):
        pm = ProgramManager()
        pm.register_task(TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/a.py"],
        ))
        pm.register_task(TaskScope(
            task_id="t2", project_dir="/tmp/t2",
            affected_files=["src/b.py"],
        ))
        notified = pm.propagate_signal("t1", "signal")
        assert notified == []

    def test_signal_stored_on_source(self):
        pm = ProgramManager()
        pm.register_task(TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/a.py"],
        ))
        pm.propagate_signal("t1", "test signal")
        assert "test signal" in pm._tasks["t1"].signals

    def test_same_file_takes_precedence_over_module(self):
        """If both same_file and shared_module apply, only same_file is reported."""
        pm = ProgramManager()
        pm.register_task(TaskScope(
            task_id="t1", project_dir="/tmp/t1",
            affected_files=["src/auth/login.py"],
            modified_files=["src/auth/login.py"],
        ))
        pm.register_task(TaskScope(
            task_id="t2", project_dir="/tmp/t2",
            affected_files=["src/auth/register.py"],
            modified_files=["src/auth/login.py"],
        ))
        conflicts = pm.detect_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "same_file"
