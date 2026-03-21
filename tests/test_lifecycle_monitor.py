"""Tests for LifecycleMonitor — system health checks."""

from __future__ import annotations

from datetime import datetime

import pytest

from swarm.lifecycle_monitor import HealthReport, LifecycleMonitor
from swarm.memory import LearningsStore
from swarm.schemas import ProjectRun


def _make_run(status="completed", formation="standard") -> ProjectRun:
    return ProjectRun(
        id="test123", project_dir="/tmp/proj",
        status=status, formation=formation,
    )


class TestHealthReport:
    def test_healthy_when_no_warnings(self):
        r = HealthReport()
        assert r.healthy is True

    def test_unhealthy_with_warnings(self):
        r = HealthReport()
        r.warnings.append("Something wrong")
        assert r.healthy is False

    def test_format_healthy(self):
        r = HealthReport()
        r.metrics["total"] = 10
        text = r.format()
        assert "HEALTHY" in text
        assert "total" in text

    def test_format_with_warnings(self):
        r = HealthReport()
        r.warnings.append("High failure rate")
        text = r.format()
        assert "WARNING" in text
        assert "High failure rate" in text


class TestLifecycleMonitor:
    def test_empty_state(self, tmp_path):
        monitor = LifecycleMonitor(tmp_path)
        report = monitor.check_health([])
        assert report.healthy is True
        assert "cold-start" in report.info[0]

    def test_with_successful_runs(self, tmp_path):
        monitor = LifecycleMonitor(tmp_path)
        runs = [_make_run("completed") for _ in range(5)]
        report = monitor.check_health(runs)
        assert report.metrics["completed"] == 5

    def test_high_pause_rate_warning(self, tmp_path):
        monitor = LifecycleMonitor(tmp_path)
        runs = [_make_run("paused") for _ in range(4)] + [_make_run("completed") for _ in range(2)]
        report = monitor.check_health(runs)
        assert any("pause rate" in w.lower() for w in report.warnings)

    def test_formation_metrics(self, tmp_path):
        monitor = LifecycleMonitor(tmp_path)
        runs = [
            _make_run("completed", "quick"),
            _make_run("completed", "standard"),
            _make_run("completed", "standard"),
        ]
        report = monitor.check_health(runs)
        assert "formations" in report.metrics

    def test_learning_health_no_learnings(self, tmp_path):
        monitor = LifecycleMonitor(tmp_path)
        report = monitor.check_health([])
        assert report.metrics.get("total_learnings") == 0

    def test_learning_health_with_learnings(self, tmp_path):
        store = LearningsStore(tmp_path)
        store.add_learning("L1", "code_pattern", 0.8, ["p1"])
        store.add_learning("L2", "test_strategy", 0.7, ["p2"])

        monitor = LifecycleMonitor(tmp_path)
        report = monitor.check_health([])
        assert report.metrics["total_learnings"] == 2
        assert report.metrics["categories_covered"] == 2

    def test_high_contradiction_warning(self, tmp_path):
        store = LearningsStore(tmp_path)
        for i in range(10):
            store.add_learning(f"L{i}", "code_pattern", 0.5, [f"p{i}"])

        # Contradict most of them
        entries = store._load_all()
        for entry in entries[:7]:
            entry.times_contradicted = 5
        store._save_all(entries)

        monitor = LifecycleMonitor(tmp_path)
        report = monitor.check_health([])
        assert any("contradiction" in w.lower() for w in report.warnings)
