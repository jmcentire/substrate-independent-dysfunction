"""Tests for SwarmConfig and ProjectConfig loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from swarm.config import (
    ProjectConfig,
    SwarmConfig,
    load_project_config,
    load_swarm_config,
)


class TestSwarmConfig:
    def test_defaults(self):
        c = SwarmConfig()
        assert c.model == "claude-opus-4-6"
        assert c.default_formation == "auto"
        assert c.default_io_mode == "interactive"
        assert c.default_budget == 10.00
        assert c.diagnose_min_confidence == 0.6
        assert c.max_diff_lines == 500
        assert c.default_stage_models == {}

    def test_load_missing_file(self, tmp_path):
        c = load_swarm_config(tmp_path / "nonexistent.yaml")
        assert c.model == "claude-opus-4-6"

    def test_load_from_yaml(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "model: claude-opus-4-6\n"
            "default_budget: 10.00\n"
            "max_diff_lines: 1000\n"
        )
        c = load_swarm_config(cfg_file)
        assert c.model == "claude-opus-4-6"
        assert c.default_budget == 10.00
        assert c.max_diff_lines == 1000

    def test_forbidden_paths_default(self):
        c = SwarmConfig()
        assert "*.env*" in c.forbidden_paths
        assert "*.pem" in c.forbidden_paths


class TestProjectConfig:
    def test_defaults(self):
        c = ProjectConfig()
        assert c.repo == ""
        assert c.formation == "auto"
        assert c.io_mode == "interactive"
        assert c.budget == 10.00
        assert c.submit_mode == "none"

    def test_repo_path(self):
        c = ProjectConfig(repo="/tmp/myrepo")
        assert c.repo_path == Path("/tmp/myrepo").resolve()

    def test_commands(self):
        c = ProjectConfig(commands={
            "test": ["npm", "test"],
            "lint": ["npm", "run", "lint"],
        })
        assert c.test_command == ["npm", "test"]
        assert c.lint_command == ["npm", "run", "lint"]
        assert c.typecheck_command == []

    def test_load_missing_file(self, tmp_path):
        c = load_project_config(tmp_path)
        assert c.formation == "auto"

    def test_load_from_yaml(self, tmp_path):
        cfg_file = tmp_path / "swarm.yaml"
        cfg_file.write_text(
            "repo: /tmp/myrepo\n"
            "formation: thorough\n"
            "io_mode: autonomous\n"
            "model: claude-opus-4-6\n"
            "budget: 10.00\n"
            "break_points: [architect, review, submit]\n"
            "commands:\n"
            "  test: [pytest, tests/]\n"
            "  lint: [ruff, check, .]\n"
            "submit:\n"
            "  mode: pr\n"
            "  branch_prefix: feature/\n"
            "  draft_pr: false\n"
        )
        c = load_project_config(tmp_path)
        assert c.repo == "/tmp/myrepo"
        assert c.formation == "thorough"
        assert c.io_mode == "autonomous"
        assert c.model == "claude-opus-4-6"
        assert c.budget == 10.00
        assert c.break_points == ["architect", "review", "submit"]
        assert c.test_command == ["pytest", "tests/"]
        assert c.submit_mode == "pr"
        assert c.branch_prefix == "feature/"
        assert c.draft_pr is False

    def test_load_custom_stages(self, tmp_path):
        cfg_file = tmp_path / "swarm.yaml"
        cfg_file.write_text(
            "formation: custom\n"
            "custom_stages: [diagnose, execute, verify, submit]\n"
        )
        c = load_project_config(tmp_path)
        assert c.formation == "custom"
        assert c.custom_stages == ["diagnose", "execute", "verify", "submit"]

    def test_load_stage_models(self, tmp_path):
        cfg_file = tmp_path / "swarm.yaml"
        cfg_file.write_text(
            "stage_models:\n"
            "  diagnose: claude-sonnet-4-5-20250929\n"
            "  execute: claude-sonnet-4-5-20250929\n"
            "  review: claude-opus-4-6\n"
        )
        c = load_project_config(tmp_path)
        assert c.stage_models["diagnose"] == "claude-sonnet-4-5-20250929"
        assert c.stage_models["review"] == "claude-opus-4-6"

    def test_load_backend(self, tmp_path):
        cfg_file = tmp_path / "swarm.yaml"
        cfg_file.write_text("backend: claude_code\n")
        c = load_project_config(tmp_path)
        assert c.backend == "claude_code"

    def test_default_backend(self):
        c = ProjectConfig()
        assert c.backend == "anthropic"
        assert c.stage_models == {}
        assert c.stage_backends == {}

    def test_load_stage_backends(self, tmp_path):
        cfg_file = tmp_path / "swarm.yaml"
        cfg_file.write_text(
            "stage_backends:\n"
            "  architect_review: claude_code\n"
            "  locate: claude_code\n"
            "  execute: claude_code\n"
        )
        c = load_project_config(tmp_path)
        assert c.stage_backends["architect_review"] == "claude_code"
        assert c.stage_backends["locate"] == "claude_code"
        assert c.stage_backends["execute"] == "claude_code"


class TestSwarmConfigStageModels:
    def test_load_stage_models(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "model: claude-opus-4-6\n"
            "stage_models:\n"
            "  diagnose: claude-sonnet-4-5-20250929\n"
            "  control_plane: claude-haiku-4-5-20251001\n"
        )
        c = load_swarm_config(cfg_file)
        assert c.default_stage_models["diagnose"] == "claude-sonnet-4-5-20250929"
        assert c.default_stage_models["control_plane"] == "claude-haiku-4-5-20251001"


class TestSwarmConfigStageBackends:
    def test_defaults_empty(self):
        c = SwarmConfig()
        assert c.default_stage_backends == {}

    def test_load_stage_backends(self, tmp_path):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "model: claude-opus-4-6\n"
            "stage_backends:\n"
            "  locate: claude_code\n"
            "  execute: claude_code\n"
        )
        c = load_swarm_config(cfg_file)
        assert c.default_stage_backends["locate"] == "claude_code"
        assert c.default_stage_backends["execute"] == "claude_code"
