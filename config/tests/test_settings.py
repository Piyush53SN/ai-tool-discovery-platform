"""Tests for the dependency-free .env loader in config/settings.py."""
import os

from config.settings import _load_env_file


def test_load_env_file_sets_missing_and_respects_existing(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment line\n"
        "AI_TOOLS_TEST_VAR=hello\n"
        'AI_TOOLS_TEST_QUOTED="quoted value"\n'
        "AI_TOOLS_TEST_SINGLE='single'\n"
        "AI_TOOLS_TEST_EMPTY=\n"
        "DATABASE_URL=from-file\n"
    )
    monkeypatch.delenv("AI_TOOLS_TEST_VAR", raising=False)
    monkeypatch.setenv("DATABASE_URL", "from-environment")

    _load_env_file(env)

    assert os.environ["AI_TOOLS_TEST_VAR"] == "hello"
    assert os.environ["AI_TOOLS_TEST_QUOTED"] == "quoted value"
    assert os.environ["AI_TOOLS_TEST_SINGLE"] == "single"
    assert os.environ["AI_TOOLS_TEST_EMPTY"] == ""
    # A real exported variable always wins over the file (setdefault).
    assert os.environ["DATABASE_URL"] == "from-environment"


def test_load_env_file_missing_file_is_noop(tmp_path):
    _load_env_file(tmp_path / "does-not-exist.env")  # must not raise
