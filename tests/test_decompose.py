"""Tests for decompose agent — 4-phase epic task decomposition."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from swarm.agents.decompose import (
    QUESTIONS_SYSTEM,
    ask_questions,
    build_component_map,
    define_contracts,
    plan_implementation_order,
)
from swarm.schemas import (
    Component,
    ComponentMapResult,
    Contract,
    ContractSetResult,
    DecomposeDecision,
    DecomposeQuestion,
    DecomposeQuestionsResult,
    DiagnoseResult,
    ImplementationOrderResult,
    ImplementationStep,
)


def _diagnose(complexity="epic"):
    return DiagnoseResult(
        task_type="feature", complexity=complexity, confidence=0.8,
        language="python", framework="pytest", summary="Build auth system",
        reasoning="Large task requiring decomposition",
        constraints=["Must be backward compatible"],
        risks=["Scope creep"],
    )


def _questions_result():
    return DecomposeQuestionsResult(
        decisions=[
            DecomposeDecision(
                ambiguity="Token format",
                decision="JWT with RS256",
                rationale="Portable, stateless, widely supported",
            ),
        ],
        questions=[
            DecomposeQuestion(question="What database?", importance="blocking"),
            DecomposeQuestion(question="OAuth or custom?", importance="clarifying"),
        ],
        assumptions=["PostgreSQL", "JWT tokens"],
        confidence=0.7,
        reasoning="Need DB clarification",
    )


def _component_map():
    return ComponentMapResult(
        one_sentence="Build a user authentication system with JWT tokens",
        components=[
            Component(id="db_schema", name="Database Schema", description="User tables",
                     dependencies=[], estimated_complexity="simple"),
            Component(id="auth_core", name="Auth Core", description="Login/logout logic",
                     dependencies=["db_schema"], estimated_complexity="moderate"),
            Component(id="api_layer", name="API Layer", description="REST endpoints",
                     dependencies=["auth_core"], estimated_complexity="moderate"),
        ],
        interaction_summary="API depends on Auth Core, which depends on DB Schema",
        confidence=0.85,
        reasoning="Clear three-layer decomposition",
    )


def _contracts():
    return ContractSetResult(
        contracts=[
            Contract(component_id="db_schema", inputs=["user fields"], outputs=["User model"],
                    invariants=["email is unique"], error_modes=["duplicate email"]),
            Contract(component_id="auth_core", inputs=["email", "password"], outputs=["JWT token"],
                    invariants=["token expires in 24h"]),
            Contract(component_id="api_layer", inputs=["HTTP requests"], outputs=["JSON responses"],
                    invariants=["401 on invalid token"]),
        ],
        cross_component_constraints=["All errors use WanderError format"],
        confidence=0.9,
        reasoning="Standard auth contracts",
    )


def _impl_order():
    return ImplementationOrderResult(
        steps=[
            ImplementationStep(component_id="db_schema", order=1, rationale="Foundation"),
            ImplementationStep(component_id="auth_core", order=2, rationale="Depends on DB"),
            ImplementationStep(component_id="api_layer", order=3, rationale="Depends on Auth"),
        ],
        parallel_groups=[["db_schema"], ["auth_core"], ["api_layer"]],
        confidence=0.9,
        reasoning="Topological sort",
    )


def _mock_agent(return_value):
    agent = MagicMock()
    agent.assess = AsyncMock(return_value=(return_value, 200, 100))
    return agent


class TestQuestionsSystemPrompt:
    """Verify the questions system prompt contains deep ambiguity guidance."""

    def test_has_interface_boundary_guidance(self):
        assert "INTERFACE BOUNDAR" in QUESTIONS_SYSTEM

    def test_has_format_propagation_guidance(self):
        assert "FORMAT PROPAGATION" in QUESTIONS_SYSTEM

    def test_has_scope_boundary_guidance(self):
        assert "SCOPE BOUNDAR" in QUESTIONS_SYSTEM

    def test_has_edge_case_tension_guidance(self):
        assert "EDGE CASE TENSION" in QUESTIONS_SYSTEM

    def test_has_implicit_assumptions_guidance(self):
        assert "IMPLICIT ASSUMPTION" in QUESTIONS_SYSTEM

    def test_instructs_resolve_not_ask(self):
        assert "RESOLVE" in QUESTIONS_SYSTEM
        assert "NOT a questionnaire" in QUESTIONS_SYSTEM

    def test_warns_against_too_many_questions(self):
        assert "0-3" in QUESTIONS_SYSTEM

    def test_primary_output_is_decisions(self):
        assert "PRIMARY output" in QUESTIONS_SYSTEM


class TestAskQuestions:
    @pytest.mark.asyncio
    async def test_returns_decisions_and_questions(self):
        result = _questions_result()
        agent = _mock_agent(result)
        r, in_tok, out_tok = await ask_questions(agent, "Build auth", _diagnose())
        assert len(r.decisions) == 1
        assert r.decisions[0].ambiguity == "Token format"
        assert len(r.questions) == 2
        assert r.questions[0].importance == "blocking"
        assert in_tok == 200

    @pytest.mark.asyncio
    async def test_passes_diagnose_context(self):
        result = _questions_result()
        agent = _mock_agent(result)
        await ask_questions(agent, "Build auth", _diagnose())
        call_args = agent.assess.call_args
        prompt = call_args[0][1]  # second positional arg
        assert "epic" in prompt.lower() or "Build auth" in prompt

    @pytest.mark.asyncio
    async def test_with_learnings(self):
        result = _questions_result()
        agent = _mock_agent(result)
        await ask_questions(agent, "Build auth", _diagnose(), learnings_context="Use JWT")
        call_args = agent.assess.call_args
        system = call_args[0][2]  # third positional arg
        assert "JWT" in system


class TestBuildComponentMap:
    @pytest.mark.asyncio
    async def test_returns_components(self):
        result = _component_map()
        agent = _mock_agent(result)
        r, _, _ = await build_component_map(agent, "Build auth", _diagnose())
        assert len(r.components) == 3
        assert r.one_sentence == "Build a user authentication system with JWT tokens"

    @pytest.mark.asyncio
    async def test_with_answers(self):
        result = _component_map()
        agent = _mock_agent(result)
        answers = {"What database?": "PostgreSQL"}
        await build_component_map(agent, "Build auth", _diagnose(), answers=answers)
        call_args = agent.assess.call_args
        prompt = call_args[0][1]
        assert "PostgreSQL" in prompt

    @pytest.mark.asyncio
    async def test_components_have_ids(self):
        result = _component_map()
        agent = _mock_agent(result)
        r, _, _ = await build_component_map(agent, "Build auth", _diagnose())
        ids = [c.id for c in r.components]
        assert "db_schema" in ids
        assert "auth_core" in ids


class TestDefineContracts:
    @pytest.mark.asyncio
    async def test_returns_contracts(self):
        result = _contracts()
        agent = _mock_agent(result)
        r, _, _ = await define_contracts(agent, "Build auth", _component_map())
        assert len(r.contracts) == 3
        assert r.cross_component_constraints[0] == "All errors use WanderError format"

    @pytest.mark.asyncio
    async def test_contracts_match_components(self):
        result = _contracts()
        agent = _mock_agent(result)
        r, _, _ = await define_contracts(agent, "Build auth", _component_map())
        contract_ids = {c.component_id for c in r.contracts}
        component_ids = {c.id for c in _component_map().components}
        assert contract_ids == component_ids

    @pytest.mark.asyncio
    async def test_prompt_includes_components(self):
        result = _contracts()
        agent = _mock_agent(result)
        await define_contracts(agent, "Build auth", _component_map())
        call_args = agent.assess.call_args
        prompt = call_args[0][1]
        assert "db_schema" in prompt
        assert "auth_core" in prompt


class TestPlanImplementationOrder:
    @pytest.mark.asyncio
    async def test_returns_ordered_steps(self):
        result = _impl_order()
        agent = _mock_agent(result)
        r, _, _ = await plan_implementation_order(agent, _component_map(), _contracts())
        assert len(r.steps) == 3
        assert r.steps[0].order == 1
        assert r.steps[0].component_id == "db_schema"

    @pytest.mark.asyncio
    async def test_parallel_groups(self):
        result = _impl_order()
        agent = _mock_agent(result)
        r, _, _ = await plan_implementation_order(agent, _component_map(), _contracts())
        assert len(r.parallel_groups) == 3

    @pytest.mark.asyncio
    async def test_prompt_includes_dependencies(self):
        result = _impl_order()
        agent = _mock_agent(result)
        await plan_implementation_order(agent, _component_map(), _contracts())
        call_args = agent.assess.call_args
        prompt = call_args[0][1]
        assert "db_schema" in prompt

    @pytest.mark.asyncio
    async def test_confidence_propagated(self):
        result = _impl_order()
        agent = _mock_agent(result)
        r, _, _ = await plan_implementation_order(agent, _component_map(), _contracts())
        assert r.confidence == 0.9
