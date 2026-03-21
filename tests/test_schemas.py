"""Tests for all Pydantic schemas — structure, defaults, computed properties."""

from __future__ import annotations

from datetime import datetime

import pytest

from swarm.schemas import (
    ArchitectResult,
    ArchitectStep,
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
    LearningEntry,
    LearningExtraction,
    LocateResult,
    PipelineSignal,
    ProjectRun,
    Question,
    ReviewEscalationResult,
    ReviewVerdict,
    StageRecord,
    SubmitResult,
    TestFile,
    TestResult,
    VerifyResult,
)


# ── DiagnoseResult ───────────────────────────────────────────────────


class TestDiagnoseResult:
    def test_basic_creation(self):
        r = DiagnoseResult(
            task_type="feature", complexity="simple", confidence=0.9,
            language="python", summary="Add login", reasoning="Straightforward",
        )
        assert r.task_type == "feature"
        assert r.confidence == 0.9
        assert r.consternation == ""
        assert r.escalation == ""

    def test_signal_fields(self):
        r = DiagnoseResult(
            task_type="bugfix", complexity="moderate", confidence=0.7,
            language="typescript", summary="Fix auth", reasoning="Tricky",
            consternation="Something feels off", escalation="Need help",
        )
        assert r.consternation == "Something feels off"
        assert r.escalation == "Need help"

    def test_complexity_values(self):
        for c in ("trivial", "simple", "moderate", "complex", "epic"):
            r = DiagnoseResult(
                task_type="feature", complexity=c, confidence=0.5,
                language="python", summary="x", reasoning="x",
            )
            assert r.complexity == c

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            DiagnoseResult(
                task_type="feature", complexity="simple", confidence=1.5,
                language="python", summary="x", reasoning="x",
            )


# ── ArchitectResult ──────────────────────────────────────────────────


class TestArchitectResult:
    def test_basic_creation(self):
        r = ArchitectResult(
            approach="Modular approach",
            steps=[ArchitectStep(description="Step 1")],
            affected_files=["src/main.py"],
            reasoning="Best pattern",
            confidence=0.85,
        )
        assert len(r.steps) == 1
        assert r.new_files == []
        assert r.deleted_files == []

    def test_with_new_and_deleted(self):
        r = ArchitectResult(
            approach="Refactor",
            steps=[],
            affected_files=["a.py"],
            new_files=["b.py"],
            deleted_files=["c.py"],
            reasoning="Clean up",
            confidence=0.7,
        )
        assert r.new_files == ["b.py"]
        assert r.deleted_files == ["c.py"]


# ── LocateResult ─────────────────────────────────────────────────────


class TestLocateResult:
    def test_basic_creation(self):
        r = LocateResult(
            files=[FileLocation(path="src/app.py", relevance="primary", reasoning="Main file")],
            confidence=0.8, search_strategy="grep for keywords",
        )
        assert len(r.files) == 1
        assert r.files[0].relevance == "primary"

    def test_new_file_relevance(self):
        fl = FileLocation(path="src/new.py", relevance="new", reasoning="Will create")
        assert fl.relevance == "new"


# ── ExecuteResult ────────────────────────────────────────────────────


class TestExecuteResult:
    def test_modify_action(self):
        r = ExecuteResult(
            files=[FileAction(
                path="src/app.py", action="modify",
                original="return None", content="return user",
            )],
            explanation="Fix return", confidence=0.9,
        )
        assert r.files[0].action == "modify"

    def test_create_action(self):
        r = ExecuteResult(
            files=[FileAction(
                path="src/new.py", action="create",
                content="def hello(): pass",
            )],
            explanation="New file", confidence=0.85,
        )
        assert r.files[0].action == "create"

    def test_delete_action(self):
        r = ExecuteResult(
            files=[FileAction(
                path="src/old.py", action="delete",
                original="old code",
            )],
            explanation="Remove deprecated", confidence=0.95,
        )
        assert r.files[0].action == "delete"

    def test_total_diff_lines_modify(self):
        r = ExecuteResult(
            files=[FileAction(
                path="a.py", action="modify",
                original="line1\nline2", content="line1\nline2\nline3",
            )],
            explanation="x", confidence=0.5,
        )
        assert r.total_diff_lines > 0

    def test_total_diff_lines_create(self):
        r = ExecuteResult(
            files=[FileAction(path="b.py", action="create", content="line1\nline2")],
            explanation="x", confidence=0.5,
        )
        assert r.total_diff_lines == 2

    def test_total_deletions_delete(self):
        r = ExecuteResult(
            files=[FileAction(path="c.py", action="delete", original="a\nb\nc")],
            explanation="x", confidence=0.5,
        )
        assert r.total_deletions == 3

    def test_total_deletions_modify(self):
        r = ExecuteResult(
            files=[FileAction(
                path="a.py", action="modify",
                original="keep\nremove", content="keep\nadd",
            )],
            explanation="x", confidence=0.5,
        )
        assert r.total_deletions == 1


