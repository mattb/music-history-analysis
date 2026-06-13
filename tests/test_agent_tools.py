from lastfm.agent_tools import (
    dispatch,
    get_life_event_window,
    get_artist_cohort_retention,
    get_artist_trajectories,
    get_listening_stats,
    get_top_artists,
)
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


def test_dispatch_listening_graph_forwards_every_option(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    captured = {}

    def fake_analyze(df, config, **kwargs):
        captured.update(df=df, config=config, kwargs=kwargs)
        return {"graph_type": "artist_session_cooccurrence"}

    monkeypatch.setattr("lastfm.listening_graph.analyze_listening_graph", fake_analyze)
    result = dispatch(
        state,
        "listening-graph",
        {
            "gap_minutes": 45,
            "min_artist_plays": 2,
            "min_shared_sessions": 3,
            "start_year": 2020,
            "end_year": 2024,
            "community_resolution": 1.5,
            "community_seed": 7,
            "betweenness_samples": 12,
            "focus_artist": "Artist A",
            "hops": 2,
            "output_format": "graphml",
        },
    )
    assert result["graph_type"] == "artist_session_cooccurrence"
    assert captured["df"] is state.df
    assert captured["config"].__dict__ == {
        "gap_minutes": 45,
        "min_artist_plays": 2,
        "min_shared_sessions": 3,
        "start_year": 2020,
        "end_year": 2024,
        "community_resolution": 1.5,
        "community_seed": 7,
        "betweenness_samples": 12,
    }
    assert captured["kwargs"] == {
        "focus_artist": "Artist A",
        "hops": 2,
        "output_format": "graphml",
    }


def test_trajectory_agent_tools_are_thin_dataframe_wrappers(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    trajectories = get_artist_trajectories(
        state, ["Artist A", "missing"], start="2024-01", end="2024-02"
    )
    assert [item["status"] for item in trajectories["artists"]] == ["ok", "not_found"]
    retention = get_artist_cohort_retention(state, offsets=[0, 1])
    assert retention["parameters"]["offsets"] == [0, 1]
    assert (
        dispatch(state, "artist-trajectories", {"artists": ["Artist A"]})["count"] == 1
    )


def test_life_event_window_wrapper_and_dispatch(monkeypatch, sample_csv):
    state = loaded_lightweight_state(monkeypatch, sample_csv)
    result = get_life_event_window(
        state,
        event_date="2024-01-02",
        pre_days=1,
        event_days=1,
        post_days=1,
        baseline_days=1,
    )
    assert result["event_date"] == "2024-01-02"
    assert (
        dispatch(
            state,
            "life-event-window",
            {
                "event_date": "2024-01-02",
                "pre_days": 1,
                "event_days": 1,
                "post_days": 1,
                "baseline_days": 1,
            },
        )["schema_version"]
        == 1
    )
