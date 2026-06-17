import json
import os
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from lastfm.analysis_state import find_csv
from lastfm.cli import app
from lastfm.session_client import session_paths, session_root


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_exposes_only_music_history_script():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert data["project"]["name"] == "music-history-analysis"
    assert data["project"]["scripts"] == {"music-history": "lastfm.cli:app"}


def test_plugin_manifest_points_at_repo_skills():
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "music-history"
    assert manifest["skills"] == "./skills/"
    assert "mcpServers" not in manifest
    assert "apps" not in manifest


def test_readme_targets_plugin_installers():
    readme = (ROOT / "README.md").read_text()

    assert "Codex plugin" in readme
    assert "$music-history-cli-journalism" in readme
    assert "music-history --help" in readme
    assert "music-history stats" in readme
    assert "lastfm --help" not in readme


def test_cli_help_uses_music_history_command_name():
    result = CliRunner().invoke(app, ["--help"], prog_name="music-history")

    assert result.exit_code == 0
    assert "music-history session-start" in result.output
    assert "lastfm session-start" not in result.output


def test_find_csv_uses_music_history_env_and_ignores_lastfm_env(
    monkeypatch, tmp_path
):
    music_csv = tmp_path / "recenttracks-music.csv"
    old_csv = tmp_path / "recenttracks-old.csv"
    music_csv.write_text("x\n")
    old_csv.write_text("x\n")

    monkeypatch.setenv("MUSIC_HISTORY_CSV", str(music_csv))
    monkeypatch.setenv("LASTFM_CSV", str(old_csv))

    assert find_csv(tmp_path) == music_csv


def test_session_paths_use_music_history_names(monkeypatch, tmp_path):
    monkeypatch.setenv("MUSIC_HISTORY_SESSION_ROOT", str(tmp_path))
    monkeypatch.setenv("LASTFM_SESSION_ROOT", str(tmp_path / "old"))

    paths = session_paths("music-2025")

    assert session_root() == tmp_path
    assert paths.socket == tmp_path / "music-2025" / "music-history.sock"
    assert paths.restart_lock == tmp_path / ".locks" / "music-2025.lock"


def test_default_session_root_uses_music_history_cache(monkeypatch):
    monkeypatch.delenv("MUSIC_HISTORY_SESSION_ROOT", raising=False)
    monkeypatch.delenv("LASTFM_SESSION_ROOT", raising=False)

    root = session_root()

    assert root == Path.home() / ".cache" / "music-history-analysis" / "sessions"


def test_runtime_cache_strings_use_music_history_namespace():
    files = [
        ROOT / "lastfm" / "musicbrainz_db.py",
        ROOT / "lastfm" / "release_years.py",
        ROOT / "lastfm" / "spotify.py",
        ROOT / "lastfm" / "lastfm_api.py",
        ROOT / "lastfm" / "evaluation.py",
        ROOT / "lastfm" / "embeddings.py",
        ROOT / "lastfm" / "cli.py",
    ]

    combined = "\n".join(path.read_text() for path in files)

    assert "music-history-analysis" in combined
    assert "lastfm-analysis" not in combined
    assert "LASTFM_" not in combined
    assert "lastfm.sock" not in combined
    assert "Run 'lastfm " not in combined
    assert "Run: [cyan]lastfm " not in combined


def test_lastfm_console_script_is_not_installed():
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "scripts"
    ]

    assert "lastfm" not in scripts
