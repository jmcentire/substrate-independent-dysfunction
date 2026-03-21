"""Integration tests — mock-based end-to-end pipeline tests.

Tests the full formation-driven pipeline flow with mocked LLM calls.
Validates stage ordering, signal processing, gate logic, and state management.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarm.budget import BudgetTracker
from swarm.config import ProjectConfig, SwarmConfig
from swarm.formation import COMPONENT_PIPELINE, EPIC, QUICK, STANDARD, THOROUGH, select_formation
from swarm.gates import diagnose_gate, locate_gate, review_gate, verify_gate
from swarm.guardian import GuardianAgent
from swarm.lifecycle import create_run, record_stage
from swarm.manager import PipelineManager, PipelineState, SignalBus
from swarm.project import ProjectManager
from swarm.schemas import (
    ArchitectResult,
    ArchitectStep,
    ClassifiedIssue,
    Component,
    ComponentBuildResult,
    ComponentMapResult,
    Contract,
    ContractSetResult,
    DecomposeDecision,
    DecomposeQuestion,
    DecomposeQuestionsResult,
    DecompositionPlan,
    DiagnoseResult,
    ExecuteResult,
    FileAction,
    FileLocation,
    Formation,
    FormationStage,
    GateResult,
    GuardianResult,
    ImplementationOrderResult,
    ImplementationStep,
    IntegrateResult,
    IssueClassification,
    LocateResult,
    ReviewEscalationResult,
    ReviewSubVerdict,
    ReviewVerdict,
    SubmitResult,
    TestFile,
    TestResult,
    VerifyResult,
)


# ── Test helpers ─────────────────────────────────────────────────────


def _diagnose(complexity="simple", confidence=0.8):
    return DiagnoseResult(
        task_type="feature", complexity=complexity, confidence=confidence,
        language="python", framework="pytest", summary="Add login",
        reasoning="Straightforward task",
    )


def _architect():
    return ArchitectResult(
        approach="Modular", steps=[ArchitectStep(description="Add endpoint")],
        affected_files=["src/auth.py"], reasoning="Clean approach", confidence=0.85,
    )


def _locate():
    return LocateResult(
        files=[FileLocation(path="src/auth.py", relevance="primary", reasoning="Main file")],
        confidence=0.8, search_strategy="grep",
    )


def _execute():
    return ExecuteResult(
        files=[FileAction(path="src/auth.py", action="modify", original="pass", content="return user")],
        explanation="Fix login", confidence=0.9,
    )


def _tests():
    return TestResult(
        test_files=[TestFile(path="tests/test_auth.py", content="def test(): pass")],
        test_strategy="Unit", confidence=0.8,
    )


def _verify(ok=True):
    return VerifyResult(compiles=ok, lint_clean=ok, tests_passing=1 if ok else 0, tests_total=1)


def _review(approve=True):
    return ReviewVerdict(
        verdict="approve" if approve else "reject",
        confidence=0.85, issues=[] if approve else ["Bug found"],
        reasoning="Review", risks=[],
    )


def _integrate(coherent=True):
    return IntegrateResult(coherent=coherent, confidence=0.9, reasoning="Consistent")


def _escalation_result(decision="approve", confidence=0.85):
    return ReviewEscalationResult(
        decision=decision, confidence=confidence,
        rationale="Code is adequate", conditions=["Watch for edge cases"],
    )


def _classification(factual=None, subjective=None):
    """Build a mock IssueClassification."""
    issues = []
    for f in (factual or []):
        issues.append(ClassifiedIssue(issue=f, category="factual", evidence="proven"))
    for s in (subjective or []):
        issues.append(ClassifiedIssue(issue=s, category="subjective"))
    return IssueClassification(issues=issues, reasoning="Classified")


# ── Full pipeline flow tests ─────────────────────────────────────────


class TestPipelineFlow:
    """Test the pipeline flows through stages correctly."""

    def test_diagnose_selects_formation(self):
        """Diagnose result determines formation selection."""
        # Trivial + high confidence → quick
        d = _diagnose(complexity="trivial", confidence=0.95)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "quick"

        # Simple → standard
        d = _diagnose(complexity="simple", confidence=0.8)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "standard"

        # Moderate → thorough
        d = _diagnose(complexity="moderate", confidence=0.8)
        f = select_formation(d, ProjectConfig(formation="auto"))
        assert f.name == "thorough"

    def test_diagnose_gate_passes_epic_for_decomposition(self):
        """Epic complexity passes gate — decomposition required."""
        d = _diagnose(complexity="epic")
        gate = diagnose_gate(d, SwarmConfig())
        assert gate.passed is True
        assert "decomposition" in gate.reason.lower()

    def test_locate_gate_blocks_no_files(self):
        """Locate gate rejects if no files found."""
        loc = LocateResult(files=[], confidence=0.8, search_strategy="x")
        gate = locate_gate(loc, SwarmConfig())
        assert gate.passed is False

    def test_verify_gate_blocks_compile_fail(self):
        """Verify gate blocks if compilation fails."""
        v = _verify(ok=False)
        gate = verify_gate(v)
        assert gate.passed is False

    def test_review_gate_blocks_rejection(self):
        """Review gate blocks if reviewer rejects."""
        r = _review(approve=False)
        gate = review_gate(r, SwarmConfig())
        assert gate.passed is False


class TestSignalProcessing:
    """Test signal bus across pipeline stages."""

    def test_signals_propagate(self):
        """Signals from agents are collected by the bus."""
        bus = SignalBus()
        result = _diagnose()
        result.consternation = "Something feels off"
        bus.collect_from_result("diagnose", result)
        assert len(bus.signals) == 1

    def test_escalation_pauses_pipeline(self):
        """Escalation signal pauses the pipeline."""
        bus = SignalBus()
        result = _diagnose()
        result.escalation = "Need human"
        bus.collect_from_result("diagnose", result)
        decision = bus.should_continue()
        assert decision.action == "pause"

    def test_two_consternations_pause(self):
        """Two consternation signals from different stages pause."""
        bus = SignalBus()
        d = _diagnose()
        d.consternation = "Hmm"
        bus.collect_from_result("diagnose", d)

        l = _locate()
        l.consternation = "Weird"
        bus.collect_from_result("locate", l)

        decision = bus.should_continue()
        assert decision.action == "pause"

    def test_single_consternation_continues(self):
        """Single consternation continues with warning."""
        bus = SignalBus()
        d = _diagnose()
        d.consternation = "Minor concern"
        bus.collect_from_result("diagnose", d)
        decision = bus.should_continue()
        assert decision.action == "continue"


class TestGuardianIntegration:
    """Test guardian in pipeline context."""

    def test_guardian_passes_clean_change(self):
        g = GuardianAgent()
        exe = _execute()
        result = g.check(exe, _tests(), SwarmConfig())
        assert result.passed is True

    def test_guardian_blocks_secret(self):
        g = GuardianAgent()
        exe = ExecuteResult(
            files=[FileAction(path="a.py", action="create",
                              content='api_key = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"')],
            explanation="x", confidence=0.9,
        )
        result = g.check(exe, None, SwarmConfig())
        assert result.passed is False


class TestProjectStateManagement:
    """Test state persistence and resume."""

    def test_save_and_resume_state(self, tmp_path):
        """Pipeline state persists across runs for resume."""
        pm = ProjectManager(tmp_path / "proj")
        pm.init()

        run = pm.create_run()
        run.formation = "standard"
        record_stage(run, "diagnose", True, 0.9, "Task analyzed", 100, 50, 0.01)
        run.pause("Break point after diagnose")
        run.current_stage_index = 1
        pm.save_state(run)

        # Simulate resume
        loaded = pm.load_state()
        assert loaded.status == "paused"
        assert loaded.current_stage_index == 1
        assert loaded.formation == "standard"
        assert len(loaded.stages) == 1

    def test_stage_outputs_persist(self, tmp_path):
        """Stage outputs are saved and loadable."""
        pm = ProjectManager(tmp_path / "proj")
        pm.init()

        diagnose = _diagnose()
        pm.save_stage_output("diagnose", diagnose)

        loaded = pm.load_stage_output("diagnose")
        assert loaded["task_type"] == "feature"
        assert loaded["complexity"] == "simple"

    def test_audit_trail(self, tmp_path):
        """Audit trail captures stage events."""
        pm = ProjectManager(tmp_path / "proj")
        pm.init()

        pm.append_audit("diagnose", True, "Task analyzed")
        pm.append_audit("architect", True, "Plan created")
        pm.append_audit("locate", False, "No files found")

        audit = pm.load_audit()
        assert len(audit) == 3
        assert audit[2]["passed"] is False


class TestFormationStageOrdering:
    """Verify formation stage ordering is correct."""

    def test_quick_skips_architect_and_review(self):
        names = [s.name for s in QUICK.stages]
        assert "architect" not in names
        assert "review" not in names
        assert names == ["diagnose", "execute", "verify", "submit"]

    def test_standard_includes_review(self):
        names = [s.name for s in STANDARD.stages]
        assert "review" in names
        assert names.index("review") > names.index("execute")

    def test_thorough_includes_integrate(self):
        names = [s.name for s in THOROUGH.stages]
        assert "integrate" in names
        assert names.index("integrate") > names.index("review")

    def test_thorough_has_review_loops(self):
        assert THOROUGH.max_review_loops == 2

    def test_standard_submit_has_break_point(self):
        submit = next(s for s in STANDARD.stages if s.name == "submit")
        assert submit.break_point == "pause"

    def test_thorough_architect_has_break_point(self):
        architect = next(s for s in THOROUGH.stages if s.name == "architect")
        assert architect.break_point == "pause"


class TestBudgetIntegration:
    """Test budget tracking in pipeline context."""

    def test_budget_tracks_across_stages(self):
        budget = BudgetTracker(per_project_cap=5.0)
        budget.set_model_pricing("claude-sonnet-4-5-20250929")
        budget.start_project()

        # Simulate two stages
        budget.record_tokens(1000, 500)
        budget.record_tokens(2000, 1000)

        assert budget.project_spend > 0
        assert budget.project_tokens == (3000, 1500)

    def test_budget_cap_stops_pipeline(self):
        budget = BudgetTracker(per_project_cap=0.001)
        budget.set_model_pricing("claude-opus-4-6")
        budget.start_project()

        result = budget.record_tokens(10000, 5000)
        assert result is False  # Budget exceeded


# ── PipelineManager tests ──────────────────────────────────────────


def _mock_agent_returns(**overrides):
    """Build a mock agent whose assess() returns stage-appropriate results."""
    defaults = {
        "DiagnoseResult": (_diagnose(), 100, 50),
        "ArchitectResult": (_architect(), 100, 50),
        "LocateResult": (_locate(), 100, 50),
        "ExecuteResult": (_execute(), 100, 50),
        "TestResult": (_tests(), 100, 50),
        "ReviewVerdict": (_review(), 100, 50),
        "ReviewSubVerdict": (ReviewSubVerdict(
            pass_type="", verdict="approve", confidence=0.8,
            issues=[], reasoning="Mock sub-verdict",
        ), 50, 25),
        "IntegrateResult": (_integrate(), 100, 50),
    }
    defaults.update(overrides)

    agent = MagicMock()

    async def fake_assess(schema, prompt, system, **kwargs):
        name = schema.__name__
        return defaults.get(name, (MagicMock(), 10, 5))

    agent.assess = AsyncMock(side_effect=fake_assess)
    agent.close = AsyncMock()
    return agent


def _make_pm_and_run(tmp_path):
    """Create a ProjectManager and ProjectRun for testing."""
    pm = ProjectManager(tmp_path / "proj")
    pm.init()
    (tmp_path / "proj" / "task.md").write_text("Fix the login bug")
    run = pm.create_run()
    return pm, run


@pytest.mark.asyncio
async def test_pipeline_manager_diagnose_only(tmp_path):
    """PipelineManager runs diagnose and selects formation."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    # Mock all downstream agent modules
    with patch("swarm.formation.select_formation", return_value=QUICK), \
         patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))):
        # Also mock execute/verify/submit since quick formation uses them
        with patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
             patch.object(PipelineManager, "_handle_verify", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
             patch.object(PipelineManager, "_handle_submit", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):
            mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                                  BudgetTracker(), agent)
            await mgr.run()

    assert run.formation == "quick"
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_pipeline_manager_signal_pauses(tmp_path):
    """Escalation signal pauses the pipeline."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    diag = _diagnose()
    diag.escalation = "Need human input"

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(diag, 100, 50))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    assert run.status == "paused"
    assert "escalation" in run.pause_reason.lower() or "signal" in run.pause_reason.lower()


@pytest.mark.asyncio
async def test_pipeline_manager_gate_failure(tmp_path):
    """Low confidence diagnose fails at gate."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    diag = _diagnose(complexity="simple", confidence=0.3)

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(diag, 100, 50))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    assert run.status == "failed"


