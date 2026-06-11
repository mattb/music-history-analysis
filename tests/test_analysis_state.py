from pathlib import Path

import pytest

from lastfm.analysis_state import AnalysisState, find_csv


def test_find_csv_prefers_explicit_env(monkeypatch, sample_csv):
    monkeypatch.setenv("LASTFM_CSV", str(sample_csv))
    assert find_csv(Path.cwd()) == sample_csv


def test_find_csv_uses_newest_recenttracks(monkeypatch, tmp_path):
    monkeypatch.delenv("LASTFM_CSV", raising=False)
    older = tmp_path / "recenttracks-user-1.csv"
    newer = tmp_path / "recenttracks-user-2.csv"
    older.write_text("x\n")
    newer.write_text("x\n")
    assert find_csv(tmp_path) == newer


def test_state_metadata_after_lightweight_load(monkeypatch, sample_csv):
    state = AnalysisState()
    monkeypatch.setattr(state, "_build_user_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critics_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critic_vectors", lambda: None)
    state.load(sample_csv)
    assert state.metadata()["csv_path"] == str(sample_csv)
    assert state.metadata()["plays"] == 4
    assert state.metadata()["artists"] == 3
