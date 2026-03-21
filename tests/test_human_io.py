"""Tests for HumanIO — interactive, file, and autonomous modes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from swarm.human_io import AutonomousIO, FileIO, InteractiveIO, create_io
from swarm.schemas import Question


class TestAutonomousIO:
    def test_ask_returns_first_option(self):
        io = AutonomousIO()
        answer = io.ask("diagnose", "Which framework?", options=["Django", "Flask"])
        assert answer == "Django"

    def test_ask_returns_empty_without_options(self):
        io = AutonomousIO()
        answer = io.ask("diagnose", "What next?")
        assert answer == ""

    def test_confirm_always_true(self):
        io = AutonomousIO()
        assert io.confirm_break_point("review", "Break point") is True

    def test_notify_does_not_raise(self):
        io = AutonomousIO()
        io.notify("diagnose", "Starting")  # Should not raise


class TestInteractiveIO:
    @patch("builtins.input", return_value="Django")
    def test_ask_plain(self, mock_input):
        io = InteractiveIO()
        answer = io.ask("diagnose", "What framework?")
        assert answer == "Django"

    @patch("builtins.input", return_value="1")
    def test_ask_with_options_by_number(self, mock_input):
        io = InteractiveIO()
        answer = io.ask("diagnose", "Which?", options=["Django", "Flask"])
        assert answer == "Django"

    @patch("builtins.input", return_value="Flask")
    def test_ask_with_options_by_text(self, mock_input):
        io = InteractiveIO()
        answer = io.ask("diagnose", "Which?", options=["Django", "Flask"])
        assert answer == "Flask"

    @patch("builtins.input", return_value="y")
    def test_confirm_yes(self, mock_input):
        io = InteractiveIO()
        assert io.confirm_break_point("review", "Check") is True

    @patch("builtins.input", return_value="n")
    def test_confirm_no(self, mock_input):
        io = InteractiveIO()
        assert io.confirm_break_point("review", "Check") is False

    @patch("builtins.input", return_value="")
    def test_confirm_empty_is_yes(self, mock_input):
        io = InteractiveIO()
        assert io.confirm_break_point("review", "Check") is True


class TestFileIO:
    def test_ask_writes_question(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        io = FileIO(proj, poll_interval=0.1, timeout=0.5)

        # Pre-answer the question
        import threading

        def answer_after_delay():
            import time
            time.sleep(0.2)
            # Find the pending question
            pending = list((proj / "questions" / "pending").glob("*.json"))
            if pending:
                q_data = json.loads(pending[0].read_text())
                answer_path = proj / "questions" / "answered" / pending[0].name
                answer_path.write_text(json.dumps({"answer": "Django"}))

        t = threading.Thread(target=answer_after_delay)
        t.start()

        answer = io.ask("diagnose", "What framework?", options=["Django", "Flask"])
        t.join()
        assert answer == "Django"

    def test_ask_timeout(self, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        io = FileIO(proj, poll_interval=0.05, timeout=0.1)

        with pytest.raises(TimeoutError):
            io.ask("diagnose", "What framework?")


class TestCreateIO:
    def test_interactive(self):
        io = create_io("interactive")
        assert isinstance(io, InteractiveIO)

    def test_file(self, tmp_path):
        io = create_io("file", tmp_path)
        assert isinstance(io, FileIO)

    def test_file_without_dir_raises(self):
        with pytest.raises(ValueError, match="project_dir"):
            create_io("file")

    def test_autonomous(self):
        io = create_io("autonomous")
        assert isinstance(io, AutonomousIO)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown IO mode"):
            create_io("smoke_signal")
