"""Envelope enforcement tests — verify context boundaries.

Each agent must only see what the architecture allows. These tests mock
agent.assess, call each agent function, and inspect the prompt string
to verify exclusions.

Architecture-defined boundaries (9 stages):
  Diagnose:   Nothing excluded (first stage)
  Architect:  EXCLUDED diagnose.reasoning
  Locate:     EXCLUDED architect.reasoning, diagnose.reasoning
  Execute:    EXCLUDED all .reasoning, locate.search_strategy
  Test:       EXCLUDED execute.explanation, all .reasoning
  Review:     EXCLUDED execute.explanation, test.test_strategy, all .reasoning
  Integrate:  EXCLUDED all .reasoning from all stages
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from swarm.agents.architect import architect
from swarm.agents.execute import execute
from swarm.agents.integrate import integrate
from swarm.agents.locate import locate
from swarm.agents.review import review, review_sub_pass, review_full
from swarm.agents.test import generate_tests
from swarm.schemas import (
    ArchitectResult,
    ArchitectStep,
    DiagnoseResult,
    ExecuteResult,
    FileAction,
    FileLocation,
    IntegrateResult,
    LocateResult,
    ReviewSubVerdict,
    ReviewVerdict,
    TestFile,
    TestResult,
    VerifyResult,
)


# ── Test data with distinctive marker strings ───────────────────────

DIAGNOSE = DiagnoseResult(
    task_type="feature", complexity="simple", confidence=0.9,
    language="python", framework="Django",
    summary="DIAGNOSE_SUMMARY_MARKER_XYZ",
    reasoning="DIAGNOSE_REASONING_MARKER_ABC",
    constraints=["backward compat"],
    risks=["could break auth"],
)

ARCHITECT = ArchitectResult(
    approach="Modular approach",
    steps=[ArchitectStep(
        description="Add login endpoint",
        files_involved=["src/auth.py"],
        risk="low",
    )],
    affected_files=["src/auth.py"],
    new_files=["src/auth_test.py"],
    reasoning="ARCHITECT_REASONING_MARKER_DEF",
    confidence=0.85,
)

LOCATE = LocateResult(
    files=[
        FileLocation(path="src/auth.py", relevance="primary",
                     reasoning="LOCATE_FILE_REASONING_MARKER_GHI"),
    ],
    confidence=0.8,
    search_strategy="LOCATE_STRATEGY_MARKER_JKL",
)

EXECUTE = ExecuteResult(
    files=[FileAction(
        path="src/auth.py", action="modify",
        original="return None", content="return user",
        explanation="EXECUTE_FILE_EXPLANATION_MARKER",
    )],
    explanation="EXECUTE_EXPLANATION_MARKER_MNO",
    confidence=0.85,
)

TESTS = TestResult(
    test_files=[TestFile(path="tests/test_auth.py", content="def test_login(): assert True")],
    test_strategy="TEST_STRATEGY_MARKER_PQR",
    confidence=0.8,
)

VERIFY = VerifyResult(
    compiles=True, lint_clean=True, tests_passing=1, tests_total=1,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_agent():
    """Create a mock agent that captures the prompt."""
    agent = MagicMock()
    captured = {"prompt": "", "system": ""}

    async def capture_assess(schema, prompt, system, **kwargs):
        captured["prompt"] = prompt
        captured["system"] = system
        if schema is ArchitectResult:
            return ARCHITECT, 100, 50
        if schema is LocateResult:
            return LOCATE, 100, 50
        if schema is ExecuteResult:
            return EXECUTE, 100, 50
        if schema is TestResult:
            return TESTS, 100, 50
        if schema is ReviewVerdict:
            return ReviewVerdict(
                verdict="approve", confidence=0.85, issues=[],
                risks=[], missed_edge_cases=[], reasoning="Clean",
            ), 100, 50
        if schema is ReviewSubVerdict:
            return ReviewSubVerdict(
                pass_type="structural", verdict="approve", confidence=0.85,
            ), 100, 50
        if schema is IntegrateResult:
            return IntegrateResult(
                coherent=True, confidence=0.9, reasoning="All good",
            ), 100, 50
        raise RuntimeError(f"Unexpected schema: {schema.__name__}")

    agent.assess = AsyncMock(side_effect=capture_assess)
    return agent, captured


def _combined(captured: dict) -> str:
    """Combine prompt + system for full text search."""
    return captured["prompt"] + "\n" + captured["system"]


# ── Architect boundary tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_architect_excludes_diagnose_reasoning():
    """Architect must not see diagnose.reasoning."""
    agent, captured = _make_agent()
    await architect(agent, "Add login", DIAGNOSE)
    text = _combined(captured)
    assert "DIAGNOSE_REASONING_MARKER_ABC" not in text


@pytest.mark.asyncio
async def test_architect_includes_task_type_and_language():
    """Architect SHOULD see diagnose.task_type and language."""
    agent, captured = _make_agent()
    await architect(agent, "Add login", DIAGNOSE)
    text = _combined(captured)
    assert "feature" in text
    assert "python" in text


@pytest.mark.asyncio
async def test_architect_includes_constraints():
    """Architect SHOULD see diagnose.constraints."""
    agent, captured = _make_agent()
    await architect(agent, "Add login", DIAGNOSE)
    text = _combined(captured)
    assert "backward compat" in text


# ── Locate boundary tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_locate_excludes_architect_reasoning(tmp_path):
    """Locate must not see architect.reasoning."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("code")
    agent, captured = _make_agent()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("swarm.agents.locate.get_file_tree", AsyncMock(return_value="src/auth.py"))
        await locate(agent, "Add login", DIAGNOSE, ARCHITECT, tmp_path)

    text = _combined(captured)
    assert "ARCHITECT_REASONING_MARKER_DEF" not in text


