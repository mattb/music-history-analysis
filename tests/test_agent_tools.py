from lastfm.agent_tools import get_listening_stats, get_top_artists
from lastfm.analysis_state import AnalysisState


def loaded_lightweight_state(monkeypatch, sample_csv):
    state = AnalysisState()
    monkeypatch.setattr(state, "_build_user_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critics_embeddings", lambda: None)
    monkeypatch.setattr(state, "_build_critic_vectors", lambda: None)
    state.load(sample_csv)
    return state


def test_get_listening_stats_all_time(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    result = get_listening_stats(state)
    assert result["period"] == "all time"
    assert result["total_plays"] == 4
    assert result["unique_artists"] == 3


def test_get_top_artists(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    assert get_top_artists(state, limit=2) == [
        {"artist": "Artist A", "plays": 2},
        {"artist": "Artist B", "plays": 1},
    ]
