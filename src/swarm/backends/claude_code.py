"""Claude Code CLI backend — uses `claude` with tool access for codebase-aware stages.

Two modes:
  - Structured extraction (assess): schema-enforced JSON output via prompt engineering
  - Tool-aware extraction (assess with repo_path): Claude Code reads files, searches
    code, runs commands — then produces schema-enforced output

The key advantage over the Anthropic API: Claude Code can look at the actual codebase
before answering. The architect review can grep for existing implementations instead
of guessing. The locate stage can search real files.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from swarm.budget import BudgetExceeded, BudgetTracker

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class ClaudeCodeBackend:
    """Backend using the claude CLI with optional tool access.

    When repo_path is set, uses --allowedTools to give Claude Code access
    to file reading, searching, and bash commands scoped to the repo.
    """

    def __init__(
        self,
        budget: BudgetTracker,
        model: str = "claude-opus-4-6",
        repo_path: Path | None = None,
    ) -> None:
        self._model = model
        self._budget = budget
        self._repo_path = repo_path
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    def set_model(self, model: str) -> None:
        """Switch the active model."""
        self._model = model

    def set_repo_path(self, path: Path) -> None:
        """Set the repo path for codebase-aware tool access."""
        self._repo_path = path

    async def assess(
        self,
        schema: type[T],
        prompt: str,
        system: str,
        max_tokens: int = 32768,
    ) -> tuple[T, int, int]:
        """Call claude CLI with schema enforcement.

        If repo_path is set, Claude Code gets tool access to read files and
        search code within the repo before producing its structured output.

        Returns (result, input_tokens, output_tokens).
        """
        schema_json = json.dumps(schema.model_json_schema(), indent=2)

        full_prompt = (
            f"{system}\n\n"
            f"You MUST respond with a JSON object matching this schema:\n"
            f"```json\n{schema_json}\n```\n\n"
        )

        # If we have a repo path, tell the agent it can explore
        if self._repo_path and self._repo_path.exists():
            full_prompt += (
                f"You have access to the codebase at: {self._repo_path}\n"
                f"Use your tools to read files, search code, and explore the repository "
                f"structure BEFORE forming your response. Ground your analysis in what "
                f"actually exists in the codebase, not assumptions.\n\n"
            )

        full_prompt += (
            f"Task:\n{prompt}\n\n"
            f"After any exploration, respond ONLY with the JSON object matching the schema above."
        )

        cmd = ["claude", "-p", full_prompt, "--output-format", "json"]

        # Add model flag
        cmd.extend(["--model", self._model])

        # Add tool access when repo_path is set
        if self._repo_path and self._repo_path.exists():
            cmd.extend([
                "--allowedTools", "Read,Glob,Grep,Bash",
            ])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._repo_path) if self._repo_path else None,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (exit {proc.returncode}): {stderr.decode()[:500]}"
            )

        raw = stdout.decode()

        # Parse the CLI JSON response
        try:
            cli_response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse claude CLI output as JSON: {exc}") from exc

        # Extract the result text from CLI response
        result_text = cli_response.get("result", raw)
        if isinstance(result_text, str):
            # Try to extract JSON from the result text (may have surrounding text)
            data = self._extract_json(result_text)
        elif isinstance(result_text, dict):
            data = result_text
        else:
            raise RuntimeError(f"Unexpected result type from claude CLI: {type(result_text)}")

        # Use cost_usd from CLI response if available, otherwise estimate tokens
        cost_usd = cli_response.get("cost_usd", 0.0)
        in_tok = cli_response.get("input_tokens", len(full_prompt) // 4)
        out_tok = cli_response.get("output_tokens", len(raw) // 4)
        self._total_input_tokens += in_tok
        self._total_output_tokens += out_tok

        if not self._budget.record_tokens(in_tok, out_tok):
            raise BudgetExceeded(f"Budget exceeded after {in_tok}+{out_tok} tokens")

        return schema.model_validate(data), in_tok, out_tok

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract JSON object from text that may contain surrounding content."""
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON block in markdown code fence
        import re
        json_match = re.search(r'```(?:json)?\s*\n({.*?})\s*\n```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find first { ... } block
        brace_start = text.find('{')
        if brace_start >= 0:
            # Find matching closing brace
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[brace_start:i + 1])
                        except json.JSONDecodeError:
                            break

        raise RuntimeError(f"Claude CLI result contains no valid JSON: {text[:200]}")

    async def close(self) -> None:
        """No persistent resources to release."""
        pass
