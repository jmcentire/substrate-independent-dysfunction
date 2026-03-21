"""Backend protocol + factory — decouples agents from LLM provider.

Backend is a Protocol: any class implementing assess/set_model/close
can be used as an LLM backend. Factory creates backends by name.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Backend(Protocol):
    """Protocol for LLM backends — structured extraction + model switching."""

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call LLM with schema enforcement. Returns (result, input_tokens, output_tokens)."""
        ...

    def set_model(self, model: str) -> None:
        """Switch the active model."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


def create_backend(name: str, budget: object, model: str, repo_path: object = None) -> Backend:
    """Factory: create a Backend by name.

    Args:
        name: Backend type — "anthropic" or "claude_code".
        budget: BudgetTracker instance for token accounting.
        model: Default model identifier.
        repo_path: Optional repo path for codebase-aware backends.

    Returns:
        A Backend instance.
    """
    if name == "anthropic":
        from swarm.backends.anthropic import AnthropicBackend
        return AnthropicBackend(budget=budget, model=model)
    elif name == "claude_code":
        from swarm.backends.claude_code import ClaudeCodeBackend
        return ClaudeCodeBackend(budget=budget, model=model, repo_path=repo_path)
    else:
        raise ValueError(f"Unknown backend: {name}. Available: anthropic, claude_code")
