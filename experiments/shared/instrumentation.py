"""Shared instrumentation for the 4-architecture experiment.

Provides JSONL event logging and budget tracking used by all architectures.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

# Pricing per million tokens (Opus 4, as of Feb 2026)
PRICE_INPUT_PER_M = 15.0
PRICE_OUTPUT_PER_M = 75.0


class BudgetExceeded(Exception):
    """Raised when cumulative spend exceeds the run budget cap."""

    def __init__(self, spent: float, cap: float):
        self.spent = spent
        self.cap = cap
        super().__init__(f"Budget exceeded: ${spent:.4f} spent, cap was ${cap:.2f}")


class BudgetTracker:
    """Track token usage and cost against a USD cap."""

    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def spent_usd(self) -> float:
        return (
            self.total_input_tokens * PRICE_INPUT_PER_M / 1_000_000
            + self.total_output_tokens * PRICE_OUTPUT_PER_M / 1_000_000
        )

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    def record(self, input_tokens: int, output_tokens: int) -> None:
        """Record token usage. Raises BudgetExceeded if cap breached."""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        if self.spent_usd > self.cap_usd:
            raise BudgetExceeded(self.spent_usd, self.cap_usd)

    def summary(self) -> dict:
        return {
            "cap_usd": self.cap_usd,
            "spent_usd": round(self.spent_usd, 4),
            "remaining_usd": round(self.remaining_usd, 4),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }


class InstrumentationLogger:
    """Append-only JSONL event logger for experiment runs."""

    def __init__(self, log_path: Path):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._start_wall = time.monotonic()

    def log(
        self,
        agent_id: str,
        event_type: str,
        summary: str,
        tokens_in: int = 0,
        tokens_out: int = 0,
        extra: dict | None = None,
    ) -> None:
        """Append one event to the JSONL log."""
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "wall_s": round(time.monotonic() - self._start_wall, 3),
            "agent": agent_id,
            "event": event_type,
            "summary": summary,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        }
        if extra:
            record["extra"] = extra
        with open(self.log_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_wall