@pytest.mark.asyncio
async def test_pipeline_manager_standard_stages(tmp_path):
    """Standard formation executes all 8 stages."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes", new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=STANDARD):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    assert run.formation == "standard"
    # Should have stages recorded
    assert len(run.stages) >= 2


@pytest.mark.asyncio
async def test_pipeline_manager_review_loopback(tmp_path):
    """Review reject triggers re-execute loop."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    call_count = {"review": 0}

    async def mock_review_full(*args, **kwargs):
        call_count["review"] += 1
        if call_count["review"] == 1:
            return _review(approve=False), 100, 50
        return _review(approve=True), 100, 50

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="architect"),
            FormationStage(name="locate"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=2,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(side_effect=mock_review_full)), \
         patch("swarm.executor.submit_changes", new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    assert call_count["review"] == 2  # First reject, then approve


@pytest.mark.asyncio
async def test_pipeline_manager_review_max_exceeded_escalates(tmp_path):
    """Exceeding max review loops triggers escalation instead of failure."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=1,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(approve=False), 100, 50))), \
         patch("swarm.agents.review.escalate_to_project",
               new=AsyncMock(return_value=(_escalation_result(decision="approve", confidence=0.85), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Should complete via escalation, not fail
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_pipeline_manager_state_persisted(tmp_path):
    """State is persisted between stages."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.formation.select_formation", return_value=QUICK), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    # State should be saved
    assert pm.has_state()
    loaded = pm.load_state()
    assert loaded.status == "completed"


