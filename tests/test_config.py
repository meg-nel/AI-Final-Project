""".env lookup: the repo's folder first, then the folder above it."""

import os
from pathlib import Path

import pytest

from backend import agent, config

KEY = "FOSTERMATCH_TEST_SETTING"


@pytest.fixture
def folders(tmp_path, monkeypatch):
    monkeypatch.delenv(KEY, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    yield repo / ".env", tmp_path / ".env"
    os.environ.pop(KEY, None)


def test_repo_env_is_used_first(folders):
    repo_env, parent_env = folders
    repo_env.write_text(f"{KEY}=from-repo\n")
    parent_env.write_text(f"{KEY}=from-parent\n")
    assert config.load_settings((repo_env, parent_env)) == repo_env
    assert os.environ[KEY] == "from-repo"


def test_parent_env_is_the_fallback(folders):
    repo_env, parent_env = folders
    parent_env.write_text(f"{KEY}=from-parent\n")
    assert config.load_settings((repo_env, parent_env)) == parent_env
    assert os.environ[KEY] == "from-parent"


def test_no_env_file_is_fine(folders):
    assert config.load_settings(folders) is None


def test_real_environment_beats_the_file(folders, monkeypatch):
    repo_env, parent_env = folders
    repo_env.write_text(f"{KEY}=from-file\n")
    monkeypatch.setenv(KEY, "from-shell")
    config.load_settings((repo_env, parent_env))
    assert os.environ[KEY] == "from-shell"


def test_default_lookup_is_repo_then_parent():
    repo = Path(config.__file__).resolve().parent.parent
    assert config.ENV_FILES == (repo / ".env", repo.parent / ".env")


def test_missing_key_gives_a_clear_error(monkeypatch):
    monkeypatch.delenv("PORTKEY_API_KEY", raising=False)
    agent.make_model.cache_clear()
    with pytest.raises(agent.AINotConfigured, match=".env.example"):
        agent.make_model()
    agent.make_model.cache_clear()


def test_env_example_lists_every_setting_without_values():
    repo = Path(config.__file__).resolve().parent.parent
    example = (repo / ".env.example").read_text()
    assert "PORTKEY_API_KEY=\n" in example  # placeholder only
    code = "".join(p.read_text(encoding="utf-8") for p in (repo / "backend").glob("*.py"))
    import re
    for name in set(re.findall(r'getenv\("([A-Z_]+)"', code)):
        assert f"{name}=" in example, f"{name} is read by the app but missing from .env.example"


def test_real_env_files_are_ignored_by_git():
    repo = Path(config.__file__).resolve().parent.parent
    ignore = (repo / ".gitignore").read_text().splitlines()
    assert ".env" in ignore and "!.env.example" in ignore
