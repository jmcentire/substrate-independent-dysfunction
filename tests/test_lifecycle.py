"""Tests for lifecycle — run creation, stage recording, formatting."""

from __future__ import annotations

import pytest

from swarm.lifecycle import create_run, format_run_summary, record_stage
from swarm.schemas import ProjectRun


class TestCreateRun:
    def test_creates_active_run(self):
        run = create_run("/tmp/proj")
        assert run.status == "active"
        assert run.project_dir == "/tmp/proj"
        assert len(run.id) == 12
        assert run.total_tokens == 0

    def test_unique_ids(self):
        r1 = create_run("/tmp/p1")
        r2 = create_run("/tmp/p2")
        assert r1.id != r2.id


class TestRecordStage:
    def test_records_stage(self):
        run = create_run("/tmp/proj")
        record_stage(run, "diagnose", True, confidence=0.9, detail="OK",
                     tokens_in=100, tokens_out=50, cost_usd=0.01)
        assert len(run.stages) == 1
        assert run.stages[0].stage == "diagnose"
        assert run.total_tokens == 150
        assert run.total_cost_usd == 0.01

    def test_records_multiple_stages(self):
        run = create_run("/tmp/proj")
        record_stage(run, "diagnose", True, tokens_in=100, tokens_out=50, cost_usd=0.01)
        record_stage(run, "architect", True, tokens_in=200, tokens_out=100, cost_usd=0.02)
        assert len(run.stages) == 2
        assert run.total_tokens == 450
        assert run.total_cost_usd == pytest.approx(0.03)


class TestFormatRunSummary:
    def test_active_run(self):
        run = create_run("/tmp/proj")
        run.formation = "standard"
        text = format_run_summary(run)
        assert run.id in text
        assert "active" in text
        assert "standard" in text

    def test_paused_run(self):
        run = create_run("/tmp/proj")
        run.pause("Break point")
        text = format_run_summary(run)
        assert "paused" in text
        assert "Break point" in text

    def test_with_stages(self):
        run = create_run("/tmp/proj")
        record_stage(run, "diagnose", True)
        record_stage(run, "architect", False, detail="Failed")
        text = format_run_summary(run)
        assert "diagnose(ok)" in text
        assert "architect(FAIL)" in text