@pytest.mark.asyncio
async def test_pipeline_manager_thorough_formation(tmp_path):
    """Thorough formation includes integrate stage."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(complexity="moderate"), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate", new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes", new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=THOROUGH):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    assert run.formation == "thorough"
    stage_names = [s.stage for s in run.stages]
    assert "integrate" in stage_names


@pytest.mark.asyncio
async def test_pipeline_manager_quick_skips_stages(tmp_path):
    """Quick formation skips architect, locate, test, review, integrate."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.formation.select_formation", return_value=QUICK), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    assert run.status == "completed"
    assert run.formation == "quick"
    stage_names = [s.stage for s in run.stages]
    assert "architect" not in stage_names
    assert "review" not in stage_names


# ── Competition mode tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_competition_mode(tmp_path):
    """Competition mode runs N candidates and selects winner."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    execute_calls = {"count": 0}
    async def mock_execute(*args, **kwargs):
        execute_calls["count"] += 1
        return _execute(), 100, 50

    competition_formation = Formation(
        name="thorough",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute", competitors=2),
            FormationStage(name="verify"),
            FormationStage(name="submit"),
        ],
        max_review_loops=0,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(side_effect=mock_execute)), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.executor.submit_changes", new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=competition_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Execute should have been called twice (2 competitors)
    assert execute_calls["count"] == 2


@pytest.mark.asyncio
async def test_pipeline_competition_single_candidate_no_competition(tmp_path):
    """No competition when competitors < 2."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    execute_calls = {"count": 0}
    async def mock_execute(*args, **kwargs):
        execute_calls["count"] += 1
        return _execute(), 100, 50

    no_comp = Formation(
        name="quick",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute", competitors=0),
            FormationStage(name="verify"),
            FormationStage(name="submit"),
        ],
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(side_effect=mock_execute)), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.executor.submit_changes", new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=no_comp):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    assert execute_calls["count"] == 1


# ── Perturbation annealing wiring tests ────────────────────────────


@pytest.mark.asyncio
async def test_perturbation_anneals_on_gate(tmp_path):
    """Perturbation engine anneals success/failure based on gates."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.formation.select_formation", return_value=QUICK), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        initial_temp = mgr._perturbation.temperature
        await mgr.run()

    # After passing gates, temperature should have cooled
    assert mgr._perturbation.temperature < initial_temp


# ── Well-formedness wiring tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_well_formedness_completeness_checked(tmp_path):
    """Well-formedness completeness is checked before pipeline stages."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.formation.select_formation", return_value=QUICK), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit", new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        # The wf_checker should exist and have a registry
        assert mgr._wf_checker is not None
        assert mgr._score_registry is not None
        await mgr.run()

    assert run.status == "completed"


