"""Stage gate consensus logic — pure functions, deterministic.

Each gate checks confidence threshold + stage-specific constraints.
These are rigid boundaries: no LLM judgment, no persuasion.
"""

from __future__ import annotations

from swarm.config import SwarmConfig
from swarm.schemas import (
    DiagnoseResult,
    GateResult,
    IntegrateResult,
    LocateResult,
    ReviewVerdict,
    VerifyResult,
)


def diagnose_gate(result: DiagnoseResult, config: SwarmConfig) -> GateResult:
    """Gate 1: Is this task feasible?"""
    if result.complexity == "epic":
        return GateResult(passed=True, reason="Epic complexity — decomposition required")
    if result.confidence < config.diagnose_min_confidence:
        return GateResult(
            passed=False,
            reason=f"Confidence {result.confidence:.2f} < {config.diagnose_min_confidence} threshold",
        )
    return GateResult(passed=True, reason="Diagnose passed")


def locate_gate(result: LocateResult, config: SwarmConfig) -> GateResult:
    """Gate 2: Did we find plausible files?"""
    if result.confidence < config.locate_min_confidence:
        return GateResult(
            passed=False,
            reason=f"Confidence {result.confidence:.2f} < {config.locate_min_confidence} threshold",
        )
    if len(result.files) == 0:
        return GateResult(passed=False, reason="No files identified")
    if len(result.files) > config.locate_max_files:
        return GateResult(
            passed=False,
            reason=f"Too many files ({len(result.files)}) > {config.locate_max_files} max",
        )
    return GateResult(passed=True, reason="Locate passed")


def verify_gate(result: VerifyResult) -> GateResult:
    """Gate 3: Did mechanical verification pass?"""
    errors = []
    if not result.compiles:
        errors.append("Compilation failed")
    if result.tests_total > 0 and result.tests_passing < result.tests_total:
        errors.append(f"Tests: {result.tests_passing}/{result.tests_total} passing")
    if result.errors:
        errors.extend(result.errors[:3])

    if errors:
        return GateResult(passed=False, reason="; ".join(errors))
    return GateResult(passed=True, reason="Verification passed")


def review_gate(verdict: ReviewVerdict, config: SwarmConfig) -> GateResult:
    """Gate 4: Does the reviewer approve?"""
    if verdict.verdict != "approve":
        issues = "; ".join(verdict.issues) if verdict.issues else "No specific issues"
        return GateResult(passed=False, reason=f"Review rejected: {issues}")
    if verdict.confidence < config.review_min_confidence:
        return GateResult(
            passed=False,
            reason=f"Review confidence {verdict.confidence:.2f} < {config.review_min_confidence} threshold",
        )
    if verdict.escalation:
        return GateResult(
            passed=True, paused=True,
            reason=f"Review approved but escalated: {verdict.escalation}",
        )
    return GateResult(passed=True, reason="Review approved")


def integrate_gate(result: IntegrateResult, config: SwarmConfig) -> GateResult:
    """Gate 5: Is cross-file coherence satisfied?"""
    if not result.coherent:
        issues = "; ".join(result.issues) if result.issues else "Coherence check failed"
        return GateResult(passed=False, reason=f"Integration: {issues}")
    if result.confidence < config.integrate_min_confidence:
        return GateResult(
            passed=False,
            reason=f"Integration confidence {result.confidence:.2f} < {config.integrate_min_confidence} threshold",
        )
    return GateResult(passed=True, reason="Integration passed")
