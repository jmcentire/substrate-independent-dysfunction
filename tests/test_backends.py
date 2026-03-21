"""Tests for backend abstraction — protocol, factory, model switching."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from swarm.backends import Backend, create_backend
from swarm.budget import BudgetTracker


class TestBackendProtocol:
    """Verify the Backend protocol contract."""

    def test_protocol_requires_assess(self):
        """Backend must have assess method."""
        assert hasattr(Backend, "assess")

    def test_protocol_requires_set_model(self):
        """Backend must have set_model method."""
        assert hasattr(Backend, "set_model")

    def test_protocol_requires_close(self):
        """Backend must have close method."""
        assert hasattr(Backend, "close")


class TestCreateBackend:
    """Test the backend factory function."""

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_create_anthropic_backend(self):
        budget = BudgetTracker()
        backend = create_backend("anthropic", budget, "claude-opus-4-6")
        from swarm.backends.anthropic import AnthropicBackend
        assert isinstance(backend, AnthropicBackend)

    def test_create_claude_code_backend(self):
        budget = BudgetTracker()
        backend = create_backend("claude_code", budget, "claude-opus-4-6")
        from swarm.backends.claude_code import ClaudeCodeBackend
        assert isinstance(backend, ClaudeCodeBackend)

    def test_unknown_backend_raises(self):
        budget = BudgetTracker()
        with pytest.raises(ValueError, match="Unknown backend"):
            create_backend("nonexistent", budget, "claude-opus-4-6")


class TestAnthropicBackend:
    """Test AnthropicBackend behavior."""

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_default_model(self):
        from swarm.backends.anthropic import AnthropicBackend
        budget = BudgetTracker()
        backend = AnthropicBackend(budget=budget)
        assert backend._model == "claude-opus-4-6"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_custom_model(self):
        from swarm.backends.anthropic import AnthropicBackend
        budget = BudgetTracker()
        backend = AnthropicBackend(budget=budget, model="claude-sonnet-4-5-20250929")
        assert backend._model == "claude-sonnet-4-5-20250929"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_set_model(self):
        from swarm.backends.anthropic import AnthropicBackend
        budget = BudgetTracker()
        backend = AnthropicBackend(budget=budget, model="claude-opus-4-6")
        backend.set_model("claude-haiku-4-5-20251001")
        assert backend._model == "claude-haiku-4-5-20251001"

    def test_missing_api_key_raises(self):
        from swarm.backends.anthropic import AnthropicBackend
        budget = BudgetTracker()
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
                AnthropicBackend(budget=budget)


class TestClaudeCodeBackend:
    """Test ClaudeCodeBackend behavior."""

    def test_default_model(self):
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        backend = ClaudeCodeBackend(budget=budget)
        assert backend._model == "claude-opus-4-6"

    def test_set_model(self):
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        backend = ClaudeCodeBackend(budget=budget)
        backend.set_model("claude-sonnet-4-5-20250929")
        assert backend._model == "claude-sonnet-4-5-20250929"

    def test_default_no_repo_path(self):
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        backend = ClaudeCodeBackend(budget=budget)
        assert backend._repo_path is None

    def test_set_repo_path(self, tmp_path):
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        backend = ClaudeCodeBackend(budget=budget)
        backend.set_repo_path(tmp_path)
        assert backend._repo_path == tmp_path

    def test_repo_path_in_constructor(self, tmp_path):
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        backend = ClaudeCodeBackend(budget=budget, repo_path=tmp_path)
        assert backend._repo_path == tmp_path

    def test_extract_json_direct(self):
        from swarm.backends.claude_code import ClaudeCodeBackend
        data = ClaudeCodeBackend._extract_json('{"key": "value"}')
        assert data == {"key": "value"}

    def test_extract_json_from_code_fence(self):
        from swarm.backends.claude_code import ClaudeCodeBackend
        text = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        data = ClaudeCodeBackend._extract_json(text)
        assert data == {"key": "value"}

    def test_extract_json_from_braces(self):
        from swarm.backends.claude_code import ClaudeCodeBackend
        text = 'Here is the result: {"key": "value"} done.'
        data = ClaudeCodeBackend._extract_json(text)
        assert data == {"key": "value"}

    def test_extract_json_no_json_raises(self):
        from swarm.backends.claude_code import ClaudeCodeBackend
        with pytest.raises(RuntimeError, match="no valid JSON"):
            ClaudeCodeBackend._extract_json("no json here at all")


class TestAgentBaseBackendIntegration:
    """Test that AgentBase properly delegates to backends."""

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_default_backend_is_anthropic(self):
        from swarm.agents.base import AgentBase
        from swarm.backends.anthropic import AnthropicBackend
        budget = BudgetTracker()
        agent = AgentBase(budget=budget)
        assert isinstance(agent._backend, AnthropicBackend)

    def test_claude_code_backend(self):
        from swarm.agents.base import AgentBase
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        agent = AgentBase(budget=budget, backend="claude_code")
        assert isinstance(agent._backend, ClaudeCodeBackend)

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_set_model_propagates(self):
        from swarm.agents.base import AgentBase
        budget = BudgetTracker()
        agent = AgentBase(budget=budget)
        agent.set_model("claude-haiku-4-5-20251001")
        assert agent._model == "claude-haiku-4-5-20251001"
        assert agent._backend._model == "claude-haiku-4-5-20251001"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_set_backend_switches(self):
        from swarm.agents.base import AgentBase
        from swarm.backends.anthropic import AnthropicBackend
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        agent = AgentBase(budget=budget)
        assert isinstance(agent._backend, AnthropicBackend)
        agent.set_backend("claude_code")
        assert isinstance(agent._backend, ClaudeCodeBackend)

    def test_set_backend_preserves_model(self):
        from swarm.agents.base import AgentBase
        from swarm.backends.claude_code import ClaudeCodeBackend
        budget = BudgetTracker()
        agent = AgentBase(budget=budget, backend="claude_code")
        agent.set_model("claude-sonnet-4-5-20250929")
        agent.set_backend("claude_code")  # re-create backend
        assert isinstance(agent._backend, ClaudeCodeBackend)
        assert agent._backend._model == "claude-sonnet-4-5-20250929"


class TestAnthropicBackendTokenCap:
    """Test model-aware token cap and field coercion."""

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_sonnet_cap(self):
        from swarm.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend(BudgetTracker(), model="claude-sonnet-4-5-20250929")
        assert backend._max_tokens_cap() == 64000

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_opus_cap(self):
        from swarm.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend(BudgetTracker(), model="claude-opus-4-6")
        assert backend._max_tokens_cap() == 32768

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_unknown_model_uses_default(self):
        from swarm.backends.anthropic import AnthropicBackend
        backend = AnthropicBackend(BudgetTracker(), model="claude-future-9000")
        assert backend._max_tokens_cap() == 32768

    def test_coerce_stringified_list(self):
        from swarm.backends.anthropic import AnthropicBackend
        data = {"issues": '["bug 1", "bug 2"]', "name": "test"}
        result = AnthropicBackend._coerce_fields(data)
        assert result["issues"] == ["bug 1", "bug 2"]
        assert result["name"] == "test"

    def test_coerce_stringified_dict(self):
        from swarm.backends.anthropic import AnthropicBackend
        data = {"meta": '{"key": "value"}', "count": 5}
        result = AnthropicBackend._coerce_fields(data)
        assert result["meta"] == {"key": "value"}
        assert result["count"] == 5

    def test_coerce_leaves_normal_strings(self):
        from swarm.backends.anthropic import AnthropicBackend
        data = {"name": "hello", "desc": "starts with [bracket"}
        result = AnthropicBackend._coerce_fields(data)
        assert result["name"] == "hello"
        # Invalid JSON starting with [ — left as string
        assert result["desc"] == "starts with [bracket"

    def test_coerce_non_dict_passthrough(self):
        from swarm.backends.anthropic import AnthropicBackend
        assert AnthropicBackend._coerce_fields("not a dict") == "not a dict"