# ── Resume tests ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_resume_from_paused(tmp_path):
    """Resume continues from paused stage index."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    # Save diagnose output so resume can restore it
    pm.save_stage_output("diagnose", _diagnose())

    # Simulate a paused run at stage index 1 (architect in standard)
    run.formation = "standard"
    run.current_stage_index = 1
    run.pause("Test pause")
    pm.save_state(run)

    with patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes", new=AsyncMock(return_value=SubmitResult(action="none", success=True))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.resume(start_index=1)

    assert run.status == "completed"
    # Should have stage records from resumed stages
    assert len(run.stages) >= 1


@pytest.mark.asyncio
async def test_pipeline_resume_restores_state(tmp_path):
    """Resume restores PipelineState from saved stage outputs."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    # Save outputs from completed stages
    diag = _diagnose()
    pm.save_stage_output("diagnose", diag)
    pm.save_stage_output("architect", _architect())
    pm.save_stage_output("locate", _locate())

    run.formation = "standard"
    run.current_stage_index = 3  # execute
    run.pause("Test pause")

    with patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes", new=AsyncMock(return_value=SubmitResult(action="none", success=True))):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=MagicMock(confirm_break_point=MagicMock(return_value=True)))
        await mgr.resume(start_index=3)

    # State should have been restored from saved outputs
    assert mgr.state.diagnose_result is not None
    assert mgr.state.diagnose_result.summary == diag.summary
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_pipeline_resume_unknown_formation_fails(tmp_path):
    """Resume fails gracefully with unknown formation."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    run.formation = "nonexistent"
    run.pause("Test pause")

    mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                          BudgetTracker(), agent)
    await mgr.resume(start_index=1)

    assert run.status == "failed"
    assert "unknown formation" in run.pause_reason.lower()


# ── Epic pipeline tests ─────────────────────────────────────────────


def _decompose_questions():
    return DecomposeQuestionsResult(
        decisions=[
            DecomposeDecision(
                ambiguity="Token format",
                decision="JWT with RS256",
                rationale="Portable, stateless, widely supported",
            ),
        ],
        questions=[
            DecomposeQuestion(question="What DB?", importance="blocking"),
        ],
        assumptions=["PostgreSQL"],
        confidence=0.7,
        reasoning="Need DB info",
    )


def _component_map():
    return ComponentMapResult(
        one_sentence="Build auth system",
        components=[
            Component(id="db", name="DB Schema", description="Tables",
                     dependencies=[], estimated_complexity="simple"),
            Component(id="auth", name="Auth Core", description="Login",
                     dependencies=["db"], estimated_complexity="moderate"),
        ],
        interaction_summary="Auth depends on DB",
        confidence=0.85,
        reasoning="Clear decomposition",
    )


def _contracts():
    return ContractSetResult(
        contracts=[
            Contract(component_id="db", inputs=["fields"], outputs=["model"]),
            Contract(component_id="auth", inputs=["creds"], outputs=["token"]),
        ],
        confidence=0.9,
        reasoning="Standard contracts",
    )


def _impl_order():
    return ImplementationOrderResult(
        steps=[
            ImplementationStep(component_id="db", order=1, rationale="Foundation"),
            ImplementationStep(component_id="auth", order=2, rationale="Depends on DB"),
        ],
        parallel_groups=[["db"], ["auth"]],
        confidence=0.9,
        reasoning="Topological sort",
    )


@pytest.mark.asyncio
async def test_epic_formation_selected_for_epic(tmp_path):
    """Epic complexity selects epic formation."""
    d = _diagnose(complexity="epic")
    f = select_formation(d, ProjectConfig(formation="auto"))
    assert f.name == "epic"


@pytest.mark.asyncio
async def test_epic_escalation_suppressed(tmp_path):
    """Diagnose escalation is suppressed for epic tasks — decomposition handles it."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    diag = _diagnose(complexity="epic")
    diag.escalation = "This should be decomposed into seven tasks"

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(diag, 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build chess engine", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Should NOT have paused — epic escalation is suppressed
    assert run.status == "completed"
    assert run.formation == "epic"


@pytest.mark.asyncio
async def test_epic_decisions_flow_to_component_map(tmp_path):
    """Agent decisions from Phase 1 are passed as answers to Phase 2."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    build_cmap_mock = AsyncMock(return_value=(_component_map(), 100, 50))

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map", new=build_cmap_mock), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # build_component_map should have received agent decisions as answers
    call_kwargs = build_cmap_mock.call_args
    answers = call_kwargs[1].get("answers", None)
    # The decisions should be merged in — "Token format" → "JWT with RS256"
    assert answers is not None
    assert "Token format" in answers


@pytest.mark.asyncio
async def test_epic_pipeline_decomposes_and_builds(tmp_path):
    """Epic pipeline decomposes into components and builds each."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={"What DB?": "PostgreSQL"})

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.formation == "epic"
    assert run.status == "completed"
    assert mgr.state.decomposition_plan is not None
    assert len(mgr.state.component_results) == 2


@pytest.mark.asyncio
async def test_epic_pipeline_component_failure(tmp_path):
    """Component failure is recorded but doesn't crash pipeline."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    # Architect fails for all components
    async def failing_architect(*a, **kw):
        raise RuntimeError("Architect failed")

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=failing_architect), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Both components should have failed
    assert len(mgr.state.component_results) == 2
    assert all(r.status == "failed" for r in mgr.state.component_results)
    # Run should fail (not crash) when no components complete
    assert run.status == "failed"
    assert "no components" in run.pause_reason.lower()


@pytest.mark.asyncio
async def test_epic_pipeline_surfaces_clarifying_questions(tmp_path):
    """Clarifying questions (not just blocking) are sent to human."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    # Questions with both blocking and clarifying
    questions = DecomposeQuestionsResult(
        questions=[
            DecomposeQuestion(question="What DB?", importance="blocking"),
            DecomposeQuestion(question="OAuth or custom?", importance="clarifying"),
            DecomposeQuestion(question="Logging preference?", importance="nice_to_have"),
        ],
        assumptions=["PostgreSQL"],
        confidence=0.7,
        reasoning="Need info",
    )

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(questions, 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Should have been called with blocking + clarifying (2 questions), not nice_to_have
    call_args = human_io.answer_decomposition_questions.call_args[0][0]
    assert len(call_args) == 2
    importances = {q.importance for q in call_args}
    assert importances == {"blocking", "clarifying"}


@pytest.mark.asyncio
async def test_epic_pipeline_questions_pause(tmp_path):
    """Blocking questions pause for human when no human_io."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))):

        # No human_io — decompose break point should pause
        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=None)
        await mgr.run()

    assert run.status == "paused"


@pytest.mark.asyncio
async def test_epic_pipeline_resume(tmp_path):
    """Epic pipeline can resume from decompose break point."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    # Set up paused state with epic formation
    run.formation = "epic"
    run.pause("Break point at decompose")
    run.current_stage_index = 1
    pm.save_state(run)

    # Save diagnose output so restore works
    pm.save_stage_output("diagnose", _diagnose(complexity="epic"))

    with patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.resume(start_index=1)

    assert run.status == "completed"


@pytest.mark.asyncio
async def test_epic_resume_skips_decompose_with_saved_plan(tmp_path):
    """Resume skips decomposition when a saved plan already exists."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    # Set up paused epic state with saved plan
    run.formation = "epic"
    run.pause("Break point at decompose")
    run.current_stage_index = 1
    pm.save_state(run)

    pm.save_stage_output("diagnose", _diagnose(complexity="epic"))

    # Save a full decomposition plan
    plan = DecompositionPlan(
        questions=_decompose_questions(),
        component_map=_component_map(),
        contracts=_contracts(),
        implementation_order=_impl_order(),
        answers={"What DB?": "PostgreSQL"},
    )
    pm.save_stage_output("decompose", plan)

    # Track if decompose functions are called
    decompose_calls = {"count": 0}

    async def counting_ask_questions(*args, **kwargs):
        decompose_calls["count"] += 1
        return _decompose_questions(), 100, 50

    with patch("swarm.agents.decompose.ask_questions", new=AsyncMock(side_effect=counting_ask_questions)), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.resume(start_index=1)

    # Decompose should NOT have been called — plan was restored from saved output
    assert decompose_calls["count"] == 0
    assert run.status == "completed"
    assert mgr.state.decomposition_plan is not None


@pytest.mark.asyncio
async def test_epic_pipeline_single_submit(tmp_path):
    """Epic pipeline submits all changes in a single commit."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    submit_mock = AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes", new=submit_mock):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Submit should be called exactly once (single commit for all components)
    assert submit_mock.call_count == 1


@pytest.mark.asyncio
async def test_epic_pipeline_cross_component_constraints(tmp_path):
    """Cross-component constraints are fed into component task strings."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    contracts = ContractSetResult(
        contracts=[
            Contract(component_id="db", inputs=["fields"], outputs=["model"]),
            Contract(component_id="auth", inputs=["creds"], outputs=["token"]),
        ],
        cross_component_constraints=["All errors use WanderError format", "IDs are UUIDs everywhere"],
        confidence=0.9,
        reasoning="Standard contracts",
    )

    captured_tasks = []
    original_execute = AsyncMock(return_value=(_execute(), 100, 50))

    async def capture_execute(*args, **kwargs):
        # The manager sets self._task before calling handlers, capture it
        return await original_execute(*args, **kwargs)

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(contracts, 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute",
               new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Verify constraints are in the decomposition plan
    assert mgr.state.decomposition_plan is not None
    assert "All errors use WanderError format" in mgr.state.decomposition_plan.contracts.cross_component_constraints
    assert "IDs are UUIDs everywhere" in mgr.state.decomposition_plan.contracts.cross_component_constraints


@pytest.mark.asyncio
async def test_epic_pipeline_rich_prior_context(tmp_path):
    """Prior component architect/execute context is included in task string."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.answer_decomposition_questions = MagicMock(return_value={})

    # Track what task strings are used for execute calls
    execute_tasks = []
    async def capturing_execute(agent, task, *args, **kwargs):
        execute_tasks.append(task)
        return _execute(), 100, 50

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="epic"), 100, 50))), \
         patch("swarm.agents.decompose.ask_questions",
               new=AsyncMock(return_value=(_decompose_questions(), 100, 50))), \
         patch("swarm.agents.decompose.build_component_map",
               new=AsyncMock(return_value=(_component_map(), 100, 50))), \
         patch("swarm.agents.decompose.define_contracts",
               new=AsyncMock(return_value=(_contracts(), 100, 50))), \
         patch("swarm.agents.decompose.plan_implementation_order",
               new=AsyncMock(return_value=(_impl_order(), 100, 50))), \
         patch("swarm.agents.architect.architect",
               new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.locate.locate",
               new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(side_effect=capturing_execute)), \
         patch("swarm.agents.test.generate_tests",
               new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock()), \
         patch("swarm.executor.write_tests", new=AsyncMock()), \
         patch("swarm.executor.run_verification",
               new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full",
               new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.agents.integrate.integrate",
               new=AsyncMock(return_value=(_integrate(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="commit", success=True, ref="abc123"))):

        mgr = PipelineManager(pm, run, "Build auth system", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Second component (auth) should have prior context from first (db)
    assert len(execute_tasks) == 2
    second_task = execute_tasks[1]
    assert "Prior completed components" in second_task
    assert "db" in second_task


@pytest.mark.asyncio
async def test_non_epic_unchanged(tmp_path):
    """Standard/quick/thorough flows still work unchanged after epic additions."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="simple"), 100, 50))), \
         patch.object(PipelineManager, "_handle_architect", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_locate",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_test", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_review",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):

        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.formation == "standard"
    assert run.status == "completed"
    # No decomposition plan for non-epic
    assert mgr.state.decomposition_plan is None


# ── Per-stage model routing tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_model_switching_per_stage(tmp_path):
    """Per-stage model routing switches model before each handler."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = MagicMock()
    agent.set_model = MagicMock()
    agent.assess = AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))
    agent.close = AsyncMock()

    config = ProjectConfig(
        stage_models={"execute": "claude-sonnet-4-5-20250929"},
    )

    # Use trivial+high confidence → QUICK formation (diagnose, execute, verify, submit)
    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):

        mgr = PipelineManager(pm, run, "Fix login", config, SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    # Agent.set_model should have been called with Sonnet for execute
    calls = [c.args[0] for c in agent.set_model.call_args_list]
    assert "claude-sonnet-4-5-20250929" in calls


@pytest.mark.asyncio
async def test_model_fallback_to_default(tmp_path):
    """Without stage_models, uses SwarmConfig.model as fallback."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = MagicMock()
    agent.set_model = MagicMock()
    agent.assess = AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))
    agent.close = AsyncMock()

    swarm_config = SwarmConfig(model="claude-opus-4-6")

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):

        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), swarm_config,
                              BudgetTracker(), agent)
        await mgr.run()

    # All calls should use the default model
    calls = [c.args[0] for c in agent.set_model.call_args_list]
    assert all(c == "claude-opus-4-6" for c in calls)


@pytest.mark.asyncio
async def test_config_override_precedence(tmp_path):
    """ProjectConfig.stage_models overrides SwarmConfig.default_stage_models."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = MagicMock()
    agent.set_model = MagicMock()
    agent.assess = AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))
    agent.close = AsyncMock()

    # SwarmConfig says use Haiku for execute, ProjectConfig says Opus
    swarm_config = SwarmConfig(
        model="claude-sonnet-4-5-20250929",
        default_stage_models={"execute": "claude-haiku-4-5-20251001"},
    )
    config = ProjectConfig(
        stage_models={"execute": "claude-opus-4-6"},
    )

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):

        mgr = PipelineManager(pm, run, "Fix login", config, swarm_config,
                              BudgetTracker(), agent)
        await mgr.run()

    # The execute stage set_model call should be Opus (project config wins over swarm config)
    calls = [c.args[0] for c in agent.set_model.call_args_list]
    assert "claude-opus-4-6" in calls