@pytest.mark.asyncio
async def test_locate_excludes_diagnose_reasoning(tmp_path):
    """Locate must not see diagnose.reasoning."""
    agent, captured = _make_agent()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("swarm.agents.locate.get_file_tree", AsyncMock(return_value="src/auth.py"))
        await locate(agent, "Add login", DIAGNOSE, ARCHITECT, tmp_path)

    text = _combined(captured)
    assert "DIAGNOSE_REASONING_MARKER_ABC" not in text


@pytest.mark.asyncio
async def test_locate_includes_affected_files(tmp_path):
    """Locate SHOULD see architect.affected_files."""
    agent, captured = _make_agent()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("swarm.agents.locate.get_file_tree", AsyncMock(return_value="src/auth.py"))
        await locate(agent, "Add login", DIAGNOSE, ARCHITECT, tmp_path)

    text = _combined(captured)
    assert "src/auth.py" in text


# ── Execute boundary tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_excludes_all_reasoning(tmp_path):
    """Execute must not see any .reasoning field."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "auth.py").write_text("def login(): return None")
    agent, captured = _make_agent()

    await execute(agent, "Add login", DIAGNOSE, LOCATE, tmp_path)

    text = _combined(captured)
    assert "DIAGNOSE_REASONING_MARKER_ABC" not in text


@pytest.mark.asyncio
async def test_execute_excludes_locate_strategy(tmp_path):
    """Execute must not see locate.search_strategy."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "auth.py").write_text("def login(): return None")
    agent, captured = _make_agent()

    await execute(agent, "Add login", DIAGNOSE, LOCATE, tmp_path)

    text = _combined(captured)
    assert "LOCATE_STRATEGY_MARKER_JKL" not in text


