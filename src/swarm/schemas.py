"""All Pydantic schemas — rigid boundaries for agent outputs.

Every agent output is a Pydantic model enforced at generation time via
tool_choice. The schema shapes the model's output — it's a guardrail,
not a validator. Pattern: AgentBase.assess(schema, prompt, system).

9 cognitive passes: diagnose → architect → locate → execute → test →
verify → review → integrate → submit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ── Stage 1: Diagnose ────────────────────────────────────────────────


class DiagnoseResult(BaseModel):
    """Diagnose agent assessment of a programming task."""
    task_type: Literal["feature", "bugfix", "refactor", "test", "docs", "config", "other"]
    complexity: Literal["trivial", "simple", "moderate", "complex", "epic"]
    confidence: float = Field(ge=0.0, le=1.0)
    language: str
    framework: str = ""
    summary: str
    reasoning: str
    constraints: list[str] = []
    risks: list[str] = []
    consternation: str = ""
    escalation: str = ""


# ── Decomposition schemas ────────────────────────────────────────────


class DecomposeDecision(BaseModel):
    """An engineering decision resolving an ambiguity found in the spec."""
    ambiguity: str
    decision: str
    rationale: str


class DecomposeQuestion(BaseModel):
    """A question to clarify before decomposing an epic task."""
    question: str
    context: str = ""
    importance: Literal["blocking", "clarifying", "nice_to_have"] = "clarifying"


class DecomposeQuestionsResult(BaseModel):
    """Phase 1 output — engineering decisions and remaining questions."""
    decisions: list[DecomposeDecision] = []
    questions: list[DecomposeQuestion] = []
    assumptions: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    consternation: str = ""
    escalation: str = ""


class Component(BaseModel):
    """A single logical component of a decomposed epic task."""
    id: str
    name: str
    description: str
    dependencies: list[str] = []
    estimated_complexity: Literal["trivial", "simple", "moderate", "complex"] = "moderate"
    affected_files: list[str] = []


class ComponentMapResult(BaseModel):
    """Phase 2 output — decomposition into logical components."""
    one_sentence: str
    components: list[Component]
    interaction_summary: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    consternation: str = ""
    escalation: str = ""


class Contract(BaseModel):
    """Per-component contract — inputs, outputs, invariants."""
    component_id: str
    inputs: list[str]
    outputs: list[str]
    invariants: list[str] = []
    error_modes: list[str] = []


class ContractSetResult(BaseModel):
    """Phase 3 output — contracts for all components."""
    contracts: list[Contract]
    cross_component_constraints: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    consternation: str = ""
    escalation: str = ""


class ImplementationStep(BaseModel):
    """A single step in the implementation order."""
    component_id: str
    order: int
    rationale: str


class ImplementationOrderResult(BaseModel):
    """Phase 4 output — topological ordering for implementation."""
    steps: list[ImplementationStep]
    parallel_groups: list[list[str]] = []
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    consternation: str = ""
    escalation: str = ""


class DecompositionPlan(BaseModel):
    """Assembled decomposition plan — all 4 phases combined."""
    questions: DecomposeQuestionsResult | None = None
    component_map: ComponentMapResult | None = None
    contracts: ContractSetResult | None = None
    implementation_order: ImplementationOrderResult | None = None
    answers: dict[str, str] = {}


class ComponentBuildResult(BaseModel):
    """Per-component build result after running the component pipeline."""
    component_id: str
    status: Literal["completed", "failed", "skipped"] = "completed"
    stages_completed: list[str] = []
    issues: list[str] = []
    files_changed: list[str] = []


# ── Stage 2: Architect ───────────────────────────────────────────────


class ArchitectStep(BaseModel):
    """A single step in the architecture plan."""
    description: str
    files_involved: list[str] = []
    risk: Literal["low", "medium", "high"] = "low"


class ArchitectResult(BaseModel):
    """Architect agent output — approach plan for the task."""
    approach: str
    steps: list[ArchitectStep]
    affected_files: list[str]
    new_files: list[str] = []
    deleted_files: list[str] = []
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    consternation: str = ""
    escalation: str = ""


# ── Stage 3: Locate ──────────────────────────────────────────────────


class FileLocation(BaseModel):
    """A file identified by the locator agent."""
    path: str
    relevance: Literal["primary", "context", "test", "new"]
    reasoning: str


class LocateResult(BaseModel):
    """Locator agent output — files involved in the task."""
    files: list[FileLocation]
    confidence: float = Field(ge=0.0, le=1.0)
    search_strategy: str
    consternation: str = ""
    escalation: str = ""


# ── Stage 4: Execute ─────────────────────────────────────────────────


class FileAction(BaseModel):
    """A single file operation: create, modify, or delete."""
    path: str
    action: Literal["create", "modify", "delete"]
    original: str = ""
    content: str = ""
    explanation: str = ""


class ExecuteResult(BaseModel):
    """Execute agent output — code changes."""
    files: list[FileAction]
    explanation: str
    confidence: float = Field(ge=0.0, le=1.0)
    consternation: str = ""
    escalation: str = ""

    @property
    def total_diff_lines(self) -> int:
        total = 0
        for fa in self.files:
            if fa.action == "delete":
                total += fa.original.count("\n") + 1
            elif fa.action == "create":
                total += fa.content.count("\n") + 1
            else:
                old_lines = fa.original.count("\n") + 1
                new_lines = fa.content.count("\n") + 1
                total += abs(new_lines - old_lines) + max(old_lines, new_lines)
        return total

    @property
    def total_deletions(self) -> int:
        total = 0
        for fa in self.files:
            if fa.action == "delete":
                total += fa.original.count("\n") + 1
            elif fa.action == "modify":
                old_lines = set(fa.original.splitlines())
                new_lines = set(fa.content.splitlines())
                total += len(old_lines - new_lines)
        return total


# ── Stage 5: Test ────────────────────────────────────────────────────


class TestFile(BaseModel):
    """A generated test file."""
    path: str
    content: str


class TestResult(BaseModel):
    """Test agent output — regression tests for the changes."""
    test_files: list[TestFile]
    test_strategy: str
    confidence: float = Field(ge=0.0, le=1.0)
    consternation: str = ""
    escalation: str = ""


# ── Stage 6: Verify (mechanical, no LLM) ────────────────────────────


class VerifyResult(BaseModel):
    """Result of mechanical verification: compile, lint, test."""
    compiles: bool = False
    lint_clean: bool = False
    tests_passing: int = 0
    tests_total: int = 0
    errors: list[str] = []


# ── Stage 7: Review (adversarial) ────────────────────────────────────


class ReviewSubVerdict(BaseModel):
    """Single sub-pass verdict from a focused review dimension."""
    pass_type: str  # "structural", "logic", "consistency", "blast_radius"
    verdict: Literal["approve", "concern", "reject"]
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str] = Field(default_factory=list)
    reasoning: str = ""
    consternation: str = ""
    escalation: str = ""

    @classmethod
    def model_validate(cls, obj, *args, **kwargs):
        """Handle model returning strings instead of lists for issues."""
        if isinstance(obj, dict):
            val = obj.get("issues")
            if val in (None, "None", "null", "none"):
                obj["issues"] = []
            elif isinstance(val, str):
                # Model returned a bullet-point string instead of a list
                lines = [line.lstrip("- ").strip() for line in val.strip().splitlines()]
                obj["issues"] = [line for line in lines if line]
        return super().model_validate(obj, *args, **kwargs)


class ReviewVerdict(BaseModel):
    """Review agent verdict — adversarial code review."""
    verdict: Literal["approve", "reject"]
    confidence: float = Field(ge=0.0, le=1.0)
    issues: list[str]
    risks: list[str] = []
    missed_edge_cases: list[str] = []
    reasoning: str
    consternation: str = ""
    escalation: str = ""
    sub_verdicts: list[ReviewSubVerdict] = []


class ClassifiedIssue(BaseModel):
    """A review issue classified as factual or subjective."""
    issue: str
    category: Literal["factual", "subjective"]
    evidence: str = ""


class IssueClassification(BaseModel):
    """Classification of review issues into factual vs subjective."""
    issues: list[ClassifiedIssue]
    reasoning: str
    consternation: str = ""
    escalation: str = ""


class ReviewEscalationResult(BaseModel):
    """Higher-level review decision when component review loops are exhausted."""
    decision: Literal["approve", "reject", "force_approve"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    conditions: list[str] = []
    consternation: str = ""
    escalation: str = ""


# ── Stage 8: Integrate ───────────────────────────────────────────────


class IntegrateResult(BaseModel):
    """Integrate agent output — cross-file coherence check."""
    coherent: bool
    issues: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    consternation: str = ""
    escalation: str = ""


# ── Stage 9: Submit (mechanical) ─────────────────────────────────────


class SubmitResult(BaseModel):
    """Result of the submit stage."""
    action: Literal["commit", "pr", "deploy", "none"]
    success: bool
    ref: str = ""
    url: str = ""
    errors: list[str] = []


# ── Guardian output ──────────────────────────────────────────────────


class GuardianResult(BaseModel):
    """Mechanical safety check result — no LLM involved."""
    passed: bool
    reasons: list[str]


# ── Gate result ──────────────────────────────────────────────────────


class GateResult(BaseModel):
    """Whether a stage gate passed, failed, or paused."""
    passed: bool
    paused: bool = False
    reason: str
    timeout_minutes: int = 0


# ── Formation types ─────────────────────────────────────────────────


class FormationStage(BaseModel):
    """A single stage within a formation."""
    name: str
    mode: Literal["cooperation", "adversarial"] = "cooperation"
    break_point: Literal["none", "pause"] = "none"
    competitors: int = 0
    review_sub_passes: list[str] = []
    architect_sub_passes: list[str] = []


class Formation(BaseModel):
    """Pipeline formation — defines stage ordering, modes, and break points."""
    name: str
    stages: list[FormationStage]
    max_review_loops: int = 0
    max_architect_review_loops: int = 0


# ── Pipeline signal ──────────────────────────────────────────────────


class PipelineSignal(BaseModel):
    """A signal emitted by an agent during pipeline execution."""
    stage: str
    signal_type: Literal["consternation", "escalation"]
    message: str


# ── Stage record (audit trail) ──────────────────────────────────────


class StageRecord(BaseModel):
    """Audit trail entry for a single pipeline stage."""
    stage: str
    passed: bool
    confidence: float = 0.0
    detail: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


# ── Human IO ─────────────────────────────────────────────────────────


class Question(BaseModel):
    """A question for the human operator."""
    id: str
    stage: str
    question: str
    context: str = ""
    options: list[str] = []
    answered: bool = False
    answer: str = ""


# ── Project run lifecycle ────────────────────────────────────────────


class ProjectRun(BaseModel):
    """Mutable lifecycle tracking a project run from init to completion."""
    id: str
    project_dir: str
    status: Literal["active", "paused", "completed", "failed", "budget_exceeded"] = "active"
    formation: str = ""
    current_stage_index: int = 0
    stages: list[StageRecord] = []
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: datetime | None = None
    pause_reason: str = ""
    signals: list[PipelineSignal] = []

    def record_stage(self, stage: StageRecord) -> None:
        self.stages.append(stage)
        self.total_tokens += stage.tokens_in + stage.tokens_out
        self.total_cost_usd += stage.cost_usd

    def pause(self, reason: str) -> None:
        self.status = "paused"
        self.pause_reason = reason

    def fail(self, reason: str) -> None:
        self.status = "failed"
        self.pause_reason = reason
        self.completed_at = datetime.now()

    def complete(self) -> None:
        self.status = "completed"
        self.completed_at = datetime.now()

    def mark_budget_exceeded(self) -> None:
        self.status = "budget_exceeded"
        self.pause_reason = "Budget cap reached"
        self.completed_at = datetime.now()


# ── Learning schemas ─────────────────────────────────────────────────


class LearningEntry(BaseModel):
    """A single validated learning from project feedback."""
    id: str
    lesson: str
    category: Literal[
        "architecture_pattern", "code_pattern", "file_location",
        "test_strategy", "review_feedback", "repo_convention", "failure_mode",
    ]
    repo: str = ""
    scope: str = ""
    source_project_ids: list[str]
    target_agents: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)
    times_validated: int = 0
    times_contradicted: int = 0
    created_at: datetime
    last_validated: datetime | None = None

    @property
    def is_stale(self) -> bool:
        """No validation in 30 days → stale."""
        if self.last_validated is None:
            from datetime import timedelta
            return (datetime.now() - self.created_at) > timedelta(days=30)
        from datetime import timedelta
        return (datetime.now() - self.last_validated) > timedelta(days=30)

    @property
    def is_decayed(self) -> bool:
        """Exclude from prompts when contradicted > validated + 2 or stale."""
        return self.times_contradicted > self.times_validated + 2


class LearningExtraction(BaseModel):
    """LLM output — schema-enforced via tool_choice."""
    learnings: list[str]
    categories: list[str]
    confidence_scores: list[float]
    target_stages: list[str] = []
    reasoning: str