# ── Per-stage backend routing tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_stage_backend_routing(tmp_path):
    """Per-stage backend routing switches backend for configured stages."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = MagicMock()
    agent.set_model = MagicMock()
    agent.set_backend = MagicMock()
    agent.assess = AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))
    agent.close = AsyncMock()

    config = ProjectConfig(
        stage_backends={"execute": "claude_code"},
    )

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):

        mgr = PipelineManager(pm, run, "Fix bug", config, SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    # set_backend should have been called with "claude_code" for the execute stage
    backend_calls = [c.args[0] for c in agent.set_backend.call_args_list]
    assert "claude_code" in backend_calls


@pytest.mark.asyncio
async def test_stage_backend_not_called_when_not_configured(tmp_path):
    """No stage_backends config → set_backend never called."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = MagicMock()
    agent.set_model = MagicMock()
    agent.set_backend = MagicMock()
    agent.assess = AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))
    agent.close = AsyncMock()

    with patch("swarm.agents.diagnose.diagnose",
               new=AsyncMock(return_value=(_diagnose(complexity="trivial", confidence=0.95), 100, 50))), \
         patch.object(PipelineManager, "_handle_execute", new=AsyncMock(return_value=None)), \
         patch.object(PipelineManager, "_handle_verify",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))), \
         patch.object(PipelineManager, "_handle_submit",
                      new=AsyncMock(return_value=GateResult(passed=True, reason="ok"))):

        mgr = PipelineManager(pm, run, "Fix bug", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent)
        await mgr.run()

    # set_backend should not be called when no stage_backends configured
    agent.set_backend.assert_not_called()


# ── Review escalation tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_escalation_project_approves(tmp_path):
    """Project-level escalation approves with high confidence → pipeline continues."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=1,
    )

    project_escalation = AsyncMock(
        return_value=(_escalation_result(decision="approve", confidence=0.85), 100, 50),
    )
    architect_escalation = AsyncMock(
        return_value=(_escalation_result(decision="force_approve", confidence=0.6), 100, 50),
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(approve=False), 100, 50))), \
         patch("swarm.agents.review.escalate_to_project", new=project_escalation), \
         patch("swarm.agents.review.escalate_to_architect", new=architect_escalation), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Project approved with high confidence — architect should NOT be called
    project_escalation.assert_called_once()
    architect_escalation.assert_not_called()


@pytest.mark.asyncio
async def test_review_escalation_to_architect(tmp_path):
    """Low project confidence escalates to architect level."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=1,
    )

    # Project rejects — escalate to architect
    project_escalation = AsyncMock(
        return_value=(_escalation_result(decision="reject", confidence=0.4), 100, 50),
    )
    architect_escalation = AsyncMock(
        return_value=(_escalation_result(decision="force_approve", confidence=0.65), 100, 50),
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(approve=False), 100, 50))), \
         patch("swarm.agents.review.escalate_to_project", new=project_escalation), \
         patch("swarm.agents.review.escalate_to_architect", new=architect_escalation), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Both levels should have been called
    project_escalation.assert_called_once()
    architect_escalation.assert_called_once()


@pytest.mark.asyncio
async def test_review_escalation_low_project_confidence_escalates(tmp_path):
    """Project approve with low confidence still escalates to architect."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=1,
    )

    # Project approves but with low confidence (< 0.7) — should still escalate
    project_escalation = AsyncMock(
        return_value=(_escalation_result(decision="approve", confidence=0.5), 100, 50),
    )
    architect_escalation = AsyncMock(
        return_value=(_escalation_result(decision="approve", confidence=0.8), 100, 50),
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(approve=False), 100, 50))), \
         patch("swarm.agents.review.escalate_to_project", new=project_escalation), \
         patch("swarm.agents.review.escalate_to_architect", new=architect_escalation), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Low confidence approve → still escalates to architect
    project_escalation.assert_called_once()
    architect_escalation.assert_called_once()


@pytest.mark.asyncio
async def test_review_escalation_notifies_user(tmp_path):
    """Architect-level forced decision notifies the user."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=1,
    )

    # Force through to architect
    project_escalation = AsyncMock(
        return_value=(_escalation_result(decision="reject", confidence=0.3), 100, 50),
    )
    forced_result = ReviewEscalationResult(
        decision="force_approve", confidence=0.55,
        rationale="Code works but has rough edges",
        conditions=["Manual review of error handling recommended"],
    )
    architect_escalation = AsyncMock(return_value=(forced_result, 100, 50))

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(approve=False), 100, 50))), \
         patch("swarm.agents.review.escalate_to_project", new=project_escalation), \
         patch("swarm.agents.review.escalate_to_architect", new=architect_escalation), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # User should have been notified about the forced decision
    human_io.notify.assert_called()
    notify_args = human_io.notify.call_args
    assert "review" in notify_args[0][0]  # stage
    assert "force_approve" in notify_args[0][1]  # message contains decision