# ── TestResult ───────────────────────────────────────────────────────


class TestTestResult:
    def test_basic_creation(self):
        r = TestResult(
            test_files=[TestFile(path="tests/test_app.py", content="def test_it(): pass")],
            test_strategy="Unit tests", confidence=0.8,
        )
        assert len(r.test_files) == 1


# ── VerifyResult ─────────────────────────────────────────────────────


class TestVerifyResult:
    def test_defaults(self):
        r = VerifyResult()
        assert r.compiles is False
        assert r.lint_clean is False
        assert r.tests_passing == 0

    def test_all_passing(self):
        r = VerifyResult(compiles=True, lint_clean=True, tests_passing=5, tests_total=5)
        assert r.compiles is True


# ── ReviewVerdict ────────────────────────────────────────────────────


class TestReviewVerdict:
    def test_approve(self):
        r = ReviewVerdict(
            verdict="approve", confidence=0.9, issues=[], reasoning="Clean code",
        )
        assert r.verdict == "approve"

    def test_reject(self):
        r = ReviewVerdict(
            verdict="reject", confidence=0.8, issues=["Missing error handling"],
            risks=["Could crash"], reasoning="Needs work",
        )
        assert len(r.issues) == 1
        assert len(r.risks) == 1


# ── IntegrateResult ──────────────────────────────────────────────────


class TestIntegrateResult:
    def test_coherent(self):
        r = IntegrateResult(coherent=True, confidence=0.95, reasoning="All consistent")
        assert r.coherent is True
        assert r.issues == []

    def test_incoherent(self):
        r = IntegrateResult(
            coherent=False, issues=["Type mismatch across files"],
            confidence=0.8, reasoning="Found inconsistency",
        )
        assert r.coherent is False


# ── SubmitResult ─────────────────────────────────────────────────────


class TestSubmitResult:
    def test_pr(self):
        r = SubmitResult(action="pr", success=True, url="https://github.com/org/repo/pull/1")
        assert r.action == "pr"

    def test_none(self):
        r = SubmitResult(action="none", success=True)
        assert r.ref == ""


# ── GateResult ───────────────────────────────────────────────────────


class TestGateResult:
    def test_pass(self):
        r = GateResult(passed=True, reason="All good")
        assert r.paused is False
        assert r.timeout_minutes == 0

    def test_pause(self):
        r = GateResult(passed=True, paused=True, reason="Break point")
        assert r.paused is True

    def test_fail(self):
        r = GateResult(passed=False, reason="Too complex")
        assert r.passed is False


# ── Formation ────────────────────────────────────────────────────────


class TestFormation:
    def test_basic(self):
        f = Formation(
            name="standard",
            stages=[
                FormationStage(name="diagnose"),
                FormationStage(name="execute"),
            ],
            max_review_loops=1,
        )
        assert len(f.stages) == 2
        assert f.max_review_loops == 1

    def test_adversarial_mode(self):
        s = FormationStage(name="review", mode="adversarial", break_point="pause")
        assert s.mode == "adversarial"
        assert s.break_point == "pause"


# ── PipelineSignal ───────────────────────────────────────────────────


class TestPipelineSignal:
    def test_consternation(self):
        s = PipelineSignal(stage="diagnose", signal_type="consternation", message="Hmm")
        assert s.signal_type == "consternation"

    def test_escalation(self):
        s = PipelineSignal(stage="review", signal_type="escalation", message="Need help")
        assert s.signal_type == "escalation"


