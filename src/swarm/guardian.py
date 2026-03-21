"""Mechanical safety — NO LLM. Cannot be persuaded.

Config-driven, regex-based checks. Extended from autofix to support
create/modify/delete file actions.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

from swarm.config import SwarmConfig
from swarm.schemas import ExecuteResult, GuardianResult, TestResult

# Patterns that suggest secrets or credentials
SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:api[_-]?key|secret|token|password|passwd|credential)\s*[:=]\s*['\"][^'\"]{8,}", re.IGNORECASE),
    re.compile(r"(?:sk|pk)[-_](?:live|test)[-_][a-zA-Z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),
    re.compile(r"xox[bpas]-[a-zA-Z0-9-]+"),
]


class GuardianAgent:
    """Mechanical safety. No LLM. Cannot be persuaded."""

    def check(self, execute: ExecuteResult, tests: TestResult | None, config: SwarmConfig) -> GuardianResult:
        failures: list[str] = []
        failures += self._check_forbidden_paths(execute, tests, config)
        failures += self._check_forbidden_patterns(execute, tests, config)
        failures += self._check_diff_size(execute, config)
        failures += self._check_deletion_count(execute, config)
        failures += self._check_file_count(execute, config)
        failures += self._check_no_secrets(execute, tests)
        if failures:
            return GuardianResult(passed=False, reasons=failures)
        return GuardianResult(passed=True, reasons=[])

    def _check_forbidden_paths(
        self, execute: ExecuteResult, tests: TestResult | None, config: SwarmConfig,
    ) -> list[str]:
        failures = []
        for fa in execute.files:
            basename = Path(fa.path).name
            for pattern in config.forbidden_paths:
                if fnmatch(basename, pattern):
                    failures.append(f"Forbidden path: {fa.path} matches {pattern}")
        if tests:
            for tf in tests.test_files:
                basename = Path(tf.path).name
                for pattern in config.forbidden_paths:
                    if fnmatch(basename, pattern):
                        failures.append(f"Forbidden test path: {tf.path} matches {pattern}")
        return failures

    def _check_forbidden_patterns(
        self, execute: ExecuteResult, tests: TestResult | None, config: SwarmConfig,
    ) -> list[str]:
        failures = []
        compiled = [re.compile(p) for p in config.forbidden_patterns]
        for fa in execute.files:
            content = fa.content if fa.action != "delete" else ""
            for regex in compiled:
                if regex.search(content):
                    failures.append(f"Forbidden pattern in {fa.path}: {regex.pattern}")
        if tests:
            for tf in tests.test_files:
                for regex in compiled:
                    if regex.search(tf.content):
                        failures.append(f"Forbidden pattern in test {tf.path}: {regex.pattern}")
        return failures

    def _check_diff_size(self, execute: ExecuteResult, config: SwarmConfig) -> list[str]:
        total = execute.total_diff_lines
        if total > config.max_diff_lines:
            return [f"Diff too large: {total} lines > {config.max_diff_lines} max"]
        return []

    def _check_deletion_count(self, execute: ExecuteResult, config: SwarmConfig) -> list[str]:
        total = execute.total_deletions
        if total > config.max_deletions:
            return [f"Too many deletions: {total} > {config.max_deletions} max"]
        return []

    def _check_file_count(self, execute: ExecuteResult, config: SwarmConfig) -> list[str]:
        count = len(execute.files)
        if count > config.max_files_changed:
            return [f"Too many files changed: {count} > {config.max_files_changed} max"]
        return []

    def _check_no_secrets(self, execute: ExecuteResult, tests: TestResult | None) -> list[str]:
        failures = []
        for fa in execute.files:
            content = fa.content if fa.action != "delete" else ""
            for pattern in SECRET_PATTERNS:
                if pattern.search(content):
                    failures.append(f"Possible secret in {fa.path}: matches {pattern.pattern[:40]}...")
        if tests:
            for tf in tests.test_files:
                for pattern in SECRET_PATTERNS:
                    if pattern.search(tf.content):
                        failures.append(f"Possible secret in test {tf.path}: matches {pattern.pattern[:40]}...")
        return failures
