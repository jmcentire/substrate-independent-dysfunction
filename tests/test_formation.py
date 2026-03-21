"""Tests for formation definitions + selector."""

from __future__ import annotations

import pytest

from swarm.config import ProjectConfig
from swarm.formation import (
    COMPONENT_PIPELINE,
    EPIC,
    FORMATIONS,
    QUICK,
    STANDARD,
    THOROUGH,
    VALID_STAGES,
    build_custom_formation,
    select_formation,
)
from swarm.schemas import DiagnoseResult, FormationStage


def _make_diagnose(complexity="simple", confidence=0.8, task_type="feature"):
    return DiagnoseResult(
        task_type=task_type, complexity=complexity, confidence=confidence,
        language="python", summary="Test", reasoning="Test",
    )


class TestFormationDefinitions:
    def test_quick_has_4_stages(self):
        assert len(QUICK.stages) == 4
        assert QUICK.stages[0].name == "diagnose"
        assert QUICK.stages[-1].name == "submit"
        assert QUICK.max_review_loops == 0

    def test_standard_has_review(self):
        names = [s.name for s in STANDARD.stages]
        assert "review" in names
        assert STANDARD.stages[-1].break_point == "pause"

    def test_thorough_has_all_stages(self):
        names = [s.name for s in THOROUGH.stages]
        assert "integrate" in names
        assert THOROUGH.max_review_loops == 2

    def test_thorough_architect_break_point(self):
        architect = next(s for s in THOROUGH.stages if s.name == "architect")
        assert architect.break_point == "pause"

    def test_review_is_adversarial(self):
        for formation in FORMATIONS.values():
            for stage in formation.stages:
                if stage.name == "review":
                    assert stage.mode == "adversarial"

    def test_all_formations_in_dict(self):
        assert "quick" in FORMATIONS
        assert "standard" in FORMATIONS
        assert "thorough" in FORMATIONS
        assert "epic" in FORMATIONS

    def test_epic_has_decompose(self):
        names = [s.name for s in EPIC.stages]
        assert "diagnose" in names
        assert "decompose" in names
        assert EPIC.stages[-1].break_point == "pause"

    def test_component_pipeline_has_6_stages(self):
        assert len(COMPONENT_PIPELINE.stages) == 6
        names = [s.name for s in COMPONENT_PIPELINE.stages]
        assert names == ["architect", "locate", "execute", "test", "verify", "review"]
        assert COMPONENT_PIPELINE.max_review_loops == 3

    def test_component_pipeline_review_is_adversarial(self):
        review = next(s for s in COMPONENT_PIPELINE.stages if s.name == "review")
        assert review.mode == "adversarial"


class TestSelectFormation:
    def test_trivial_high_confidence_selects_quick(self):
        d = _make_diagnose(complexity="trivial", confidence=0.9)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "quick"

    def test_trivial_low_confidence_selects_standard(self):
        d = _make_diagnose(complexity="trivial", confidence=0.7)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "standard"

    def test_simple_selects_standard(self):
        d = _make_diagnose(complexity="simple", confidence=0.8)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "standard"

    def test_moderate_selects_thorough(self):
        d = _make_diagnose(complexity="moderate", confidence=0.8)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "thorough"

    def test_complex_selects_thorough(self):
        d = _make_diagnose(complexity="complex", confidence=0.8)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "thorough"

    def test_epic_selects_epic(self):
        d = _make_diagnose(complexity="epic", confidence=0.8)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "epic"

    def test_config_override(self):
        d = _make_diagnose(complexity="trivial", confidence=0.99)
        c = ProjectConfig(formation="thorough")
        f = select_formation(d, c)
        assert f.name == "thorough"

    def test_custom_formation(self):
        d = _make_diagnose()
        c = ProjectConfig(
            formation="custom",
            custom_stages=["diagnose", "execute", "verify", "submit"],
        )
        f = select_formation(d, c)
        assert f.name == "custom"
        assert len(f.stages) == 4


class TestBuildCustomFormation:
    def test_builds_from_stage_names(self):
        f = build_custom_formation(["diagnose", "execute", "submit"])
        assert len(f.stages) == 3
        assert f.name == "custom"

    def test_invalid_stage_raises(self):
        with pytest.raises(ValueError, match="Unknown stage"):
            build_custom_formation(["diagnose", "nonexistent"])

    def test_break_points(self):
        f = build_custom_formation(
            ["diagnose", "execute", "review", "submit"],
            break_points=["review"],
        )
        review = next(s for s in f.stages if s.name == "review")
        assert review.break_point == "pause"
        assert review.mode == "adversarial"

    def test_review_is_adversarial(self):
        f = build_custom_formation(["diagnose", "review", "submit"])
        review = next(s for s in f.stages if s.name == "review")
        assert review.mode == "adversarial"

    def test_max_review_loops(self):
        f = build_custom_formation(["diagnose", "submit"], max_review_loops=3)
        assert f.max_review_loops == 3


class TestArchitectSubPasses:
    def test_standard_architect_has_sub_passes(self):
        architect = next(s for s in STANDARD.stages if s.name == "architect")
        assert architect.architect_sub_passes == ["mvp", "simplicity"]
        assert STANDARD.max_architect_review_loops == 1

    def test_thorough_architect_has_5_sub_passes(self):
        architect = next(s for s in THOROUGH.stages if s.name == "architect")
        assert architect.architect_sub_passes == ["mvp", "simplicity", "integrity", "reuse", "standards"]
        assert THOROUGH.max_architect_review_loops == 2

    def test_component_pipeline_architect_has_3_sub_passes(self):
        architect = next(s for s in COMPONENT_PIPELINE.stages if s.name == "architect")
        assert architect.architect_sub_passes == ["mvp", "simplicity", "reuse"]
        assert COMPONENT_PIPELINE.max_architect_review_loops == 2

    def test_quick_has_no_architect_sub_passes(self):
        """QUICK formation has no architect stage at all."""
        names = [s.name for s in QUICK.stages]
        assert "architect" not in names

    def test_epic_has_no_architect_sub_passes(self):
        """EPIC formation has no architect stage (decompose handles it)."""
        names = [s.name for s in EPIC.stages]
        assert "architect" not in names


class TestValidStages:
    def test_all_10_stages(self):
        assert len(VALID_STAGES) == 10
        expected = {"diagnose", "decompose", "architect", "locate", "execute",
                    "test", "verify", "review", "integrate", "submit"}
        assert VALID_STAGES == expected
