"""Pipeline manager — formation-driven orchestrator.

Components:
  SignalBus         — Collects consternation/escalation signals from agents
  PipelineState     — Accumulated state across pipeline stages
  PipelineManager   — Iterates formation stages, dispatches handlers,
                      processes signals, manages gates and break points

The manager does NOT write code or review code. It manages flow, context
boundaries, and signal processing. Mechanical decisions only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from swarm.schemas import (
    ArchitectResult,
    ClassifiedIssue,
    ComponentBuildResult,
    DecompositionPlan,
    DiagnoseResult,
    ExecuteResult,
    Formation,
    FormationStage,
    GateResult,
    GuardianResult,
    IntegrateResult,
    IssueClassification,
    LocateResult,
    PipelineSignal,
    ProjectRun,
    ReviewEscalationResult,
    ReviewVerdict,
    StageRecord,
    SubmitResult,
    TestResult,
    VerifyResult,
)

logger = logging.getLogger(__name__)


# ── SignalBus ────────────────────────────────────────────────────────


class SignalBus:
    """Collects and processes signals across pipeline stages.

    Signal processing rules (mechanical, no LLM):
    - Escalation from any agent -> pause pipeline (needs human)
    - 2+ consternation signals -> pause pipeline (systemic concern)
    - Single consternation -> log warning, continue
    """

    def __init__(self) -> None:
        self._signals: list[PipelineSignal] = []

    @property
    def signals(self) -> list[PipelineSignal]:
        return list(self._signals)

    def emit(self, stage: str, signal_type: str, message: str) -> None:
        """Record a signal from an agent."""
        if signal_type not in ("consternation", "escalation"):
            return
        self._signals.append(PipelineSignal(
            stage=stage,
            signal_type=signal_type,
            message=message,
        ))
        logger.info("Signal [%s] from %s: %s", signal_type, stage, message)

    def collect_from_result(self, stage: str, result: object) -> None:
        """Extract consternation/escalation signals from an agent result."""
        consternation = getattr(result, "consternation", "")
        escalation = getattr(result, "escalation", "")
        if consternation:
            self.emit(stage, "consternation", consternation)
        if escalation:
            self.emit(stage, "escalation", escalation)

    def should_continue(self) -> ContinueDecision:
        """Decide whether the pipeline should continue, pause, or abort."""
        escalations = [s for s in self._signals if s.signal_type == "escalation"]
        consternations = [s for s in self._signals if s.signal_type == "consternation"]

        if escalations:
            return ContinueDecision(
                action="pause",
                reason=f"Escalation from {escalations[0].stage}: {escalations[0].message}",
            )

        if len(consternations) >= 2:
            stages = ", ".join(s.stage for s in consternations)
            return ContinueDecision(
                action="pause",
                reason=f"Multiple consternation signals ({stages}) — systemic concern",
            )

        if len(consternations) == 1:
            logger.warning(
                "Single consternation from %s: %s — continuing",
                consternations[0].stage, consternations[0].message,
            )

        return ContinueDecision(action="continue", reason="")

    def clear(self) -> None:
        self._signals.clear()


@dataclass
class ContinueDecision:
    """Result of signal processing — should the pipeline continue?"""
    action: str  # "continue", "pause"
    reason: str = ""


# ── Gate utilities ───────────────────────────────────────────────────


def check_break_point(stage: FormationStage) -> bool:
    """Check if a stage has a break point that should pause execution."""
    return stage.break_point == "pause"


def should_pause_at_gate(
    gate_result: GateResult,
    confidence: float,
    threshold: float,
    has_consternation: bool = False,
    break_point: bool = False,
) -> GateResult:
    """Determine if a gate result should be a pause instead of pass/fail.

    Pause triggers:
    - Confidence in borderline zone: threshold - 0.1 <= confidence < threshold
    - Consternation signal on an otherwise-passing result
    - Formation break point at this stage
    """
    if gate_result.passed and break_point:
        return GateResult(
            passed=True, paused=True,
            reason=f"Break point: {gate_result.reason}",
        )

    if gate_result.passed and has_consternation:
        return GateResult(
            passed=True, paused=True,
            reason=f"Consternation on passing result: {gate_result.reason}",
        )

    borderline_floor = threshold - 0.1
    if not gate_result.passed and borderline_floor <= confidence < threshold:
        return GateResult(
            passed=False, paused=True,
            reason=f"Borderline ({confidence:.2f} in [{borderline_floor:.2f}, {threshold:.2f})): {gate_result.reason}",
            timeout_minutes=60,
        )

    return gate_result


# ── Pipeline state ───────────────────────────────────────────────────


@dataclass
class PipelineState:
    """Accumulated state across pipeline stages."""
    diagnose_result: DiagnoseResult | None = None
    architect_result: ArchitectResult | None = None
    locate_result: LocateResult | None = None
    execute_result: ExecuteResult | None = None
    test_result: TestResult | None = None
    verify_result: VerifyResult | None = None
    guardian_result: GuardianResult | None = None
    review_verdict: ReviewVerdict | None = None
    integrate_result: IntegrateResult | None = None
    submit_result: SubmitResult | None = None
    repo_path: Path | None = None
    working_scope: str = ""
    cascade_ctx: str = ""
    file_cascade_ctx: str = ""
    learnings_applied: list[str] = field(default_factory=list)
    review_loops: int = 0
    review_issues_for_reexecute: list[str] = field(default_factory=list)
    _perspective_shift: str = ""  # Prepended to review on re-engagement
    # Architect review state
    architect_review_loops: int = 0
    architect_issues_for_replan: list[str] = field(default_factory=list)
    _architect_perspective_shift: str = ""
    _resume_message: str = ""  # Human message from resume, injected as context
    # Epic pipeline state
    decomposition_plan: DecompositionPlan | None = None
    component_results: list[ComponentBuildResult] = field(default_factory=list)
    current_component_id: str = ""


# ── PipelineManager ────────────────────────────────────────────────


class PipelineManager:
    """Formation-driven orchestrator — iterates stages, dispatches handlers.

    Creates all components, drives the stage loop, handles signals,
    gates, break points, review loop-back, and competition.
    """

    def __init__(
        self,
        pm: object,                  # ProjectManager
        run: ProjectRun,
        task: str,
        config: object,              # ProjectConfig
        swarm_config: object,        # SwarmConfig
        budget: object,              # BudgetTracker
        agent: object,               # AgentBase
        human_io: object | None = None,  # HumanIO
        redo_components: set[str] | None = None,  # Components to force rebuild
    ) -> None:
        self._pm = pm
        self._run = run
        self._task = task
        self._config = config
        self._swarm_config = swarm_config
        self._budget = budget
        self._agent = agent
        self._human_io = human_io
        self._redo_components = redo_components or set()

        self._signal_bus = SignalBus()
        self._state = PipelineState()
        self._formation: Formation | None = None
        self._start_time: float | None = None

        # Lazy imports to avoid circular deps
        from swarm.guardian import GuardianAgent
        from swarm.memory import ContextCascade, LearningsStore
        from swarm.perturbation import PerturbationEngine
        from swarm.scoring import default_registry
        from swarm.well_formedness import WellFormednessChecker

        self._guardian = GuardianAgent()
        self._learnings = LearningsStore(base_dir=Path(run.project_dir))
        self._cascade = ContextCascade()
        self._perturbation = PerturbationEngine()
        self._score_registry = default_registry()
        self._wf_checker = WellFormednessChecker(self._score_registry)

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def formation(self) -> Formation | None:
        return self._formation

    @property
    def signal_bus(self) -> SignalBus:
        return self._signal_bus

    async def run(self) -> None:
        """Execute the full pipeline."""
        self._start_time = time.monotonic()

        # Stage 1: Diagnose (always first)
        gate = await self._handle_diagnose()
        if gate and not gate.passed:
            self._run.fail(gate.reason)
            self._pm.save_state(self._run)
            return

        # Epic tasks: suppress diagnose escalation (decomposition handles it)
        if self._state.diagnose_result and self._state.diagnose_result.complexity == "epic":
            self._signal_bus._signals = [
                s for s in self._signal_bus._signals
                if not (s.stage == "diagnose" and s.signal_type == "escalation")
            ]

        # Signal check after diagnose
        decision = self._signal_bus.should_continue()
        if decision.action == "pause":
            self._run.pause(f"Signal: {decision.reason}")
            self._pm.save_state(self._run)
            return

        # Select formation
        from swarm.formation import select_formation
        self._formation = select_formation(self._state.diagnose_result, self._config)
        self._run.formation = self._formation.name

        # Well-formedness: completeness check before starting
        complete, violations = self._wf_checker.check_completeness(self._formation)
        if not complete:
            for v in violations:
                logger.warning("Well-formedness: %s", v)

        # Epic formation: run decompose then component loop
        if self._formation.name == "epic":
            await self._run_epic_pipeline()
            return

        # Iterate remaining stages (skip diagnose, already done)
        for i, stage in enumerate(self._formation.stages):
            if stage.name == "diagnose":
                continue

            self._run.current_stage_index = i

            gate = await self._handle_stage(stage)

            # Post-stage: signals + gate + break point + well-formedness
            post_result = await self._post_stage(stage, gate)
            if post_result == "pause":
                self._pm.save_state(self._run)
                return
            if post_result == "fail":
                self._pm.save_state(self._run)
                return

        self._run.complete()
        self._pm.save_state(self._run)

    async def resume(self, start_index: int, resume_message: str = "") -> None:
        """Resume pipeline from a saved stage index.

        Args:
            start_index: Stage index to resume from.
            resume_message: Optional human message injected as context for the
                next stage. Also clears prior escalation signals so the pipeline
                doesn't immediately re-pause on stale signals.
        """
        self._start_time = time.monotonic()

        # Inject resume message as cascade context for subsequent stages
        if resume_message:
            self._state._resume_message = resume_message
            # Clear stale signals — the human has acknowledged the pause
            self._signal_bus.clear()
            logger.info("Resume with message: %s", resume_message[:200])

        # Restore state from saved stage outputs
        self._restore_state_from_outputs()

        # If formation was never selected (paused during diagnose), restart
        if not self._run.formation:
            self._run.status = "active"
            self._run.pause_reason = ""
            await self.run()
            return

        # Re-select formation from saved name
        from swarm.formation import EPIC, QUICK, STANDARD, THOROUGH
        formations = {"quick": QUICK, "standard": STANDARD, "thorough": THOROUGH, "epic": EPIC}
        self._formation = formations.get(self._run.formation)

        if not self._formation:
            self._run.fail("Cannot resume: unknown formation")
            self._pm.save_state(self._run)
            return

        self._run.status = "active"
        self._run.pause_reason = ""

        # Epic formation: resume through epic pipeline (skips decompose if plan exists)
        if self._formation.name == "epic":
            await self._run_epic_pipeline()
            return

        # Resume from the paused stage
        for i, stage in enumerate(self._formation.stages):
            if i < start_index:
                continue

            self._run.current_stage_index = i
            gate = await self._handle_stage(stage)
            post_result = await self._post_stage(stage, gate)
            if post_result == "pause":
                self._pm.save_state(self._run)
                return
            if post_result == "fail":
                self._pm.save_state(self._run)
                return

        self._run.complete()
        self._pm.save_state(self._run)

    def _restore_state_from_outputs(self) -> None:
        """Restore PipelineState from saved stage output files."""
        stage_loaders = {
            "diagnose": ("diagnose_result", DiagnoseResult),
            "architect": ("architect_result", ArchitectResult),
            "locate": ("locate_result", LocateResult),
            "execute": ("execute_result", ExecuteResult),
            "test": ("test_result", TestResult),
            "verify": ("verify_result", VerifyResult),
            "review": ("review_verdict", ReviewVerdict),
            "integrate": ("integrate_result", IntegrateResult),
            "submit": ("submit_result", SubmitResult),
        }
        for stage_name, (attr, model) in stage_loaders.items():
            data = self._pm.load_stage_output(stage_name)
            if data is not None:
                try:
                    setattr(self._state, attr, model.model_validate(data))
                except Exception:
                    logger.warning("Could not restore %s from saved output", stage_name)

        # Restore decomposition plan if present
        decompose_data = self._pm.load_stage_output("decompose")
        if decompose_data is not None:
            try:
                self._state.decomposition_plan = DecompositionPlan.model_validate(decompose_data)
            except Exception:
                logger.warning("Could not restore decompose from saved output")

    async def _handle_stage(self, stage: FormationStage) -> GateResult | None:
        """Dispatch to stage handler with per-stage model and backend routing."""
        # Per-stage backend routing
        stage_backend = (
            getattr(self._config, "stage_backends", {}).get(stage.name)
            or getattr(self._swarm_config, "default_stage_backends", {}).get(stage.name)
        )
        if stage_backend and hasattr(self._agent, "set_backend"):
            self._agent.set_backend(stage_backend, repo_path=self._state.repo_path)

        # Per-stage model routing
        stage_model = (
            getattr(self._config, "stage_models", {}).get(stage.name)
            or getattr(self._swarm_config, "default_stage_models", {}).get(stage.name)
            or getattr(self._config, "model", "")
            or getattr(self._swarm_config, "model", "")
        )
        if stage_model and hasattr(self._agent, "set_model"):
            self._agent.set_model(stage_model)
            if hasattr(self._budget, "set_model_pricing"):
                self._budget.set_model_pricing(stage_model)

        handlers = {
            "diagnose": self._handle_diagnose,
            "decompose": self._handle_decompose,
            "architect": self._handle_architect,
            "locate": self._handle_locate,
            "execute": self._handle_execute,
            "test": self._handle_test,
            "verify": self._handle_verify,
            "review": self._handle_review,
            "integrate": self._handle_integrate,
            "submit": self._handle_submit,
        }
        handler = handlers.get(stage.name)
        if handler:
            return await handler()
        return None

    async def _post_stage(self, stage: FormationStage, gate: GateResult | None) -> str:
        """Post-stage processing: signals, gate, break point, well-formedness.

        Returns: "continue", "pause", or "fail".
        """
        # Signal check — escalations route through management chain, never pause directly
        decision = self._signal_bus.should_continue()
        if decision.action == "pause":
            escalations = [s for s in self._signal_bus.signals if s.signal_type == "escalation"]
            if escalations:
                # Route through full management chain — project → architect → forced decision
                # Escalations NEVER directly pause. Management must make a call.
                await self._route_escalation_through_management(stage.name, escalations)
                # Clear escalation signals — management has handled them
                self._signal_bus._signals = [
                    s for s in self._signal_bus._signals if s.signal_type != "escalation"
                ]
                # Re-check: are there still consternation signals that would pause?
                decision = self._signal_bus.should_continue()
                if decision.action == "pause":
                    self._run.pause(f"Signal: {decision.reason}")
                    return "pause"
            else:
                # Pure consternation pause — stays mechanical
                self._run.pause(f"Signal: {decision.reason}")
                return "pause"

        # Gate check + perturbation annealing
        if gate is not None:
            if gate.passed:
                self._perturbation.anneal_success()
            else:
                self._perturbation.anneal_failure()

            if gate.paused:
                if self._human_io:
                    cont = self._human_io.confirm_break_point(stage.name, gate.reason)
                    if not cont:
                        self._run.pause(gate.reason)
                        return "pause"
                else:
                    self._run.pause(gate.reason)
                    return "pause"
            if not gate.passed:
                self._run.fail(gate.reason)
                return "fail"

        # Break point check
        if check_break_point(stage):
            if self._human_io:
                cont = self._human_io.confirm_break_point(stage.name, "Formation break point")
                if not cont:
                    self._run.pause("Break point at " + stage.name)
                    return "pause"
            # No human_io and break point → pause
            elif stage.break_point == "pause":
                self._run.pause("Break point at " + stage.name)
                return "pause"

        # Well-formedness: liveness + dissipation
        if self._start_time is not None and self._formation:
            elapsed = time.monotonic() - self._start_time
            wf = self._wf_checker.check_all(
                self._formation, self._budget,
                elapsed=elapsed, review_loops=self._state.review_loops,
            )
            if not wf.live:
                self._run.fail(f"Liveness violation: {'; '.join(wf.violations)}")
                return "fail"
            if not wf.dissipating:
                self._run.fail(f"Budget violation: {'; '.join(wf.violations)}")
                return "fail"

        return "continue"

    def _record(self, stage: str, passed: bool, confidence: float,
                detail: str, tok_in: int, tok_out: int) -> None:
        """Record stage in run and audit."""
        cost = self._budget.tokens_to_dollars(tok_in, tok_out)
        record = StageRecord(
            stage=stage, passed=passed, confidence=confidence,
            detail=detail[:80], tokens_in=tok_in, tokens_out=tok_out, cost_usd=cost,
        )
        self._run.record_stage(record)
        self._pm.append_audit(stage, passed, detail[:80])

    def _learnings_for(self, agent_name: str) -> str:
        repo = getattr(self._config, "repo", "")
        learnings = self._learnings.for_agent(agent_name, repo=repo)
        # Inject human resume message if present (one-shot: consumed after first use)
        if self._state._resume_message:
            msg = self._state._resume_message
            self._state._resume_message = ""
            learnings = (
                f"HUMAN DIRECTION (from pipeline resume): {msg}\n"
                f"The human reviewed the paused state and provided this guidance. "
                f"Follow it.\n\n" + learnings
            )
        return learnings

    async def _handle_decompose(self) -> GateResult | None:
        """Run the 4-phase decomposition process."""
        from swarm.agents.decompose import (
            ask_questions,
            build_component_map,
            define_contracts,
            plan_implementation_order,
        )

        diag = self._state.diagnose_result
        if diag is None:
            return GateResult(passed=False, reason="No diagnose result — cannot decompose")

        # Phase 1: Find ambiguities, make decisions, ask remaining questions
        questions_result, tok_in, tok_out = await ask_questions(
            self._agent, self._task, diag,
            learnings_context=self._learnings_for("decompose"),
        )
        n_decisions = len(getattr(questions_result, "decisions", []))
        n_questions = len(questions_result.questions)
        self._record("decompose", True, questions_result.confidence,
                     f"{n_decisions} decisions, {n_questions} questions", tok_in, tok_out)
        self._signal_bus.collect_from_result("decompose", questions_result)

        # Log engineering decisions the agent made
        for d in getattr(questions_result, "decisions", []):
            logger.info("Decompose decision: %s → %s (%s)", d.ambiguity, d.decision, d.rationale)

        # Surface remaining questions to human (should be rare: 0-3)
        answers: dict[str, str] = {}
        actionable = [q for q in questions_result.questions
                      if q.importance in ("blocking", "clarifying")]
        skipped = [q for q in questions_result.questions
                   if q.importance == "nice_to_have"]
        if actionable and self._human_io:
            answers = self._human_io.answer_decomposition_questions(actionable)

        # Log skipped and assumptions
        if skipped:
            for q in skipped:
                logger.info("Skipped nice_to_have question: %s", q.question)
        if questions_result.assumptions:
            for assumption in questions_result.assumptions:
                logger.info("Decompose assumption: %s", assumption)

        # Phase 2: Build component map (with decisions + human answers)
        decisions_context = {
            d.ambiguity: d.decision
            for d in getattr(questions_result, "decisions", [])
        }
        all_answers = {**decisions_context, **answers}  # Human answers override
        cmap, tok_in, tok_out = await build_component_map(
            self._agent, self._task, diag, answers=all_answers or None,
            learnings_context=self._learnings_for("decompose"),
        )
        self._record("decompose", True, cmap.confidence,
                     f"{len(cmap.components)} components", tok_in, tok_out)
        self._signal_bus.collect_from_result("decompose", cmap)

        # Phase 3: Define contracts
        contracts, tok_in, tok_out = await define_contracts(
            self._agent, self._task, cmap,
            learnings_context=self._learnings_for("decompose"),
        )
        self._record("decompose", True, contracts.confidence,
                     f"{len(contracts.contracts)} contracts", tok_in, tok_out)
        self._signal_bus.collect_from_result("decompose", contracts)

        # Phase 4: Plan implementation order
        impl_order, tok_in, tok_out = await plan_implementation_order(
            self._agent, cmap, contracts,
            learnings_context=self._learnings_for("decompose"),
        )
        self._record("decompose", True, impl_order.confidence,
                     f"{len(impl_order.steps)} steps", tok_in, tok_out)
        self._signal_bus.collect_from_result("decompose", impl_order)

        # Assemble plan
        plan = DecompositionPlan(
            questions=questions_result,
            component_map=cmap,
            contracts=contracts,
            implementation_order=impl_order,
            answers=answers,
        )
        self._state.decomposition_plan = plan
        self._pm.save_decomposition_plan(plan)

        return GateResult(passed=True, reason=f"Decomposed into {len(cmap.components)} components")

    async def _run_epic_pipeline(self) -> None:
        """Run the full epic pipeline: decompose → component loop → integrate → submit."""
        # Check if a saved decomposition plan already exists (e.g., from a previous run)
        if self._state.decomposition_plan is not None:
            logger.info("Resuming epic pipeline — reusing saved decomposition plan")
        else:
            # Run decompose stage
            decompose_stage = next(
                (s for s in self._formation.stages if s.name == "decompose"), None,
            )
            if decompose_stage:
                self._run.current_stage_index = 1  # After diagnose
                gate = await self._handle_decompose()
                post_result = await self._post_stage(decompose_stage, gate)
                if post_result in ("pause", "fail"):
                    self._pm.save_state(self._run)
                    return

        plan = self._state.decomposition_plan
        if not plan or not plan.component_map or not plan.implementation_order:
            self._run.fail("Decomposition incomplete — no component map or implementation order")
            self._pm.save_state(self._run)
            return

        # Run component pipelines in implementation order
        for step in plan.implementation_order.steps:
            component = next(
                (c for c in plan.component_map.components if c.id == step.component_id), None,
            )
            if component is None:
                logger.warning("Component %s not found in map — skipping", step.component_id)
                continue

            # Skip already-completed components (unless marked for redo)
            if component.id not in self._redo_components:
                saved = self._pm.load_component_output(component.id, "build_result")
                if saved and saved.get("status") == "completed":
                    logger.info("Component %s already completed — skipping", component.id)
                    restored = ComponentBuildResult(**saved)
                    self._state.component_results.append(restored)
                    continue

            contract = None
            if plan.contracts:
                contract = next(
                    (ct for ct in plan.contracts.contracts if ct.component_id == step.component_id),
                    None,
                )

            # Check budget before starting each component
            if self._budget.is_exceeded():
                logger.warning("Budget exceeded — stopping component loop")
                break

            prior = [r for r in self._state.component_results if r.status == "completed"]
            build_result = await self._run_component_pipeline(component, contract, prior)
            self._state.component_results.append(build_result)
            self._pm.save_component_output(component.id, "build_result", build_result)

            if build_result.status == "failed":
                logger.warning("Component %s failed: %s", component.id, build_result.issues)

        # Final integration — aggregate all completed component files
        completed = [r for r in self._state.component_results if r.status == "completed"]
        if not completed:
            self._run.fail("No components completed successfully")
            self._pm.save_state(self._run)
            return

        # Build aggregated execute result from all completed components
        from swarm.schemas import FileAction
        all_files = []
        for result in completed:
            exec_data = self._pm.load_component_output(result.component_id, "execute")
            if exec_data and isinstance(exec_data, dict):
                for f in exec_data.get("files", []):
                    if isinstance(f, dict):
                        all_files.append(FileAction(**f))
        if all_files:
            self._state.execute_result = ExecuteResult(
                files=all_files,
                explanation="Aggregated from completed components",
                confidence=0.0,
            )

        if self._state.execute_result is not None:
            gate = await self._handle_integrate()
            if gate and not gate.passed:
                self._run.fail(gate.reason)
                self._pm.save_state(self._run)
                return

        # Submit
        gate = await self._handle_submit()
        if gate and not gate.passed:
            self._run.fail(gate.reason)
            self._pm.save_state(self._run)
            return

        self._run.complete()
        self._pm.save_state(self._run)

    async def _run_component_pipeline(
        self, component: object, contract: object | None, prior_components: list,
    ) -> ComponentBuildResult:
        """Run the 6-stage pipeline for a single component."""
        self._state.current_component_id = component.id
        logger.info("Building component: %s (%s)", component.name, component.id)

        # Build a component-scoped task string
        task = (
            f"Component: {component.name}\n"
            f"Description: {component.description}\n"
            f"Dependencies: {', '.join(component.dependencies) or 'none'}\n"
            f"Affected files: {', '.join(component.affected_files) or 'unknown'}\n\n"
            f"Original task:\n{self._task}"
        )
        if contract:
            task += (
                f"\n\nContract:\n"
                f"  Inputs: {', '.join(contract.inputs)}\n"
                f"  Outputs: {', '.join(contract.outputs)}\n"
                f"  Invariants: {', '.join(contract.invariants)}\n"
            )
            if getattr(contract, "error_modes", None):
                task += f"  Error modes: {', '.join(contract.error_modes)}\n"

        # Cross-component constraints from decomposition plan
        plan = self._state.decomposition_plan
        if plan and plan.contracts and plan.contracts.cross_component_constraints:
            task += "\n\nCross-component constraints (MUST be honored):\n"
            for constraint in plan.contracts.cross_component_constraints:
                task += f"  - {constraint}\n"

        if prior_components:
            task += "\n\nPrior completed components:\n"
            for p in prior_components:
                task += f"  - {p.component_id}: {', '.join(p.files_changed) or 'no files'}\n"
                # Include architect decisions from prior component
                prior_arch = self._pm.load_component_output(p.component_id, "architect")
                if prior_arch and isinstance(prior_arch, dict):
                    approach = prior_arch.get("approach", "")
                    if approach:
                        task += f"    Approach: {approach}\n"
                # Include execute explanation from prior component
                prior_exec = self._pm.load_component_output(p.component_id, "execute")
                if prior_exec and isinstance(prior_exec, dict):
                    explanation = prior_exec.get("explanation", "")
                    if explanation:
                        task += f"    What was done: {explanation}\n"
                # Include contract from prior component
                if plan and plan.contracts:
                    prior_contract = next(
                        (ct for ct in plan.contracts.contracts if ct.component_id == p.component_id),
                        None,
                    )
                    if prior_contract:
                        task += (
                            f"    Contract: inputs={', '.join(prior_contract.inputs)}, "
                            f"outputs={', '.join(prior_contract.outputs)}\n"
                        )

        # Save original task/formation and reset per-component state
        original_task = self._task
        original_formation = self._formation
        self._task = task
        self._state.architect_result = None
        self._state.locate_result = None
        self._state.execute_result = None
        self._state.test_result = None
        self._state.verify_result = None
        self._state.guardian_result = None
        self._state.review_verdict = None
        self._state.review_loops = 0
        self._state.review_issues_for_reexecute = []
        self._state._perspective_shift = ""
        self._state.architect_review_loops = 0
        self._state.architect_issues_for_replan = []
        self._state._architect_perspective_shift = ""

        stages_completed = []
        issues = []
        files_changed = []

        from swarm.formation import COMPONENT_PIPELINE
        # Use COMPONENT_PIPELINE as formation so _handle_review uses its max_review_loops
        self._formation = COMPONENT_PIPELINE
        for stage in COMPONENT_PIPELINE.stages:
            try:
                gate = await self._handle_stage(stage)
                if gate and not gate.passed:
                    issues.append(f"{stage.name}: {gate.reason}")
                    self._task = original_task
                    self._formation = original_formation
                    return ComponentBuildResult(
                        component_id=component.id,
                        status="failed",
                        stages_completed=stages_completed,
                        issues=issues,
                        files_changed=files_changed,
                    )
                stages_completed.append(stage.name)

                # Track changed files from execute
                if stage.name == "execute" and self._state.execute_result:
                    files_changed = [f.path for f in self._state.execute_result.files]

                # Save component stage output
                self._pm.save_component_output(component.id, stage.name,
                    getattr(self._state, {
                        "architect": "architect_result",
                        "locate": "locate_result",
                        "execute": "execute_result",
                        "test": "test_result",
                        "verify": "verify_result",
                        "review": "review_verdict",
                    }.get(stage.name, ""), None) or {})

            except Exception as exc:
                issues.append(f"{stage.name}: {exc}")
                self._task = original_task
                self._formation = original_formation
                return ComponentBuildResult(
                    component_id=component.id,
                    status="failed",
                    stages_completed=stages_completed,
                    issues=issues,
                    files_changed=files_changed,
                )

        self._task = original_task
        self._formation = original_formation
        return ComponentBuildResult(
            component_id=component.id,
            status="completed",
            stages_completed=stages_completed,
            files_changed=files_changed,
        )

    async def _handle_diagnose(self) -> GateResult | None:
        from swarm.agents.diagnose import diagnose
        from swarm.gates import diagnose_gate

        result, tok_in, tok_out = await diagnose(
            self._agent, self._task,
            learnings_context=self._learnings_for("diagnose"),
        )
        self._state.diagnose_result = result
        self._record("diagnose", True, result.confidence, result.summary, tok_in, tok_out)
        self._pm.save_stage_output("diagnose", result)
        self._signal_bus.collect_from_result("diagnose", result)

        return diagnose_gate(result, self._swarm_config)

    async def _handle_architect(self) -> GateResult | None:
        from swarm.agents.architect import (
            ARCHITECT_PERSPECTIVE_SHIFT_PREFIX,
            architect,
            architect_review_full,
        )
        from swarm.agents.review import classify_review_issues

        # Build task with any re-plan constraints from previous review
        task = self._task
        if self._state.architect_issues_for_replan:
            task += "\n\nCONSTRAINTS from architecture review (MUST be addressed):\n"
            for issue in self._state.architect_issues_for_replan:
                task += f"  - {issue}\n"
            self._state.architect_issues_for_replan = []

        result, tok_in, tok_out = await architect(
            self._agent, task, self._state.diagnose_result,
            learnings_context=self._learnings_for("architect"),
        )
        self._state.architect_result = result
        self._record("architect", True, result.confidence, result.approach[:80], tok_in, tok_out)
        self._pm.save_stage_output("architect", result)
        self._signal_bus.collect_from_result("architect", result)

        # Architecture review sub-passes (if configured)
        sub_passes = None
        if self._formation:
            for stage in self._formation.stages:
                if stage.name == "architect" and stage.architect_sub_passes:
                    sub_passes = stage.architect_sub_passes
                    break

        if not sub_passes:
            return None  # No review configured — same as before

        # Switch to Sonnet for review sub-passes — narrowly focused, Opus is overkill
        review_model = (
            getattr(self._config, "stage_models", {}).get("architect_review")
            or getattr(self._swarm_config, "default_stage_models", {}).get("architect_review")
        )
        if review_model:
            self._agent.set_model(review_model)
            self._budget.set_model_pricing(review_model)

        # Inject perspective shift if set from previous loop
        review_learnings = self._learnings_for("architect")
        if self._state._architect_perspective_shift:
            review_learnings = self._state._architect_perspective_shift + review_learnings
            self._state._architect_perspective_shift = ""

        verdict, rv_tok_in, rv_tok_out = await architect_review_full(
            self._agent, self._task, result, self._state.diagnose_result,
            sub_passes=sub_passes, learnings_context=review_learnings,
        )
        self._record("architect_review", verdict.verdict == "approve",
                     verdict.confidence, verdict.verdict, rv_tok_in, rv_tok_out)

        if verdict.verdict == "approve":
            return None  # Approved — proceed

        # Rejected — classify and loop back (same pattern as _handle_review)
        max_loops = self._formation.max_architect_review_loops if self._formation else 0
        if max_loops > 0 and self._state.architect_review_loops < max_loops:
            self._state.architect_review_loops += 1
            loop = self._state.architect_review_loops

            # Classify issues (reuse classify_review_issues)
            plan_summary = self._build_architect_summary()
            classification, cl_in, cl_out = await classify_review_issues(
                self._agent, verdict.issues, plan_summary,
            )
            self._record("architect_review_classify", True, 0.0,
                         f"factual={sum(1 for i in classification.issues if i.category == 'factual')}, "
                         f"subjective={sum(1 for i in classification.issues if i.category == 'subjective')}",
                         cl_in, cl_out)

            factual = [i for i in classification.issues if i.category == "factual"]
            subjective = [i for i in classification.issues if i.category == "subjective"]

            logger.info("Architect review loop %d/%d: %d factual, %d subjective issues",
                        loop, max_loops, len(factual), len(subjective))

            # Factual: re-plan with constraints
            if factual:
                self._state.architect_issues_for_replan = [i.issue for i in factual]

            # Subjective: check threshold (architect uses higher thresholds)
            if subjective:
                threshold_idx = min(loop - 1, len(self._ARCHITECT_DISAGREEMENT_THRESHOLDS) - 1)
                threshold = self._ARCHITECT_DISAGREEMENT_THRESHOLDS[threshold_idx]

                if verdict.confidence < threshold:
                    logger.info("Architect subjective disagreement below threshold %.1f "
                                "(reviewer confidence %.2f) — approving",
                                threshold, verdict.confidence)
                    return None  # Below threshold — approve

                # Set perspective shift for next review
                logger.info("Architect subjective issues above threshold %.1f — "
                            "re-reviewing with perspective shift", threshold)
                next_threshold_idx = min(loop, len(self._ARCHITECT_DISAGREEMENT_THRESHOLDS) - 1)
                next_threshold = self._ARCHITECT_DISAGREEMENT_THRESHOLDS[next_threshold_idx]
                self._state._architect_perspective_shift = ARCHITECT_PERSPECTIVE_SHIFT_PREFIX.format(
                    threshold=next_threshold,
                    issues="\n".join(f"- {i.issue}" for i in subjective),
                )

            # Re-run architect (with constraints if factual)
            return await self._handle_architect()
        else:
            # Escalate (reuse _escalate_review pattern)
            return await self._escalate_architect_review(verdict)

    async def _escalate_architect_review(self, verdict: ReviewVerdict) -> GateResult:
        """Escalate architect review disagreement: project → architect → forced decision."""
        from swarm.agents.review import escalate_to_architect, escalate_to_project

        goal = self._task
        plan_summary = self._build_architect_summary()
        contention = self._build_architect_contention(verdict)

        # Level 1: Project-level review
        logger.info("Architect review escalation: project level")
        project_result, tok_in, tok_out = await escalate_to_project(
            self._agent, goal, plan_summary, contention,
        )
        self._record("architect_review_escalation", True, project_result.confidence,
                     f"project: {project_result.decision}", tok_in, tok_out)
        self._signal_bus.collect_from_result("architect_review_escalation", project_result)

        if project_result.decision == "approve" and project_result.confidence >= 0.7:
            logger.info("Architect escalation: approved at project level (confidence %.2f)",
                        project_result.confidence)
            return GateResult(passed=True,
                              reason=f"Architect escalation approved at project level: {project_result.rationale}")

        # Level 2: Architect-level review (final authority)
        logger.info("Architect review escalation: architect level (project confidence=%.2f, decision=%s)",
                     project_result.confidence, project_result.decision)
        project_assessment = (
            f"Decision: {project_result.decision} (confidence: {project_result.confidence:.2f})\n"
            f"Rationale: {project_result.rationale}\n"
            f"Conditions: {', '.join(project_result.conditions) if project_result.conditions else 'none'}"
        )
        architect_result, tok_in, tok_out = await escalate_to_architect(
            self._agent, goal, plan_summary, contention, project_assessment,
        )
        self._record("architect_review_escalation", True, architect_result.confidence,
                     f"architect: {architect_result.decision}", tok_in, tok_out)
        self._signal_bus.collect_from_result("architect_review_escalation", architect_result)

        conditions_msg = ""
        if architect_result.conditions:
            conditions_msg = " Conditions: " + "; ".join(architect_result.conditions)

        if self._human_io:
            self._human_io.notify(
                "architect_review",
                f"Architecture review escalated to architect level — {architect_result.decision}. "
                f"{architect_result.rationale}{conditions_msg}",
            )

        logger.info("Architect-level escalation: %s (confidence %.2f)%s",
                     architect_result.decision, architect_result.confidence, conditions_msg)

        return GateResult(
            passed=True,
            reason=f"Architect review escalation: {architect_result.decision}. {architect_result.rationale}{conditions_msg}",
        )

    async def _route_escalation_through_management(self, stage_name: str, escalations: list) -> None:
        """Route escalation signals through the full management chain.

        Escalations NEVER directly pause the pipeline. Management must make a decision:
          Level 1: Project arbiter — pragmatic "is this worth worrying about?"
          Level 2: Architect arbiter — final authority, MUST decide, cannot reject

        The user is notified of the decision and any conditions, but is never blocked.
        This mirrors how a real organization works: an IC raises a concern, management
        absorbs it, and only surfaces what the exec actually needs to know.
        """
        from swarm.agents.review import escalate_to_architect, escalate_to_project

        escalation_summary = "\n".join(
            f"- [{e.stage}] {e.message}" for e in escalations
        )
        goal = self._task
        context = (
            f"Stage: {stage_name}\n"
            f"Formation: {self._formation.name if self._formation else 'unknown'}\n"
            f"Stages completed: {self._run.current_stage_index + 1}\n\n"
            f"Escalation signals:\n{escalation_summary}"
        )
        contention = (
            f"An agent raised an escalation signal during the {stage_name} stage.\n\n"
            f"Escalation signals:\n{escalation_summary}\n\n"
            f"Preferences about tooling, test runners, compilation strategies, or "
            f"'would be nice' improvements are NOT real problems. Only flag issues "
            f"that will cause the output to be WRONG or BROKEN."
        )

        # Level 1: Project arbiter
        logger.info("Escalation from %s → project arbiter", stage_name)
        try:
            project_result, tok_in, tok_out = await escalate_to_project(
                self._agent, goal, context, contention,
            )
            self._record("escalation_triage", True, project_result.confidence,
                         f"project: {project_result.decision}", tok_in, tok_out)

            if project_result.decision == "approve" and project_result.confidence >= 0.7:
                logger.info("Escalation absorbed by project arbiter: %s", project_result.rationale[:100])
                if self._human_io:
                    self._human_io.notify(
                        "escalation_triage",
                        f"Agent escalation from {stage_name} resolved by management: {project_result.rationale}",
                    )
                return

            # Level 2: Architect arbiter — final authority, must decide
            logger.info("Escalation from %s → architect arbiter (project: %s, confidence=%.2f)",
                        stage_name, project_result.decision, project_result.confidence)
            project_assessment = (
                f"Decision: {project_result.decision} (confidence: {project_result.confidence:.2f})\n"
                f"Rationale: {project_result.rationale}\n"
                f"Conditions: {', '.join(project_result.conditions) if project_result.conditions else 'none'}"
            )
            architect_result, tok_in, tok_out = await escalate_to_architect(
                self._agent, goal, context, contention, project_assessment,
            )
            self._record("escalation_triage", True, architect_result.confidence,
                         f"architect: {architect_result.decision}", tok_in, tok_out)

            conditions_msg = ""
            if architect_result.conditions:
                conditions_msg = " Conditions: " + "; ".join(architect_result.conditions)

            logger.info("Escalation resolved by architect arbiter: %s%s",
                        architect_result.decision, conditions_msg)

            if self._human_io:
                self._human_io.notify(
                    "escalation_resolved",
                    f"Agent escalation from {stage_name} — {architect_result.decision}. "
                    f"{architect_result.rationale}{conditions_msg}",
                )

        except Exception as e:
            # If management chain fails, log and continue — don't pause user
            logger.warning("Escalation management chain failed (%s) — continuing anyway", e)
            if self._human_io:
                self._human_io.notify(
                    "escalation_error",
                    f"Agent escalation from {stage_name} could not be triaged ({e}). Continuing.",
                )

    def _build_architect_summary(self) -> str:
        """Build a plan summary from the current architect result for escalation context."""
        if not self._state.architect_result:
            return "(no architecture plan)"
        result = self._state.architect_result
        parts = [f"Approach: {result.approach}"]
        for i, step in enumerate(result.steps):
            parts.append(f"Step {i+1}: {step.description} (risk: {step.risk})")
        parts.append(f"Affected files: {', '.join(result.affected_files)}")
        if result.new_files:
            parts.append(f"New files: {', '.join(result.new_files)}")
        return "\n".join(parts)

    def _build_architect_contention(self, verdict: ReviewVerdict) -> str:
        """Build contention summary from architect review verdict."""
        parts = []
        parts.append(f"Architecture review verdict: {verdict.verdict} (confidence: {verdict.confidence:.2f})")
        if verdict.issues:
            parts.append("Architecture review issues:\n" + "\n".join(f"  - {i}" for i in verdict.issues))
        parts.append(f"Architect review loops attempted: {self._state.architect_review_loops}")
        if self._state.architect_issues_for_replan:
            parts.append("Issues architect was asked to address:\n" + "\n".join(
                f"  - {i}" for i in self._state.architect_issues_for_replan))
        return "\n\n".join(parts)

    async def _handle_locate(self) -> GateResult | None:
        from swarm.agents.locate import locate
        from swarm.gates import locate_gate

        repo_path = getattr(self._config, "repo_path", Path("."))
        self._state.repo_path = repo_path

        result, tok_in, tok_out = await locate(
            self._agent, self._task,
            self._state.diagnose_result, self._state.architect_result,
            repo_path,
            learnings_context=self._learnings_for("locate"),
        )
        self._state.locate_result = result
        self._record("locate", True, result.confidence, f"{len(result.files)} files", tok_in, tok_out)
        self._pm.save_stage_output("locate", result)
        self._signal_bus.collect_from_result("locate", result)

        return locate_gate(result, self._swarm_config)

    async def _handle_execute(self) -> GateResult | None:
        from swarm.agents.execute import execute

        repo_path = self._state.repo_path or Path(".")

        # Check for competition mode
        competitors = 0
        if self._formation:
            for s in self._formation.stages:
                if s.name == "execute" and s.competitors >= 2:
                    competitors = self._perturbation.recommended_competitors(
                        "execute", s.competitors,
                    )
                    break

        if competitors >= 2:
            return await self._handle_execute_competition(competitors, repo_path)

        # Single execution (default)
        result, tok_in, tok_out = await execute(
            self._agent, self._task,
            self._state.diagnose_result, self._state.locate_result,
            repo_path,
            learnings_context=self._learnings_for("execute"),
            review_issues=self._state.review_issues_for_reexecute or None,
        )
        self._state.execute_result = result
        self._record("execute", True, result.confidence,
                     f"{len(result.files)} files changed", tok_in, tok_out)
        self._pm.save_stage_output("execute", result)
        self._signal_bus.collect_from_result("execute", result)
        return None

    async def _handle_execute_competition(
        self, n_competitors: int, repo_path: Path,
    ) -> GateResult | None:
        """Run N candidates and select the best via Arbiter."""
        from swarm.agents.execute import execute
        from swarm.arbiter import Arbiter, Candidate

        candidates = []
        for i in range(n_competitors):
            result, tok_in, tok_out = await execute(
                self._agent, self._task,
                self._state.diagnose_result, self._state.locate_result,
                repo_path,
                learnings_context=self._learnings_for("execute"),
                review_issues=self._state.review_issues_for_reexecute or None,
            )
            self._record("execute", True, result.confidence,
                         f"candidate {i}: {len(result.files)} files", tok_in, tok_out)
            candidates.append(Candidate(index=i, execute_result=result))

        arbiter = Arbiter(self._score_registry)
        winner = arbiter.select(candidates)

        if winner is None:
            return GateResult(passed=False, reason="All competition candidates disqualified")

        self._state.execute_result = winner.execute_result
        self._pm.save_stage_output("execute", winner.execute_result)
        self._signal_bus.collect_from_result("execute", winner.execute_result)
        logger.info(
            "Competition winner: candidate %d (score %.3f)",
            winner.index,
            winner.composite_score.value if winner.composite_score else 0.0,
        )
        return None

    async def _handle_test(self) -> GateResult | None:
        from swarm.agents.test import generate_tests

        repo_path = self._state.repo_path or Path(".")

        result, tok_in, tok_out = await generate_tests(
            self._agent, self._task,
            self._state.diagnose_result, self._state.locate_result,
            self._state.execute_result, repo_path,
            learnings_context=self._learnings_for("test"),
        )
        self._state.test_result = result
        self._record("test", True, result.confidence,
                     f"{len(result.test_files)} test files", tok_in, tok_out)
        self._pm.save_stage_output("test", result)
        self._signal_bus.collect_from_result("test", result)
        return None

    async def _handle_verify(self) -> GateResult | None:
        from swarm import executor
        from swarm.gates import verify_gate

        repo_path = self._state.repo_path or Path(".")

        # Apply changes, write tests, run verification
        await executor.apply_changes(repo_path, self._state.execute_result)
        if self._state.test_result:
            await executor.write_tests(repo_path, self._state.test_result)
        result = await executor.run_verification(repo_path, self._config)

        self._state.verify_result = result

        # Guardian check
        guardian_result = self._guardian.check(
            self._state.execute_result, self._state.test_result, self._swarm_config,
        )
        self._state.guardian_result = guardian_result

        passed = result.compiles and guardian_result.passed
        self._record("verify", passed, 0.0,
                     f"compile={result.compiles} tests={result.tests_passing}/{result.tests_total}",
                     0, 0)
        self._pm.save_stage_output("verify", result)

        return verify_gate(result)

    # Disagreement thresholds: each loop requires higher reviewer confidence to block
    _DISAGREEMENT_THRESHOLDS = [0.0, 0.6, 0.8]

    # Architect review uses higher thresholds — architecture is functional, not aesthetic.
    # Loop 1: only block if reviewer is >=50% confident the plan won't work.
    # Loop 2: >=70%. Loop 3: >=85%. Low-confidence "I'd do it differently" passes through.
    _ARCHITECT_DISAGREEMENT_THRESHOLDS = [0.5, 0.7, 0.85]

    async def _handle_review(self) -> GateResult | None:
        from swarm.agents.review import (
            PERSPECTIVE_SHIFT_PREFIX,
            classify_review_issues,
            review,
            review_full,
        )
        from swarm.gates import review_gate

        # Check for sub-passes from formation
        sub_passes = None
        if self._formation:
            for stage in self._formation.stages:
                if stage.name == "review" and stage.review_sub_passes:
                    sub_passes = stage.review_sub_passes
                    break

        # Inject perspective shift if set from previous loop
        review_learnings = self._learnings_for("review")
        if self._state._perspective_shift:
            review_learnings = self._state._perspective_shift + review_learnings
            self._state._perspective_shift = ""  # Clear after use

        verdict, tok_in, tok_out = await review_full(
            self._agent, self._task,
            self._state.execute_result, self._state.test_result,
            self._state.verify_result, self._state.guardian_result,
            learnings_context=review_learnings,
            sub_passes=sub_passes,
        )
        self._state.review_verdict = verdict
        self._record("review", verdict.verdict == "approve", verdict.confidence,
                     verdict.verdict, tok_in, tok_out)
        self._pm.save_stage_output("review", verdict)
        self._signal_bus.collect_from_result("review", verdict)

        gate = review_gate(verdict, self._swarm_config)

        # Review loop-back with exponential backoff on disagreement
        if not gate.passed and self._formation and self._formation.max_review_loops > 0:
            if self._state.review_loops < self._formation.max_review_loops:
                self._state.review_loops += 1
                loop = self._state.review_loops

                # Classify issues as factual vs subjective
                classification, cl_in, cl_out = await classify_review_issues(
                    self._agent, verdict.issues, self._build_code_summary(),
                )
                self._record("review_classify", True, 0.0,
                             f"factual={sum(1 for i in classification.issues if i.category == 'factual')}, "
                             f"subjective={sum(1 for i in classification.issues if i.category == 'subjective')}",
                             cl_in, cl_out)

                factual = [i for i in classification.issues if i.category == "factual"]
                subjective = [i for i in classification.issues if i.category == "subjective"]

                logger.info("Review loop %d/%d: %d factual, %d subjective issues",
                            loop, self._formation.max_review_loops,
                            len(factual), len(subjective))

                # Factual issues: fix them, no debate
                if factual:
                    factual_issues = [i.issue for i in factual]
                    self._state.review_issues_for_reexecute = factual_issues
                    logger.info("Fixing %d factual issues (no debate)", len(factual))
                    await self._handle_execute()
                    await self._handle_test()
                    await self._handle_verify()

                # Subjective issues: check disagreement threshold
                if subjective:
                    threshold_idx = min(loop - 1, len(self._DISAGREEMENT_THRESHOLDS) - 1)
                    threshold = self._DISAGREEMENT_THRESHOLDS[threshold_idx]

                    if verdict.confidence < threshold:
                        # Reviewer not confident enough to keep blocking
                        logger.info("Subjective disagreement below threshold %.1f "
                                    "(reviewer confidence %.2f) — approving",
                                    threshold, verdict.confidence)
                        return GateResult(
                            passed=True,
                            reason=f"Subjective issues below disagreement threshold "
                                   f"({verdict.confidence:.2f} < {threshold:.1f})",
                        )

                    # Re-review with perspective shift
                    logger.info("Subjective issues above threshold %.1f — "
                                "re-reviewing with perspective shift", threshold)
                    next_threshold_idx = min(loop, len(self._DISAGREEMENT_THRESHOLDS) - 1)
                    next_threshold = self._DISAGREEMENT_THRESHOLDS[next_threshold_idx]
                    self._state._perspective_shift = PERSPECTIVE_SHIFT_PREFIX.format(
                        threshold=next_threshold,
                        issues="\n".join(f"- {i.issue}" for i in subjective),
                    )

                # If only factual issues (all fixed), re-review clean
                return await self._handle_review()
            else:
                # Only SUBJECTIVE issues can reach escalation
                return await self._escalate_review(verdict)

        return gate

    async def _escalate_review(self, verdict: ReviewVerdict) -> GateResult:
        """Escalate review disagreement: project → architect → forced decision."""
        from swarm.agents.review import escalate_to_architect, escalate_to_project

        # Build context for escalation
        goal = self._task
        code_summary = self._build_code_summary()
        contention = self._build_contention(verdict)

        # Level 1: Project-level review
        logger.info("Review escalation: project level")
        project_result, tok_in, tok_out = await escalate_to_project(
            self._agent, goal, code_summary, contention,
        )
        self._record("review_escalation", True, project_result.confidence,
                     f"project: {project_result.decision}", tok_in, tok_out)
        self._signal_bus.collect_from_result("review_escalation", project_result)

        if project_result.decision == "approve" and project_result.confidence >= 0.7:
            logger.info("Project-level escalation: approved (confidence %.2f)",
                        project_result.confidence)
            return GateResult(passed=True, reason=f"Escalation approved at project level: {project_result.rationale}")

        # Level 2: Architect-level review (final authority)
        logger.info("Review escalation: architect level (project confidence=%.2f, decision=%s)",
                     project_result.confidence, project_result.decision)
        project_assessment = (
            f"Decision: {project_result.decision} (confidence: {project_result.confidence:.2f})\n"
            f"Rationale: {project_result.rationale}\n"
            f"Conditions: {', '.join(project_result.conditions) if project_result.conditions else 'none'}"
        )
        architect_result, tok_in, tok_out = await escalate_to_architect(
            self._agent, goal, code_summary, contention, project_assessment,
        )
        self._record("review_escalation", True, architect_result.confidence,
                     f"architect: {architect_result.decision}", tok_in, tok_out)
        self._signal_bus.collect_from_result("review_escalation", architect_result)

        # Architect is final authority — always proceeds
        conditions_msg = ""
        if architect_result.conditions:
            conditions_msg = " Conditions: " + "; ".join(architect_result.conditions)

        # Notify the user about the forced decision
        if self._human_io:
            self._human_io.notify(
                "review",
                f"Review escalated to architect level — {architect_result.decision}. "
                f"{architect_result.rationale}{conditions_msg}",
            )

        logger.info("Architect-level escalation: %s (confidence %.2f)%s",
                     architect_result.decision, architect_result.confidence, conditions_msg)

        return GateResult(
            passed=True,
            reason=f"Architect escalation: {architect_result.decision}. {architect_result.rationale}{conditions_msg}",
        )

    def _build_code_summary(self) -> str:
        """Build a code summary from the current execute result for escalation context."""
        if not self._state.execute_result:
            return "(no code changes)"
        parts = []
        for fa in self._state.execute_result.files:
            if fa.action == "modify":
                parts.append(f"File: {fa.path} (modified)\nNew:\n{fa.content[:2000]}")
            elif fa.action == "create":
                parts.append(f"File: {fa.path} (created)\n{fa.content[:2000]}")
            elif fa.action == "delete":
                parts.append(f"File: {fa.path} (deleted)")
        return "\n\n".join(parts)

    def _build_contention(self, verdict: ReviewVerdict) -> str:
        """Build contention summary from review verdict and review history."""
        parts = []
        parts.append(f"Review verdict: {verdict.verdict} (confidence: {verdict.confidence:.2f})")
        if verdict.issues:
            parts.append("Review issues:\n" + "\n".join(f"  - {i}" for i in verdict.issues))
        if verdict.risks:
            parts.append("Risks:\n" + "\n".join(f"  - {r}" for r in verdict.risks))
        if verdict.missed_edge_cases:
            parts.append("Missed edge cases:\n" + "\n".join(f"  - {e}" for e in verdict.missed_edge_cases))
        parts.append(f"Review loops attempted: {self._state.review_loops}")
        if self._state.review_issues_for_reexecute:
            parts.append("Issues executor was asked to fix:\n" + "\n".join(
                f"  - {i}" for i in self._state.review_issues_for_reexecute))
        return "\n\n".join(parts)

    async def _handle_integrate(self) -> GateResult | None:
        from swarm.agents.integrate import integrate
        from swarm.gates import integrate_gate

        result, tok_in, tok_out = await integrate(
            self._agent, self._task,
            self._state.execute_result, self._state.test_result,
            self._state.verify_result,
            learnings_context=self._learnings_for("integrate"),
        )
        self._state.integrate_result = result
        self._record("integrate", result.coherent, result.confidence,
                     "coherent" if result.coherent else "incoherent", tok_in, tok_out)
        self._pm.save_stage_output("integrate", result)
        self._signal_bus.collect_from_result("integrate", result)

        return integrate_gate(result, self._swarm_config)

    async def _handle_submit(self) -> GateResult | None:
        from swarm import executor

        repo_path = self._state.repo_path or Path(".")
        summary = ""
        if self._state.diagnose_result:
            summary = self._state.diagnose_result.summary

        result = await executor.submit_changes(
            repo_path, self._state.execute_result,
            self._state.test_result, summary,
            self._config, run_id=self._run.id,
        )
        self._state.submit_result = result
        self._record("submit", result.success, 0.0,
                     f"{result.action}: {'ok' if result.success else 'fail'}", 0, 0)
        self._pm.save_stage_output("submit", result)

        if not result.success:
            return GateResult(passed=False, reason="; ".join(result.errors))
        return GateResult(passed=True, reason="Submit succeeded")
