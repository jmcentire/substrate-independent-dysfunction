"""Subprocess runner — git, test, lint, deploy.

Language-agnostic. Commands come from swarm.yaml config.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from swarm.config import ProjectConfig
from swarm.schemas import ExecuteResult, SubmitResult, TestResult, VerifyResult

logger = logging.getLogger(__name__)


async def _run(cmd: list[str], cwd: Path, timeout: float = 60.0) -> tuple[int, str, str]:
    """Run a subprocess with timeout. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(), stderr.decode()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return 1, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"


async def apply_changes(repo_path: Path, execute_result: ExecuteResult) -> list[str]:
    """Apply file changes (create/modify/delete). Returns list of errors."""
    errors = []
    for fa in execute_result.files:
        filepath = repo_path / fa.path
        if fa.action == "create":
            filepath.parent.mkdir(parents=True, exist_ok=True)
            filepath.write_text(fa.content)
        elif fa.action == "modify":
            if not filepath.exists():
                errors.append(f"File not found for modify: {fa.path}")
                continue
            content = filepath.read_text()
            if fa.original not in content:
                errors.append(f"Original text not found in {fa.path} — cannot apply change")
                continue
            new_content = content.replace(fa.original, fa.content, 1)
            filepath.write_text(new_content)
        elif fa.action == "delete":
            if filepath.exists():
                filepath.unlink()
            else:
                errors.append(f"File not found for delete: {fa.path}")
    return errors


async def write_tests(repo_path: Path, tests: TestResult) -> list[str]:
    """Write test files to disk. Returns list of errors."""
    errors = []
    for tf in tests.test_files:
        filepath = repo_path / tf.path
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(tf.content)
    return errors


async def run_verification(repo_path: Path, config: ProjectConfig) -> VerifyResult:
    """Run compile/lint/test and return verification result."""
    errors: list[str] = []
    compiles = True
    lint_clean = True
    tests_passing = 0
    tests_total = 0

    # Typecheck
    if config.typecheck_command:
        rc, out, err = await _run(config.typecheck_command, repo_path, timeout=120.0)
        compiles = rc == 0
        if not compiles:
            errors.append(f"Typecheck failed: {err[:500]}")

    # Lint
    if config.lint_command:
        rc, out, err = await _run(config.lint_command, repo_path, timeout=60.0)
        lint_clean = rc == 0
        if not lint_clean:
            errors.append(f"Lint failed: {err[:500]}")

    # Test
    if config.test_command:
        rc, out, err = await _run(config.test_command, repo_path, timeout=180.0)
        test_output = out + err
        if rc == 0:
            tests_passing = 1
            tests_total = 1
        else:
            tests_total = 1
            errors.append(f"Tests failed: {test_output[:500]}")

    return VerifyResult(
        compiles=compiles,
        lint_clean=lint_clean,
        tests_passing=tests_passing,
        tests_total=max(tests_total, 0),
        errors=errors,
    )


async def submit_changes(
    repo_path: Path,
    execute_result: ExecuteResult,
    test_result: TestResult | None,
    task_summary: str,
    config: ProjectConfig,
    run_id: str = "",
    dry_run: bool = True,
) -> SubmitResult:
    """Create commit/PR/deploy based on config. Returns SubmitResult."""
    if config.submit_mode == "none":
        return SubmitResult(action="none", success=True)

    branch = f"{config.branch_prefix}{run_id[:16]}" if run_id else f"{config.branch_prefix}task"

    # Save current branch
    rc, original_branch, _ = await _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    original_branch = original_branch.strip()

    try:
        # Create branch
        rc, _, err = await _run(["git", "checkout", "-b", branch], repo_path)
        if rc != 0:
            return SubmitResult(action=config.submit_mode, success=False, errors=[f"Branch creation failed: {err}"])

        # Apply changes
        change_errors = await apply_changes(repo_path, execute_result)
        if change_errors:
            return SubmitResult(action=config.submit_mode, success=False, errors=change_errors)

        # Write tests
        if test_result:
            await write_tests(repo_path, test_result)

        # Stage files
        all_files = [fa.path for fa in execute_result.files if fa.action != "delete"]
        if test_result:
            all_files += [tf.path for tf in test_result.test_files]
        for f in all_files:
            await _run(["git", "add", f], repo_path)
        for fa in execute_result.files:
            if fa.action == "delete":
                await _run(["git", "rm", fa.path], repo_path)

        # Commit
        commit_msg = (
            f"[swarm] {task_summary[:60]}\n\n"
            f"Run: {run_id}\n\n"
            f"Co-Authored-By: swarm <noreply@swarm.dev>"
        )

        if config.submit_mode == "commit":
            rc, _, err = await _run(["git", "commit", "-m", commit_msg], repo_path)
            if rc != 0:
                return SubmitResult(action="commit", success=False, errors=[f"Commit failed: {err}"])
            return SubmitResult(action="commit", success=True, ref=branch)

        if dry_run:
            return SubmitResult(action=config.submit_mode, success=True, ref=f"dry-run://{branch}")

        # Commit + push + PR
        rc, _, err = await _run(["git", "commit", "-m", commit_msg], repo_path)
        if rc != 0:
            return SubmitResult(action="pr", success=False, errors=[f"Commit failed: {err}"])

        rc, _, err = await _run(["git", "push", "-u", "origin", branch], repo_path, timeout=60.0)
        if rc != 0:
            return SubmitResult(action="pr", success=False, errors=[f"Push failed: {err}"])

        pr_body = (
            f"## Swarm\n\n"
            f"**Task:** {task_summary}\n"
            f"**Run:** {run_id}\n\n"
            f"---\n"
            f"*Generated by swarm. Please review carefully before merging.*"
        )

        pr_flags = ["gh", "pr", "create", "--title", f"[swarm] {task_summary[:60]}", "--body", pr_body]
        if config.draft_pr:
            pr_flags.append("--draft")

        rc, out, err = await _run(pr_flags, repo_path, timeout=30.0)
        if rc != 0:
            return SubmitResult(action="pr", success=False, errors=[f"PR creation failed: {err}"])

        return SubmitResult(action="pr", success=True, url=out.strip(), ref=branch)

    finally:
        await _run(["git", "checkout", original_branch], repo_path)
        if dry_run and config.submit_mode != "commit":
            await _run(["git", "branch", "-D", branch], repo_path)