@pytest.mark.asyncio
async def test_execute_excludes_locate_file_reasoning(tmp_path):
    """Execute must not see locate.files[].reasoning."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "auth.py").write_text("def login(): return None")
    agent, captured = _make_agent()

    await execute(agent, "Add login", DIAGNOSE, LOCATE, tmp_path)

    text = _combined(captured)
    assert "LOCATE_FILE_REASONING_MARKER_GHI" not in text


@pytest.mark.asyncio
async def test_execute_includes_file_content(tmp_path):
    """Execute SHOULD see actual file content."""
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "auth.py").write_text("def login(): return None")
    agent, captured = _make_agent()

    await execute(agent, "Add login", DIAGNOSE, LOCATE, tmp_path)

    text = _combined(captured)
    assert "def login" in text


# ── Test agent boundary tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_tester_excludes_execute_explanation(tmp_path):
    """Tester must not see execute.explanation."""
    agent, captured = _make_agent()
    await generate_tests(agent, "Add login", DIAGNOSE, LOCATE, EXECUTE, tmp_path)
    text = _combined(captured)
    assert "EXECUTE_EXPLANATION_MARKER_MNO" not in text


@pytest.mark.asyncio
async def test_tester_excludes_diagnose_reasoning(tmp_path):
    """Tester must not see diagnose.reasoning."""
    agent, captured = _make_agent()
    await generate_tests(agent, "Add login", DIAGNOSE, LOCATE, EXECUTE, tmp_path)
    text = _combined(captured)
    assert "DIAGNOSE_REASONING_MARKER_ABC" not in text


@pytest.mark.asyncio
async def test_tester_includes_code_diffs(tmp_path):
    """Tester SHOULD see the code diffs."""
    agent, captured = _make_agent()
    await generate_tests(agent, "Add login", DIAGNOSE, LOCATE, EXECUTE, tmp_path)
    text = _combined(captured)
    assert "return None" in text
    assert "return user" in text


# ── Review boundary tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_excludes_execute_explanation():
    """Review must not see execute.explanation."""
    agent, captured = _make_agent()
    await review(agent, "Add login", EXECUTE, TESTS, VERIFY, None)
    text = _combined(captured)
    assert "EXECUTE_EXPLANATION_MARKER_MNO" not in text


@pytest.mark.asyncio
async def test_review_excludes_test_strategy():
    """Review must not see test.test_strategy."""
    agent, captured = _make_agent()
    await review(agent, "Add login", EXECUTE, TESTS, VERIFY, None)
    text = _combined(captured)
    assert "TEST_STRATEGY_MARKER_PQR" not in text


@pytest.mark.asyncio
async def test_review_includes_code_diffs():
    """Review SHOULD see code diffs, test code, and verification results."""
    agent, captured = _make_agent()
    await review(agent, "Add login", EXECUTE, TESTS, VERIFY, None)
    text = _combined(captured)
    assert "return None" in text
    assert "return user" in text
    assert "test_login" in text
    assert "Compiles: True" in text


@pytest.mark.asyncio
async def test_review_prompt_mentions_adversarial_role():
    """Review system prompt should frame the role as adversarial."""
    agent, captured = _make_agent()
    await review(agent, "Add login", EXECUTE, TESTS, VERIFY, None)
    system = captured["system"]
    assert "adversarial" in system.lower()
    assert "weaknesses" in system.lower() or "risks" in system.lower()


# ── Integrate boundary tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_integrate_excludes_all_reasoning():
    """Integrate must not see any .reasoning."""
    agent, captured = _make_agent()
    await integrate(agent, "Add login", EXECUTE, TESTS, VERIFY)
    text = _combined(captured)
    assert "DIAGNOSE_REASONING_MARKER_ABC" not in text
    assert "ARCHITECT_REASONING_MARKER_DEF" not in text
    assert "EXECUTE_EXPLANATION_MARKER_MNO" not in text


@pytest.mark.asyncio
async def test_integrate_includes_all_file_changes():
    """Integrate SHOULD see all file changes."""
    agent, captured = _make_agent()
    await integrate(agent, "Add login", EXECUTE, TESTS, VERIFY)
    text = _combined(captured)
    assert "return None" in text
    assert "return user" in text


# ── Signal field guidance tests ──────────────────────────────────────


@pytest.mark.asyncio
async def test_diagnose_prompt_mentions_signals():
    """Diagnose system prompt should mention consternation and escalation."""
    from swarm.agents.diagnose import SYSTEM_PROMPT
    assert "consternation" in SYSTEM_PROMPT.lower()
    assert "escalation" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_architect_prompt_mentions_signals():
    """Architect system prompt should mention consternation and escalation."""
    from swarm.agents.architect import SYSTEM_PROMPT
    assert "consternation" in SYSTEM_PROMPT.lower()
    assert "escalation" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_locate_prompt_mentions_signals():
    """Locate system prompt should mention consternation and escalation."""
    from swarm.agents.locate import SYSTEM_PROMPT
    assert "consternation" in SYSTEM_PROMPT.lower()
    assert "escalation" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_execute_prompt_mentions_signals():
    """Execute system prompt should mention consternation and escalation."""
    from swarm.agents.execute import SYSTEM_PROMPT
    assert "consternation" in SYSTEM_PROMPT.lower()
    assert "escalation" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_tester_prompt_mentions_signals():
    """Tester system prompt should mention consternation and escalation."""
    from swarm.agents.test import SYSTEM_PROMPT
    assert "consternation" in SYSTEM_PROMPT.lower()
    assert "escalation" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_review_prompt_mentions_signals():
    """Review system prompt should mention consternation and escalation."""
    from swarm.agents.review import SYSTEM_PROMPT
    assert "consternation" in SYSTEM_PROMPT.lower()
    assert "escalation" in SYSTEM_PROMPT.lower()


@pytest.mark.asyncio
async def test_integrate_prompt_mentions_signals():
    """Integrate system prompt should mention consternation and escalation."""
    from swarm.agents.integrate import SYSTEM_PROMPT
    assert "consternation" in SYSTEM_PROMPT.lower()
    assert "escalation" in SYSTEM_PROMPT.lower()


# ── Review sub-pass boundary tests ─────────────────────────────────


@pytest.mark.asyncio
async def test_sub_pass_excludes_execute_explanation():
    """Each sub-pass must not see execute.explanation."""
    agent, captured = _make_agent()
    await review_sub_pass(agent, "structural", "Add login", EXECUTE, TESTS, VERIFY, None)
    text = _combined(captured)
    assert "EXECUTE_EXPLANATION_MARKER_MNO" not in text


@pytest.mark.asyncio
async def test_sub_pass_structural_prompt():
    """Structural sub-pass mentions module boundaries."""
    agent, captured = _make_agent()
    await review_sub_pass(agent, "structural", "Add login", EXECUTE)
    system = captured["system"]
    assert "structural" in system.lower()
    assert "module" in system.lower() or "api" in system.lower()


@pytest.mark.asyncio
async def test_sub_pass_logic_prompt():
    """Logic sub-pass mentions correctness."""
    agent, captured = _make_agent()
    await review_sub_pass(agent, "logic", "Add login", EXECUTE)
    system = captured["system"]
    assert "logic" in system.lower()
    assert "correctness" in system.lower() or "edge case" in system.lower()


@pytest.mark.asyncio
async def test_review_full_aggregation():
    """review_full aggregates sub-verdicts."""
    agent, captured = _make_agent()
    verdict, tok_in, tok_out = await review_full(
        agent, "Add login", EXECUTE, TESTS, VERIFY, None,
        sub_passes=["structural", "logic"],
    )
    assert len(verdict.sub_verdicts) == 2
    assert verdict.verdict in ("approve", "reject")
    assert tok_in > 0

@pytest.mark.asyncio
async def test_review_full_any_reject():
    """review_full rejects if any sub-pass rejects."""
    agent = MagicMock()

    call_count = {"n": 0}
    async def mixed_assess(schema, prompt, system, **kwargs):
        call_count["n"] += 1
        if schema is ReviewSubVerdict:
            if call_count["n"] == 1:
                return ReviewSubVerdict(pass_type="structural", verdict="approve", confidence=0.9), 50, 25
            return ReviewSubVerdict(pass_type="logic", verdict="reject", confidence=0.5, issues=["bug"]), 50, 25
        return ReviewVerdict(verdict="approve", confidence=0.8, issues=[], reasoning="ok"), 50, 25

    agent.assess = AsyncMock(side_effect=mixed_assess)

    verdict, _, _ = await review_full(
        agent, "Add login", EXECUTE, TESTS, VERIFY, None,
        sub_passes=["structural", "logic"],
    )
    assert verdict.verdict == "reject"
    assert "bug" in verdict.issues


@pytest.mark.asyncio
async def test_review_full_no_sub_passes():
    """review_full without sub_passes falls back to normal review."""
    agent, captured = _make_agent()
    verdict, _, _ = await review_full(
        agent, "Add login", EXECUTE, TESTS, VERIFY, None,
    )
    assert verdict.verdict == "approve"
    assert verdict.sub_verdicts == []
