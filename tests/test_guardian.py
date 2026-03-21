"""Tests for GuardianAgent — mechanical safety checks."""

from __future__ import annotations

import pytest

from swarm.config import SwarmConfig
from swarm.guardian import GuardianAgent
from swarm.schemas import ExecuteResult, FileAction, TestFile, TestResult


def _config(**kwargs) -> SwarmConfig:
    return SwarmConfig(**kwargs)


def _execute(files: list[FileAction] | None = None) -> ExecuteResult:
    return ExecuteResult(
        files=files or [FileAction(path="src/app.py", action="modify", original="old", content="new")],
        explanation="Fix", confidence=0.9,
    )


def _tests(test_files: list[TestFile] | None = None) -> TestResult:
    return TestResult(
        test_files=test_files or [TestFile(path="tests/test_app.py", content="def test(): pass")],
        test_strategy="Unit", confidence=0.8,
    )


class TestGuardianBasic:
    def test_passes_clean_change(self):
        g = GuardianAgent()
        result = g.check(_execute(), _tests(), _config())
        assert result.passed is True
        assert result.reasons == []

    def test_passes_without_tests(self):
        g = GuardianAgent()
        result = g.check(_execute(), None, _config())
        assert result.passed is True


class TestForbiddenPaths:
    def test_blocks_env_file(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path=".env.local", action="modify", original="x", content="y")])
        result = g.check(exe, None, _config())
        assert result.passed is False
        assert any("Forbidden path" in r for r in result.reasons)

    def test_blocks_pem_file(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path="certs/server.pem", action="modify", original="x", content="y")])
        result = g.check(exe, None, _config())
        assert result.passed is False

    def test_blocks_forbidden_test_path(self):
        g = GuardianAgent()
        tests = _tests([TestFile(path=".env.test", content="test")])
        result = g.check(_execute(), tests, _config())
        assert result.passed is False


class TestForbiddenPatterns:
    def test_blocks_process_exit(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path="a.py", action="modify", original="x", content="process.exit(1)")])
        result = g.check(exe, None, _config())
        assert result.passed is False
        assert any("Forbidden pattern" in r for r in result.reasons)

    def test_blocks_rm_rf(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path="a.sh", action="create", content="rm -rf /")])
        result = g.check(exe, None, _config())
        assert result.passed is False

    def test_blocks_drop_table(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path="a.sql", action="create", content="DROP TABLE users;")])
        result = g.check(exe, None, _config())
        assert result.passed is False

    def test_blocks_pattern_in_tests(self):
        g = GuardianAgent()
        tests = _tests([TestFile(path="test.py", content="eval('bad')")])
        result = g.check(_execute(), tests, _config())
        assert result.passed is False


class TestDiffSizeLimits:
    def test_blocks_large_diff(self):
        g = GuardianAgent()
        big_content = "\n".join(f"line{i}" for i in range(600))
        exe = _execute([FileAction(path="a.py", action="create", content=big_content)])
        result = g.check(exe, None, _config())
        assert result.passed is False
        assert any("Diff too large" in r for r in result.reasons)

    def test_blocks_too_many_deletions(self):
        g = GuardianAgent()
        big_original = "\n".join(f"line{i}" for i in range(250))
        exe = _execute([FileAction(path="a.py", action="delete", original=big_original)])
        result = g.check(exe, None, _config())
        assert result.passed is False
        assert any("Too many deletions" in r for r in result.reasons)


class TestFileCount:
    def test_blocks_too_many_files(self):
        g = GuardianAgent()
        files = [FileAction(path=f"f{i}.py", action="modify", original="x", content="y") for i in range(25)]
        exe = _execute(files)
        result = g.check(exe, None, _config())
        assert result.passed is False
        assert any("Too many files" in r for r in result.reasons)


class TestSecrets:
    # NOTE: Fake keys use string concatenation to avoid triggering GitHub push
    # protection. At runtime the concatenated string still matches the guardian's
    # regex pattern r"(?:sk|pk)[-_](?:live|test)[-_][a-zA-Z0-9]{20,}".
    def test_blocks_api_key(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path="a.py", action="modify", original="x",
                                   content='api_key = "sk_live_FAKE' + 'KEY0123456789abcdef01234567"')])
        result = g.check(exe, None, _config())
        assert result.passed is False
        assert any("secret" in r.lower() or "Possible" in r for r in result.reasons)

    def test_blocks_private_key(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path="a.py", action="create",
                                   content="-----BEGIN RSA PRIVATE KEY-----\nxyz\n-----END RSA PRIVATE KEY-----")])
        result = g.check(exe, None, _config())
        assert result.passed is False

    def test_blocks_github_token(self):
        g = GuardianAgent()
        exe = _execute([FileAction(path="a.py", action="modify", original="x",
                                   content="token = 'ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij'")])
        result = g.check(exe, None, _config())
        assert result.passed is False

    def test_blocks_secret_in_test(self):
        g = GuardianAgent()
        tests = _tests([TestFile(path="test.py", content="ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")])
        result = g.check(_execute(), tests, _config())
        assert result.passed is False

    def test_skip_content_check_on_delete(self):
        """Delete actions should not check content for secrets (content is empty)."""
        g = GuardianAgent()
        exe = _execute([FileAction(path="a.py", action="delete",
                                   original='api_key = "sk_live_FAKE' + 'KEY0123456789abcdef01234567"')])
        result = g.check(exe, None, _config())
        # Should pass because delete doesn't check content
        assert result.passed is True