# ── ProjectRun ───────────────────────────────────────────────────────


class TestProjectRun:
    def test_creation(self):
        r = ProjectRun(id="abc123", project_dir="/tmp/proj")
        assert r.status == "active"
        assert r.total_tokens == 0
        assert r.current_stage_index == 0

    def test_record_stage(self):
        r = ProjectRun(id="abc123", project_dir="/tmp/proj")
        r.record_stage(StageRecord(stage="diagnose", passed=True, confidence=0.9, tokens_in=100, tokens_out=50, cost_usd=0.01))
        assert r.total_tokens == 150
        assert r.total_cost_usd == 0.01
        assert len(r.stages) == 1

    def test_pause(self):
        r = ProjectRun(id="abc123", project_dir="/tmp/proj")
        r.pause("Break point")
        assert r.status == "paused"
        assert r.pause_reason == "Break point"

    def test_fail(self):
        r = ProjectRun(id="abc123", project_dir="/tmp/proj")
        r.fail("Gate failed")
        assert r.status == "failed"
        assert r.completed_at is not None

    def test_complete(self):
        r = ProjectRun(id="abc123", project_dir="/tmp/proj")
        r.complete()
        assert r.status == "completed"
        assert r.completed_at is not None

    def test_budget_exceeded(self):
        r = ProjectRun(id="abc123", project_dir="/tmp/proj")
        r.mark_budget_exceeded()
        assert r.status == "budget_exceeded"


# ── LearningEntry ────────────────────────────────────────────────────


class TestLearningEntry:
    def test_not_decayed(self):
        e = LearningEntry(
            id="x", lesson="Use Option", category="code_pattern",
            source_project_ids=["p1"], confidence=0.8,
            created_at=datetime.now(), times_validated=3, times_contradicted=1,
        )
        assert e.is_decayed is False

    def test_decayed(self):
        e = LearningEntry(
            id="x", lesson="Bad pattern", category="code_pattern",
            source_project_ids=["p1"], confidence=0.5,
            created_at=datetime.now(), times_validated=0, times_contradicted=5,
        )
        assert e.is_decayed is True

    def test_boundary_not_decayed(self):
        e = LearningEntry(
            id="x", lesson="Edge case", category="code_pattern",
            source_project_ids=["p1"], confidence=0.5,
            created_at=datetime.now(), times_validated=1, times_contradicted=3,
        )
        assert e.is_decayed is False

    def test_boundary_decayed(self):
        e = LearningEntry(
            id="x", lesson="Edge case", category="code_pattern",
            source_project_ids=["p1"], confidence=0.5,
            created_at=datetime.now(), times_validated=1, times_contradicted=4,
        )
        assert e.is_decayed is True


# ── Question ─────────────────────────────────────────────────────────


class TestQuestion:
    def test_unanswered(self):
        q = Question(id="q1", stage="diagnose", question="What framework?")
        assert q.answered is False
        assert q.answer == ""

    def test_answered(self):
        q = Question(id="q1", stage="diagnose", question="What framework?",
                     answered=True, answer="Django")
        assert q.answered is True


# ── Decomposition schemas ───────────────────────────────────────────


class TestDecomposeQuestion:
    def test_basic(self):
        q = DecomposeQuestion(question="What DB?", importance="blocking")
        assert q.importance == "blocking"
        assert q.context == ""

    def test_defaults(self):
        q = DecomposeQuestion(question="Style preference?")
        assert q.importance == "clarifying"


class TestDecomposeQuestionsResult:
    def test_basic(self):
        r = DecomposeQuestionsResult(
            questions=[DecomposeQuestion(question="What DB?", importance="blocking")],
            assumptions=["PostgreSQL preferred"],
            confidence=0.7,
            reasoning="Need to know the DB",
        )
        assert len(r.questions) == 1
        assert r.assumptions == ["PostgreSQL preferred"]
        assert r.consternation == ""
        assert r.decisions == []

    def test_with_decisions(self):
        r = DecomposeQuestionsResult(
            decisions=[
                DecomposeDecision(
                    ambiguity="Token format",
                    decision="JWT RS256",
                    rationale="Portable and stateless",
                ),
            ],
            questions=[],
            confidence=0.9,
            reasoning="Resolved all ambiguities",
        )
        assert len(r.decisions) == 1
        assert r.decisions[0].ambiguity == "Token format"
        assert len(r.questions) == 0


