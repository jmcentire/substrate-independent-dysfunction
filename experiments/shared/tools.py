"""Sandboxed file and command tools for experiment agents.

Each tool factory returns a list of @beta_tool-decorated functions scoped
to a specific working directory.  Paths are validated to stay inside the
sandbox.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated

from anthropic import beta_tool
from pydantic import Field


class SandboxViolation(Exception):
    """Raised when a tool tries to escape its sandbox directory."""


def _resolve_safe(work_dir: Path, rel: str) -> Path:
    """Resolve *rel* inside *work_dir*; raise on escape."""
    target = (work_dir / rel).resolve()
    if not target.is_relative_to(work_dir.resolve()):
        raise SandboxViolation(f"Path escapes sandbox: {rel}")
    return target


def make_tools(work_dir: Path) -> list:
    """Return four sandboxed tools bound to *work_dir*.

    Tools: write_file, read_file, list_directory, run_command
    """
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    @beta_tool
    def write_file(
        path: Annotated[str, Field(description="Relative file path to create or overwrite (e.g. 'src/main.py')")],
        content: Annotated[str, Field(description="The complete text content to write to the file")],
    ) -> str:
        """Write content to a file at the given path. Creates parent directories as needed. Both path and content are required."""
        target = _resolve_safe(work_dir, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    @beta_tool
    def read_file(
        path: Annotated[str, Field(description="Relative file path to read (e.g. 'src/main.py')")],
    ) -> str:
        """Read a file and return its contents. Returns up to 50 KB."""
        target = _resolve_safe(work_dir, path)
        if not target.exists():
            return f"Error: {path} does not exist"
        text = target.read_text()
        if len(text) > 50_000:
            return text[:50_000] + f"\n... [truncated, {len(text)} bytes total]"
        return text

    @beta_tool
    def list_directory(
        path: Annotated[str, Field(description="Relative directory path to list (e.g. '.' or 'src/')", default=".")],
    ) -> str:
        """List all files and directories recursively. Returns paths prefixed with 'f ' (file) or 'd ' (directory)."""
        if not path:
            path = "."
        target = _resolve_safe(work_dir, path)
        if not target.exists():
            return f"Error: {path} does not exist"
        if not target.is_dir():
            return f"Error: {path} is not a directory"
        entries = []
        for p in sorted(target.rglob("*")):
            rel = p.relative_to(work_dir)
            prefix = "d " if p.is_dir() else "f "
            entries.append(prefix + str(rel))
        if not entries:
            return "(empty directory)"
        return "\n".join(entries[:500])

    @beta_tool
    def run_command(
        command: Annotated[str, Field(description="Shell command to execute (e.g. 'python3 test.py' or 'ls -la')")],
    ) -> str:
        """Run a shell command in the working directory. Has a 30-second timeout. Returns stdout+stderr."""
        try:
            result = asyncio.get_event_loop().run_until_complete(
                _run_subprocess(command, work_dir)
            )
            return result
        except RuntimeError:
            # No running event loop — run synchronously
            import subprocess

            try:
                proc = subprocess.run(
                    command,
                    shell=True,
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env={**os.environ, "HOME": str(work_dir)},
                )
                output = ""
                if proc.stdout:
                    output += proc.stdout
                if proc.stderr:
                    output += proc.stderr
                if proc.returncode != 0:
                    output += f"\n[exit code: {proc.returncode}]"
                return output[:20_000] if output else "(no output)"
            except subprocess.TimeoutExpired:
                return "Error: command timed out (30s limit)"

    @beta_tool
    def ask_question(
        question: Annotated[str, Field(description="Question about requirements, architecture, or existing code")],
    ) -> str:
        """Ask the experiment oracle a question about requirements, architecture, or existing code. Returns any pre-seeded answers relevant to your question."""
        # Write question to log
        oracle_dir = work_dir / "oracle"
        oracle_dir.mkdir(exist_ok=True)
        q_file = oracle_dir / "questions.jsonl"
        with open(q_file, "a") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(), "q": question}) + "\n")

        # Check for pre-seeded answers
        a_file = oracle_dir / "answers.jsonl"
        if a_file.exists():
            try:
                answers = [json.loads(line) for line in a_file.read_text().strip().split("\n") if line.strip()]
                if answers:
                    return "\n\n".join(f"Q: {a['q']}\nA: {a['a']}" for a in answers)
            except (json.JSONDecodeError, KeyError):
                pass

        return "Question recorded. No pre-seeded answers available. Continue with your best judgment."

    return [write_file, read_file, list_directory, run_command, ask_question]


async def _run_subprocess(command: str, cwd: Path) -> str:
    """Run a shell command asynchronously with a 30-second timeout."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HOME": str(cwd)},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = ""
        if stdout:
            output += stdout.decode(errors="replace")
        if stderr:
            output += stderr.decode(errors="replace")
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"
        return output[:20_000] if output else "(no output)"
    except asyncio.TimeoutError:
        proc.kill()
        return "Error: command timed out (30s limit)"
