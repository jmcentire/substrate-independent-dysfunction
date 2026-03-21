"""Human IO — interactive, file-based, and autonomous modes.

Three modes:
  Interactive: Terminal prompts at break points and questions.
  File:        Questions → questions/pending/, human edits → questions/answered/. Polls.
  Autonomous:  No questions. Only hard break points interrupt.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from swarm.schemas import Question

logger = logging.getLogger(__name__)


class HumanIO:
    """Base class for human IO modes."""

    def ask(self, stage: str, question: str, context: str = "", options: list[str] | None = None) -> str:
        """Ask the human a question. Returns the answer."""
        raise NotImplementedError

    def notify(self, stage: str, message: str) -> None:
        """Notify the human (no response expected)."""
        raise NotImplementedError

    def confirm_break_point(self, stage: str, reason: str) -> bool:
        """Ask human to confirm at a break point. Returns True to continue."""
        raise NotImplementedError

    def answer_decomposition_questions(self, questions: list) -> dict[str, str]:
        """Answer decomposition questions. Returns {question: answer} dict."""
        raise NotImplementedError


class InteractiveIO(HumanIO):
    """Terminal-based interactive IO."""

    def answer_decomposition_questions(self, questions: list) -> dict[str, str]:
        answers = {}
        print("\n[decompose] Questions for decomposition:")
        for q in questions:
            importance = getattr(q, "importance", "clarifying")
            context = getattr(q, "context", "")
            print(f"\n  [{importance.upper()}] {q.question}")
            if context:
                print(f"  Context: {context}")
            answer = input("  Answer (or press Enter to skip): ").strip()
            if answer:
                answers[q.question] = answer
        return answers

    def ask(self, stage: str, question: str, context: str = "", options: list[str] | None = None) -> str:
        print(f"\n[{stage}] {question}")
        if context:
            print(f"  Context: {context}")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
            print()
            response = input("  Choice (number or text): ").strip()
            try:
                idx = int(response) - 1
                if 0 <= idx < len(options):
                    return options[idx]
            except ValueError:
                pass
            return response
        return input("  Answer: ").strip()

    def notify(self, stage: str, message: str) -> None:
        print(f"[{stage}] {message}")

    def confirm_break_point(self, stage: str, reason: str) -> bool:
        print(f"\n[BREAK POINT] {stage}: {reason}")
        response = input("  Continue? (y/n): ").strip().lower()
        return response in ("y", "yes", "")


class FileIO(HumanIO):
    """File-based IO for non-interactive environments."""

    def answer_decomposition_questions(self, questions: list) -> dict[str, str]:
        answers = {}
        for q in questions:
            answer = self.ask("decompose", q.question, context=getattr(q, "context", ""))
            if answer:
                answers[q.question] = answer
        return answers

    def __init__(self, project_dir: Path, poll_interval: float = 5.0, timeout: float = 3600.0) -> None:
        self._pending_dir = project_dir / "questions" / "pending"
        self._answered_dir = project_dir / "questions" / "answered"
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._answered_dir.mkdir(parents=True, exist_ok=True)

    def ask(self, stage: str, question: str, context: str = "", options: list[str] | None = None) -> str:
        q_id = uuid4().hex[:8]
        q = Question(
            id=q_id, stage=stage, question=question,
            context=context, options=options or [],
        )
        q_file = self._pending_dir / f"{q_id}.json"
        q_file.write_text(q.model_dump_json(indent=2))
        logger.info("Question written: %s", q_file)

        # Poll for answer
        a_file = self._answered_dir / f"{q_id}.json"
        start = time.monotonic()
        while time.monotonic() - start < self._timeout:
            if a_file.exists():
                data = json.loads(a_file.read_text())
                answer = data.get("answer", "")
                # Clean up
                q_file.unlink(missing_ok=True)
                return answer
            time.sleep(self._poll_interval)

        raise TimeoutError(f"No answer received for question {q_id} within {self._timeout}s")

    def notify(self, stage: str, message: str) -> None:
        logger.info("[%s] %s", stage, message)

    def confirm_break_point(self, stage: str, reason: str) -> bool:
        answer = self.ask(stage, f"Break point: {reason}. Continue?", options=["yes", "no"])
        return answer.lower() in ("yes", "y", "")


class AutonomousIO(HumanIO):
    """No human interaction — only hard break points interrupt."""

    def answer_decomposition_questions(self, questions: list) -> dict[str, str]:
        logger.info("[decompose] Autonomous mode — skipping %d questions, using assumptions", len(questions))
        return {}

    def ask(self, stage: str, question: str, context: str = "", options: list[str] | None = None) -> str:
        logger.info("[%s] Auto-answering question: %s", stage, question)
        if options:
            return options[0]
        return ""

    def notify(self, stage: str, message: str) -> None:
        logger.info("[%s] %s", stage, message)

    def confirm_break_point(self, stage: str, reason: str) -> bool:
        logger.info("[%s] Break point (autonomous mode): %s — continuing", stage, reason)
        return True


def create_io(mode: str, project_dir: Path | None = None) -> HumanIO:
    """Factory for IO modes."""
    if mode == "interactive":
        return InteractiveIO()
    elif mode == "file":
        if project_dir is None:
            raise ValueError("File IO mode requires project_dir")
        return FileIO(project_dir)
    elif mode == "autonomous":
        return AutonomousIO()
    else:
        raise ValueError(f"Unknown IO mode: {mode}")
