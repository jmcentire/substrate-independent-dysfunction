"""LLM service wrapper — delegates to a Backend for actual LLM calls.

Thin wrapper that adds budget tracking and backend delegation.
The schema is the guardrail — tool_choice forces compliance.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from swarm.budget import BudgetTracker

T = TypeVar("T", bound=BaseModel)


class AgentBase:
    """Base class for all LLM agents. Wraps structured extraction + budget.

    Delegates to a Backend (default: AnthropicBackend). All existing call sites
    are unchanged — assess() signature is identical.
    """

    def __init__(self, budget: BudgetTracker, model: str = "claude-opus-4-6", backend: str = "anthropic") -> None:
        from swarm.backends import create_backend
        self._backend = create_backend(backend, budget, model)
        self._budget = budget
        self._model = model

    def set_model(self, model: str) -> None:
        """Switch the active model on the underlying backend."""
        self._model = model
        self._backend.set_model(model)

    def set_backend(self, backend_name: str, repo_path: object = None) -> None:
        """Switch to a different backend, preserving the current model and budget."""
        from swarm.backends import create_backend
        self._backend = create_backend(backend_name, self._budget, self._model, repo_path=repo_path)

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with schema enforcement. Returns (result, input_tokens, output_tokens).

        Uses tool_choice to force the model to produce output conforming to
        the Pydantic schema. The schema shapes generation — not validation after.
        """
        return await self._backend.assess(schema, prompt, system, max_tokens)

    async def close(self) -> None:
        await self._backend.close()