@pytest.mark.asyncio
async def test_epic_skips_completed_components(tmp_path):
    """Already-completed components are skipped in epic pipeline."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    # Save a completed build_result for component "auth"
    pm.save_component_output("auth", "build_result", {
        "component_id": "auth", "status": "completed",
        "stages_completed": ["architect", "locate", "execute", "test", "verify", "review"],
        "issues": [], "files_changed": ["src/auth.py"],
    })

    mgr = PipelineManager(pm, run, "Build auth", ProjectConfig(), SwarmConfig(),
                          BudgetTracker(), agent)

    # Verify the saved result can be loaded
    saved = pm.load_component_output("auth", "build_result")
    assert saved is not None
    assert saved["status"] == "completed"

    # The redo set is empty by default — completed components would be skipped
    assert mgr._redo_components == set()


@pytest.mark.asyncio
async def test_epic_redo_forces_rebuild(tmp_path):
    """Components in redo set are rebuilt even if completed."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()

    pm.save_component_output("auth", "build_result", {
        "component_id": "auth", "status": "completed",
        "stages_completed": ["architect", "locate", "execute", "test", "verify", "review"],
        "issues": [], "files_changed": ["src/auth.py"],
    })

    mgr = PipelineManager(pm, run, "Build auth", ProjectConfig(), SwarmConfig(),
                          BudgetTracker(), agent, redo_components={"auth"})

    assert "auth" in mgr._redo_components