class TestComponent:
    def test_basic(self):
        c = Component(
            id="auth", name="Authentication", description="Login flow",
            dependencies=[], estimated_complexity="moderate",
        )
        assert c.id == "auth"
        assert c.affected_files == []

    def test_with_dependencies(self):
        c = Component(
            id="api", name="API layer", description="REST endpoints",
            dependencies=["auth", "db"],
        )
        assert len(c.dependencies) == 2


class TestComponentMapResult:
    def test_basic(self):
        r = ComponentMapResult(
            one_sentence="Build a user auth system",
            components=[
                Component(id="auth", name="Auth", description="Login"),
                Component(id="db", name="Database", description="Schema"),
            ],
            interaction_summary="Auth depends on DB",
            confidence=0.8,
            reasoning="Clear decomposition",
        )
        assert len(r.components) == 2
        assert r.one_sentence == "Build a user auth system"


class TestContractSetResult:
    def test_basic(self):
        r = ContractSetResult(
            contracts=[
                Contract(
                    component_id="auth",
                    inputs=["email", "password"],
                    outputs=["token"],
                    invariants=["token expires in 24h"],
                ),
            ],
            cross_component_constraints=["All tokens use JWT"],
            confidence=0.85,
            reasoning="Standard auth contract",
        )
        assert len(r.contracts) == 1
        assert r.contracts[0].invariants == ["token expires in 24h"]


class TestImplementationOrderResult:
    def test_basic(self):
        r = ImplementationOrderResult(
            steps=[
                ImplementationStep(component_id="db", order=1, rationale="Foundation"),
                ImplementationStep(component_id="auth", order=2, rationale="Depends on DB"),
            ],
            parallel_groups=[["db"], ["auth"]],
            confidence=0.9,
            reasoning="Topological sort",
        )
        assert len(r.steps) == 2
        assert r.steps[0].order == 1


class TestDecompositionPlan:
    def test_empty(self):
        p = DecompositionPlan()
        assert p.questions is None
        assert p.component_map is None
        assert p.answers == {}

    def test_assembled(self):
        p = DecompositionPlan(
            component_map=ComponentMapResult(
                one_sentence="Build auth",
                components=[Component(id="a", name="A", description="A")],
                interaction_summary="None",
                confidence=0.8,
                reasoning="Simple",
            ),
            answers={"What DB?": "PostgreSQL"},
        )
        assert p.component_map is not None
        assert len(p.answers) == 1


class TestComponentBuildResult:
    def test_completed(self):
        r = ComponentBuildResult(
            component_id="auth",
            status="completed",
            stages_completed=["architect", "locate", "execute", "test", "verify", "review"],
            files_changed=["src/auth.py"],
        )
        assert r.status == "completed"
        assert len(r.stages_completed) == 6

    def test_failed(self):
        r = ComponentBuildResult(
            component_id="db",
            status="failed",
            stages_completed=["architect", "locate"],
            issues=["Could not find schema files"],
        )
        assert r.status == "failed"
        assert len(r.issues) == 1


class TestReviewEscalationResult:
    def test_approve(self):
        r = ReviewEscalationResult(
            decision="approve", confidence=0.85,
            rationale="Code works correctly",
        )
        assert r.decision == "approve"
        assert r.conditions == []
        assert r.consternation == ""

    def test_force_approve_with_conditions(self):
        r = ReviewEscalationResult(
            decision="force_approve", confidence=0.55,
            rationale="Code is adequate but has rough edges",
            conditions=["Review error handling manually", "Add integration tests later"],
        )
        assert r.decision == "force_approve"
        assert len(r.conditions) == 2

    def test_reject(self):
        r = ReviewEscalationResult(
            decision="reject", confidence=0.9,
            rationale="Critical bug in validation logic",
        )
        assert r.decision == "reject"