@pytest.mark.asyncio
async def test_review_escalation_contention_context(tmp_path):
    """Escalation receives correct contention context from review verdict."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                          BudgetTracker(), agent, human_io=human_io)
    mgr._state.execute_result = _execute()
    mgr._state.review_loops = 2

    verdict = ReviewVerdict(
        verdict="reject", confidence=0.4,
        issues=["Missing null check", "No error handling"],
        risks=["Could crash on empty input"],
        missed_edge_cases=["Empty list case"],
        reasoning="Multiple issues found",
    )
    mgr._state.review_issues_for_reexecute = ["Missing null check"]

    contention = mgr._build_contention(verdict)
    assert "Missing null check" in contention
    assert "No error handling" in contention
    assert "Could crash on empty input" in contention
    assert "Empty list case" in contention
    assert "2" in contention  # review loops count


# ── Review classification + exponential backoff tests ─────────────


@pytest.mark.asyncio
async def test_review_factual_issues_fixed_without_debate(tmp_path):
    """Factual issues trigger re-execute, not debate."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    review_calls = {"count": 0}

    async def mock_review_full(*args, **kwargs):
        review_calls["count"] += 1
        if review_calls["count"] == 1:
            return ReviewVerdict(
                verdict="reject", confidence=0.9,
                issues=["Function bar() called as foo()"],
                reasoning="Bug found", risks=[],
            ), 100, 50
        return _review(approve=True), 100, 50

    execute_calls = {"count": 0}
    async def mock_execute(*args, **kwargs):
        execute_calls["count"] += 1
        return _execute(), 100, 50

    # Classify the issue as factual
    classify_mock = AsyncMock(return_value=(
        _classification(factual=["Function bar() called as foo()"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=2,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(side_effect=mock_execute)), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(side_effect=mock_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Execute should be called twice: initial + factual fix
    assert execute_calls["count"] == 2
    # Review should be called twice: reject + approve after fix
    assert review_calls["count"] == 2
    # Classify should have been called once
    classify_mock.assert_called_once()


@pytest.mark.asyncio
async def test_review_subjective_below_threshold_approves(tmp_path):
    """Subjective issues below disagreement threshold → approve (no debate)."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    # Reviewer rejects with LOW confidence (0.4) on subjective issue
    # Threshold for loop 1 is 0.6 — reviewer must exceed this to keep blocking
    async def mock_review_full(*args, **kwargs):
        return ReviewVerdict(
            verdict="reject", confidence=0.4,
            issues=["Should use a different algorithm"],
            reasoning="I would do it differently", risks=[],
        ), 100, 50

    # Classify as subjective
    classify_mock = AsyncMock(return_value=(
        _classification(subjective=["Should use a different algorithm"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=3,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(side_effect=mock_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Loop 1: threshold=0.0, confidence=0.4 > 0.0 → re-review with perspective shift
    # Loop 2: threshold=0.6, confidence=0.4 < 0.6 → approved (below threshold)
    assert classify_mock.call_count == 2


@pytest.mark.asyncio
async def test_review_subjective_above_threshold_triggers_perspective_shift(tmp_path):
    """Subjective issues above threshold trigger perspective shift and re-review."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    review_calls = {"count": 0}

    async def mock_review_full(*args, **kwargs):
        review_calls["count"] += 1
        learnings = kwargs.get("learnings_context", "")
        if review_calls["count"] == 1:
            # First: reject with high confidence (subjective)
            return ReviewVerdict(
                verdict="reject", confidence=0.9,
                issues=["Error handling approach is wrong"],
                reasoning="Would be better with try/except", risks=[],
            ), 100, 50
        # Second call: should have perspective shift
        # Check if perspective shift was injected via learnings_context
        if review_calls["count"] == 2:
            assert "PERSPECTIVE SHIFT" in learnings
            return _review(approve=True), 100, 50
        return _review(approve=True), 100, 50

    # Classify as subjective with HIGH confidence → above threshold
    classify_mock = AsyncMock(return_value=(
        _classification(subjective=["Error handling approach is wrong"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=3,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(side_effect=mock_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Review called twice: first reject, then approve with perspective shift
    assert review_calls["count"] == 2


@pytest.mark.asyncio
async def test_review_mixed_factual_and_subjective(tmp_path):
    """Mixed issues: factual gets fixed, subjective checked against threshold."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    review_calls = {"count": 0}
    execute_calls = {"count": 0}

    async def mock_review_full(*args, **kwargs):
        review_calls["count"] += 1
        if review_calls["count"] == 1:
            return ReviewVerdict(
                verdict="reject", confidence=0.4,
                issues=["Wrong function name", "Should use different pattern"],
                reasoning="Issues found", risks=[],
            ), 100, 50
        return _review(approve=True), 100, 50

    async def mock_execute(*args, **kwargs):
        execute_calls["count"] += 1
        return _execute(), 100, 50

    # Mixed: one factual, one subjective (low confidence)
    classify_mock = AsyncMock(return_value=(
        _classification(
            factual=["Wrong function name"],
            subjective=["Should use different pattern"],
        ), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=3,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(side_effect=mock_execute)), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(side_effect=mock_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Execute called twice: initial + factual fix
    assert execute_calls["count"] == 2
    # Subjective issue below threshold (0.4 < 0.6 at loop 1) → approved
    classify_mock.assert_called_once()


@pytest.mark.asyncio
async def test_review_only_subjective_issues_can_reach_escalation(tmp_path):
    """Only subjective issues can escalate — factual issues are fixed in loop."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    # Reviewer always rejects with high confidence on subjective grounds
    async def mock_review_full(*args, **kwargs):
        return ReviewVerdict(
            verdict="reject", confidence=0.95,
            issues=["Architecture is wrong", "Should use microservices"],
            reasoning="Fundamental disagreement", risks=[],
        ), 100, 50

    # Always classify as subjective — no factual issues
    classify_mock = AsyncMock(return_value=(
        _classification(subjective=["Architecture is wrong", "Should use microservices"]),
        100, 50,
    ))

    project_escalation = AsyncMock(
        return_value=(_escalation_result(decision="approve", confidence=0.85), 100, 50),
    )

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=3,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(side_effect=mock_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.agents.review.escalate_to_project", new=project_escalation), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Should have classified 3 times (once per loop) then escalated
    assert classify_mock.call_count == 3
    project_escalation.assert_called_once()


@pytest.mark.asyncio
async def test_review_escalating_thresholds(tmp_path):
    """Each review loop requires higher confidence to keep blocking."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    # Thresholds are [0.0, 0.6, 0.8]
    # Loop 1: threshold=0.0 → any confidence passes (always re-review)
    # Loop 2: threshold=0.6 → confidence 0.5 → below → approve
    review_calls = {"count": 0}

    async def mock_review_full(*args, **kwargs):
        review_calls["count"] += 1
        if review_calls["count"] <= 2:
            return ReviewVerdict(
                verdict="reject", confidence=0.5,
                issues=["Naming is confusing"],
                reasoning="Style issue", risks=[],
            ), 100, 50
        return _review(approve=True), 100, 50

    classify_mock = AsyncMock(return_value=(
        _classification(subjective=["Naming is confusing"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_review_loops=3,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(side_effect=mock_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Loop 1: threshold=0.0, confidence=0.5 > 0.0 → re-review with perspective shift
    # Loop 2: threshold=0.6, confidence=0.5 < 0.6 → approved (below threshold)
    # So review_full called twice, classify called twice
    assert review_calls["count"] == 2
    assert classify_mock.call_count == 2


@pytest.mark.asyncio
async def test_review_classification_helper(tmp_path):
    """Classification helper constructs proper ClassifiedIssue objects."""
    c = _classification(
        factual=["Missing import"],
        subjective=["Bad naming", "Wrong pattern"],
    )
    assert len(c.issues) == 3
    assert c.issues[0].category == "factual"
    assert c.issues[0].issue == "Missing import"
    assert c.issues[1].category == "subjective"
    assert c.issues[2].category == "subjective"


# ── Architect review tests ──────────────────────────────────────────


def _architect_sub_verdict(pass_type="mvp", verdict="approve", confidence=0.8, issues=None):
    return ReviewSubVerdict(
        pass_type=pass_type, verdict=verdict,
        confidence=confidence, issues=issues or [],
        reasoning=f"{pass_type} review",
    )


@pytest.mark.asyncio
async def test_architect_review_approves_good_plan(tmp_path):
    """Architect review sub-passes all approve → pipeline continues normally."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    # architect_review_full returns approve
    async def mock_arch_review_full(*args, **kwargs):
        return ReviewVerdict(
            verdict="approve", confidence=0.9, issues=[],
            reasoning="Architecture review: mvp, simplicity",
            sub_verdicts=[
                _architect_sub_verdict("mvp", "approve", 0.9),
                _architect_sub_verdict("simplicity", "approve", 0.9),
            ],
        ), 200, 100

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="architect",
                           architect_sub_passes=["mvp", "simplicity"]),
            FormationStage(name="locate"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_architect_review_loops=1,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.architect.architect_review_full", new=AsyncMock(side_effect=mock_arch_review_full)), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Add login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"


@pytest.mark.asyncio
async def test_architect_review_rejects_triggers_replan(tmp_path):
    """Factual issues from architect review → re-architect with constraints."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    architect_calls = {"count": 0}
    async def mock_architect(*args, **kwargs):
        architect_calls["count"] += 1
        return _architect(), 100, 50

    review_calls = {"count": 0}
    async def mock_arch_review_full(*args, **kwargs):
        review_calls["count"] += 1
        if review_calls["count"] == 1:
            return ReviewVerdict(
                verdict="reject", confidence=0.9,
                issues=["Plan reinvents existing auth library"],
                reasoning="Architecture review: reuse issue",
            ), 200, 100
        return ReviewVerdict(
            verdict="approve", confidence=0.85, issues=[],
            reasoning="Architecture review: approved after replan",
        ), 200, 100

    classify_mock = AsyncMock(return_value=(
        _classification(factual=["Plan reinvents existing auth library"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="architect",
                           architect_sub_passes=["mvp", "simplicity"]),
            FormationStage(name="locate"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_architect_review_loops=2,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(side_effect=mock_architect)), \
         patch("swarm.agents.architect.architect_review_full", new=AsyncMock(side_effect=mock_arch_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Add login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Architect called twice: initial + replan after factual rejection
    assert architect_calls["count"] == 2
    # Review called twice: reject + approve
    assert review_calls["count"] == 2
    classify_mock.assert_called_once()


@pytest.mark.asyncio
async def test_architect_review_subjective_below_threshold_approves(tmp_path):
    """Subjective architect issues below threshold → approve (no debate)."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    # Reviewer rejects with LOW confidence (0.4) on subjective issue
    # Loop 1 threshold is 0.6 — reviewer must exceed to keep blocking
    async def mock_arch_review_full(*args, **kwargs):
        return ReviewVerdict(
            verdict="reject", confidence=0.4,
            issues=["Should use microservices instead"],
            reasoning="Architecture review: style preference",
        ), 200, 100

    classify_mock = AsyncMock(return_value=(
        _classification(subjective=["Should use microservices instead"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="architect",
                           architect_sub_passes=["mvp", "simplicity"]),
            FormationStage(name="locate"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_architect_review_loops=2,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.architect.architect_review_full", new=AsyncMock(side_effect=mock_arch_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Add login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Pipeline continues despite rejection — confidence below threshold
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_architect_review_subjective_above_threshold_perspective_shift(tmp_path):
    """Subjective architect issues above threshold → perspective shift + re-review."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    review_calls = {"count": 0}
    async def mock_arch_review_full(*args, **kwargs):
        review_calls["count"] += 1
        if review_calls["count"] == 1:
            return ReviewVerdict(
                verdict="reject", confidence=0.7,
                issues=["Over-abstracted design"],
                reasoning="Too many layers",
            ), 200, 100
        # Second review: approve after perspective shift
        return ReviewVerdict(
            verdict="approve", confidence=0.8, issues=[],
            reasoning="Accepted after perspective shift",
        ), 200, 100

    classify_mock = AsyncMock(return_value=(
        _classification(subjective=["Over-abstracted design"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="architect",
                           architect_sub_passes=["mvp", "simplicity"]),
            FormationStage(name="locate"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_architect_review_loops=2,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.architect.architect_review_full", new=AsyncMock(side_effect=mock_arch_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Add login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # Review called twice: first reject (above threshold), then approve with perspective shift
    assert review_calls["count"] == 2


@pytest.mark.asyncio
async def test_architect_review_escalates_on_loop_exhaustion(tmp_path):
    """Architect review loop exhaustion → escalation chain."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    # Always reject
    async def mock_arch_review_full(*args, **kwargs):
        return ReviewVerdict(
            verdict="reject", confidence=0.9,
            issues=["Fundamental design flaw"],
            reasoning="Rejected",
        ), 200, 100

    classify_mock = AsyncMock(return_value=(
        _classification(subjective=["Fundamental design flaw"]), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="architect",
                           architect_sub_passes=["mvp", "simplicity"]),
            FormationStage(name="locate"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="review", mode="adversarial"),
            FormationStage(name="submit"),
        ],
        max_architect_review_loops=1,
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.architect.architect_review_full", new=AsyncMock(side_effect=mock_arch_review_full)), \
         patch("swarm.agents.review.classify_review_issues", new=classify_mock), \
         patch("swarm.agents.review.escalate_to_project",
               new=AsyncMock(return_value=(_escalation_result("approve", 0.85), 100, 50))), \
         patch("swarm.agents.locate.locate", new=AsyncMock(return_value=(_locate(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(_tests(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.review_full", new=AsyncMock(return_value=(_review(), 100, 50))), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Add login", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Escalation should have approved — pipeline completes
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_architect_review_no_sub_passes_skips(tmp_path):
    """No architect sub-passes configured → no architect review (backward compat)."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    arch_review_mock = AsyncMock()

    # Formation with NO architect sub-passes
    test_formation = Formation(
        name="quick",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="architect"),  # No architect_sub_passes
            FormationStage(name="execute"),
            FormationStage(name="verify"),
            FormationStage(name="submit"),
        ],
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.architect.architect", new=AsyncMock(return_value=(_architect(), 100, 50))), \
         patch("swarm.agents.architect.architect_review_full", new=arch_review_mock), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix bug", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "completed"
    # architect_review_full should NOT have been called
    arch_review_mock.assert_not_called()


@pytest.mark.asyncio
async def test_standard_has_mvp_simplicity_sub_passes(tmp_path):
    """STANDARD formation architect stage has mvp + simplicity sub-passes."""
    architect_stage = next(s for s in STANDARD.stages if s.name == "architect")
    assert architect_stage.architect_sub_passes == ["mvp", "simplicity"]
    assert STANDARD.max_architect_review_loops == 1


@pytest.mark.asyncio
async def test_thorough_has_all_architect_sub_passes(tmp_path):
    """THOROUGH formation architect stage has all 5 sub-passes."""
    architect_stage = next(s for s in THOROUGH.stages if s.name == "architect")
    assert architect_stage.architect_sub_passes == ["mvp", "simplicity", "integrity", "reuse", "standards"]
    assert THOROUGH.max_architect_review_loops == 2


# ── Escalation management chain tests ────────────────────────────────


@pytest.mark.asyncio
async def test_escalation_routed_through_management_never_pauses(tmp_path):
    """Escalation signals route through management chain — pipeline never pauses."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    # Test agent sets escalation
    test_result = _tests()
    test_result.escalation = "Tests assume ts-node shebang"

    # Project arbiter approves
    project_mock = AsyncMock(return_value=(
        _escalation_result("approve", 0.85), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="submit"),
        ],
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(test_result, 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.escalate_to_project", new=project_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Build chess", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Pipeline completes — escalation absorbed by management
    assert run.status == "completed"
    project_mock.assert_called_once()
    human_io.notify.assert_called()


@pytest.mark.asyncio
async def test_escalation_reaches_architect_when_project_unsure(tmp_path):
    """Low-confidence project arbiter → architect arbiter decides (never pauses)."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)
    human_io.notify = MagicMock()

    test_result = _tests()
    test_result.escalation = "Complex testing concern"

    # Project arbiter rejects — escalates to architect
    project_mock = AsyncMock(return_value=(
        _escalation_result("reject", 0.5), 100, 50,
    ))
    # Architect must decide — force_approve
    architect_mock = AsyncMock(return_value=(
        _escalation_result("force_approve", 0.7), 100, 50,
    ))

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="test"),
            FormationStage(name="verify"),
            FormationStage(name="submit"),
        ],
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(_diagnose(), 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(_execute(), 100, 50))), \
         patch("swarm.agents.test.generate_tests", new=AsyncMock(return_value=(test_result, 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.write_tests", new=AsyncMock(return_value=[])), \
         patch("swarm.executor.run_verification", new=AsyncMock(return_value=_verify())), \
         patch("swarm.agents.review.escalate_to_project", new=project_mock), \
         patch("swarm.agents.review.escalate_to_architect", new=architect_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Build chess", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    # Pipeline completes — architect absorbed the escalation
    assert run.status == "completed"
    project_mock.assert_called_once()
    architect_mock.assert_called_once()
    human_io.notify.assert_called()


@pytest.mark.asyncio
async def test_consternation_pauses_without_management(tmp_path):
    """Multi-consternation pause stays mechanical (no management chain)."""
    pm, run = _make_pm_and_run(tmp_path)
    agent = _mock_agent_returns()
    human_io = MagicMock()
    human_io.confirm_break_point = MagicMock(return_value=True)

    # Diagnose and execute both set consternation → 2 = pause
    diag = _diagnose()
    diag.consternation = "Something off"
    exec_result = _execute()
    exec_result.consternation = "Also weird"

    project_mock = AsyncMock()

    test_formation = Formation(
        name="standard",
        stages=[
            FormationStage(name="diagnose"),
            FormationStage(name="execute"),
            FormationStage(name="submit"),
        ],
    )

    with patch("swarm.agents.diagnose.diagnose", new=AsyncMock(return_value=(diag, 100, 50))), \
         patch("swarm.agents.execute.execute", new=AsyncMock(return_value=(exec_result, 100, 50))), \
         patch("swarm.executor.apply_changes", new=AsyncMock(return_value=[])), \
         patch("swarm.agents.review.escalate_to_project", new=project_mock), \
         patch("swarm.executor.submit_changes",
               new=AsyncMock(return_value=SubmitResult(action="none", success=True))), \
         patch("swarm.formation.select_formation", return_value=test_formation):
        mgr = PipelineManager(pm, run, "Fix bug", ProjectConfig(), SwarmConfig(),
                              BudgetTracker(), agent, human_io=human_io)
        await mgr.run()

    assert run.status == "paused"
    # Management chain should NOT be called for consternation pauses
    project_mock.assert_not_called()
