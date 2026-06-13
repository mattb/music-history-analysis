import json
import shutil
import tempfile
import threading

import pytest

from typer.testing import CliRunner

from lastfm.cli import app


runner = CliRunner()


def test_agent_command_requires_session_or_csv():
    result = runner.invoke(app, ["listening-stats", "--json"])
    assert result.exit_code == 2
    assert "Provide exactly one of --session or --csv" in result.output


def test_listening_stats_one_shot_json(monkeypatch, sample_csv):
    import lastfm.analysis_state

    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_user_embeddings", lambda self: None
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState,
        "_build_critics_embeddings",
        lambda self: None,
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_critic_vectors", lambda self: None
    )

    result = runner.invoke(app, ["listening-stats", "--csv", str(sample_csv), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "listening-stats"
    assert payload["session_id"] is None
    assert payload["result"]["total_plays"] == 4


def test_root_help_mentions_agent_workflow():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Agent workflow" in result.output
    assert "session-start" in result.output
    assert "listening-stats" in result.output


def test_all_agent_commands_are_registered_in_help():
    expected = [
        "taste-evolution",
        "musical-bridges",
        "blind-spots",
        "artist-deep-dive",
        "similar-artists",
        "critic-alignment",
        "temporal-patterns",
        "period-summary",
        "year-review",
        "listening-by-release-era",
        "common-transitions",
        "discovery-context",
        "critics-world",
        "album-acclaim",
        "validated-albums",
        "critic-profile",
        "search-critics-artist",
        "obsession-tracks",
        "one-track-artists",
        "ep-single-artists",
        "overview-summary",
        "discovered-artists",
        "critics-lists",
        "listening-graph",
        "artist-trajectories",
        "artist-cohort-retention",
        "life-event-window",
    ]
    output = runner.invoke(app, ["--help"]).output
    for command in expected:
        assert command in output


def test_life_event_window_one_shot_is_json_and_does_not_build_embeddings(
    monkeypatch, sample_csv
):
    import lastfm.analysis_state

    def forbidden(*_args, **_kwargs):
        raise AssertionError("life-event-window must not build embeddings")

    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_user_embeddings", forbidden
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_critics_embeddings", forbidden
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_critic_vectors", forbidden
    )
    result = runner.invoke(
        app,
        [
            "life-event-window",
            "--csv",
            str(sample_csv),
            "--event-date",
            "2024-01-02",
            "--pre-days",
            "1",
            "--event-days",
            "1",
            "--post-days",
            "1",
            "--baseline-days",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["command"] == "life-event-window"
    assert payload["result"]["periods"]["event"]["plays"] == 1
    assert "NaN" not in result.output


def test_life_event_window_session_forwards_all_parameters(monkeypatch):
    captured = {}

    def fake_dispatch(session, command, params):
        captured.update(session=session, command=command, params=params)
        return {"schema_version": 1}

    monkeypatch.setattr("lastfm.commands_agent.dispatch_to_session", fake_dispatch)
    result = runner.invoke(
        app,
        [
            "life-event-window",
            "--session",
            "diary",
            "--event-date",
            "2024-01-02",
            "--timezone",
            "Europe/London",
            "--entity",
            "album",
            "--top-n",
            "7",
            "--json",
        ],
    )
    assert result.exit_code == 0
    assert captured["session"] == "diary"
    assert captured["command"] == "life-event-window"
    assert captured["params"]["timezone"] == "Europe/London"
    assert captured["params"]["entity"] == "album"
    assert captured["params"]["top_n"] == 7


def test_life_event_window_real_session_socket_matches_one_shot(
    monkeypatch, sample_csv
):
    from lastfm.analysis_state import AnalysisState
    from lastfm.data import load_scrobbles
    from lastfm.session_client import session_paths
    from lastfm.session_daemon import AgentRequestHandler, UnixAgentServer

    session_root = tempfile.mkdtemp(prefix="lastfm-event-", dir="/tmp")
    monkeypatch.setenv("LASTFM_SESSION_ROOT", session_root)
    paths = session_paths("event-parity")
    paths.root.mkdir(parents=True)
    state = AnalysisState()
    state.csv_path = sample_csv
    state.df = load_scrobbles(sample_csv)
    server = UnixAgentServer(
        str(paths.socket), AgentRequestHandler, state, "event-parity"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    options = [
        "--event-date",
        "2024-01-02",
        "--timezone",
        "UTC",
        "--pre-days",
        "1",
        "--event-days",
        "1",
        "--post-days",
        "1",
        "--baseline-days",
        "1",
        "--entity",
        "artist",
        "--top-n",
        "10",
        "--json",
    ]
    try:
        one_shot = runner.invoke(
            app, ["life-event-window", "--csv", str(sample_csv), *options]
        )
        session = runner.invoke(
            app, ["life-event-window", "--session", "event-parity", *options]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        shutil.rmtree(session_root)

    assert one_shot.exit_code == 0, one_shot.output
    assert session.exit_code == 0, session.output
    assert json.loads(session.output)["result"] == json.loads(one_shot.output)["result"]


def test_listening_stats_help_documents_output_contract():
    result = runner.invoke(app, ["listening-stats", "--help"])
    assert result.exit_code == 0
    assert "Output contract" in result.output
    assert "--session" in result.output
    assert "--csv" in result.output


def test_session_start_help_documents_lifecycle():
    result = runner.invoke(app, ["session-start", "--help"])
    assert result.exit_code == 0
    assert "NDJSON lifecycle events" in result.output
    assert "ready" in result.output


def test_listening_graph_one_shot_json(monkeypatch, sample_csv):
    import lastfm.analysis_state

    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_user_embeddings", lambda self: None
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState,
        "_build_critics_embeddings",
        lambda self: None,
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_critic_vectors", lambda self: None
    )
    result = runner.invoke(
        app,
        [
            "listening-graph",
            "--csv",
            str(sample_csv),
            "--min-artist-plays",
            "1",
            "--min-shared-sessions",
            "1",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["command"] == "listening-graph"
    assert payload["result"]["parameters"]["min_artist_plays"] == 1
    assert json.dumps(payload, allow_nan=False)


def test_listening_graph_cli_validates_parameters(sample_csv):
    invalid = runner.invoke(
        app,
        [
            "listening-graph",
            "--csv",
            str(sample_csv),
            "--gap-minutes",
            "0",
        ],
    )
    assert invalid.exit_code == 2
    assert "positive" in invalid.output
    years = runner.invoke(
        app,
        [
            "listening-graph",
            "--csv",
            str(sample_csv),
            "--start-year",
            "2025",
            "--end-year",
            "2024",
        ],
    )
    assert years.exit_code == 2
    assert "start-year" in years.output


def test_listening_graph_cli_rejects_invalid_format_and_both_targets(sample_csv):
    invalid_format = runner.invoke(
        app, ["listening-graph", "--csv", str(sample_csv), "--format", "gexf"]
    )
    assert invalid_format.exit_code == 2
    assert "json or graphml" in invalid_format.output
    both = runner.invoke(
        app,
        ["listening-graph", "--csv", str(sample_csv), "--session", "live"],
    )
    assert both.exit_code == 2
    assert "exactly one" in both.output


@pytest.mark.parametrize(
    "option",
    [
        "--gap-minutes",
        "--min-artist-plays",
        "--min-shared-sessions",
        "--community-resolution",
        "--betweenness-samples",
        "--hops",
    ],
)
def test_listening_graph_cli_rejects_nonpositive_options(sample_csv, option):
    result = runner.invoke(
        app, ["listening-graph", "--csv", str(sample_csv), option, "0"]
    )
    assert result.exit_code == 2
    assert "positive" in result.output


@pytest.mark.parametrize("resolution", ["nan", "inf", "-inf"])
def test_listening_graph_cli_rejects_nonfinite_resolution(sample_csv, resolution):
    result = runner.invoke(
        app,
        [
            "listening-graph",
            "--csv",
            str(sample_csv),
            "--community-resolution",
            resolution,
        ],
    )
    assert result.exit_code == 2
    assert "finite and positive" in result.output


def test_listening_graph_cli_forwards_every_option(monkeypatch):
    import lastfm.commands_agent

    captured = {}

    def fake_run(command, session, csv, params):
        captured.update(command=command, session=session, csv=csv, params=params)

    monkeypatch.setattr(lastfm.commands_agent, "_run_agent_command", fake_run)
    result = runner.invoke(
        app,
        [
            "listening-graph",
            "--session",
            "live",
            "--gap-minutes",
            "45",
            "--min-artist-plays",
            "2",
            "--min-shared-sessions",
            "3",
            "--start-year",
            "2020",
            "--end-year",
            "2024",
            "--community-resolution",
            "1.5",
            "--community-seed",
            "7",
            "--betweenness-samples",
            "12",
            "--artist",
            "Artist A",
            "--hops",
            "2",
            "--format",
            "graphml",
        ],
    )
    assert result.exit_code == 0
    assert captured == {
        "command": "listening-graph",
        "session": "live",
        "csv": None,
        "params": {
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
    }


def test_listening_graph_help_lists_graph_options():
    output = runner.invoke(app, ["listening-graph", "--help"]).output
    for option in [
        "--gap-minutes",
        "--min-artist-plays",
        "--min-shared-sessions",
        "--community-resolution",
        "--community-seed",
        "--betweenness-samples",
        "--artist",
        "--hops",
        "--format",
    ]:
        assert option in output


def test_artist_trajectories_cli_preserves_artists_and_forwards_options(monkeypatch):
    import lastfm.commands_agent

    captured = {}
    monkeypatch.setattr(
        lastfm.commands_agent,
        "_run_agent_command",
        lambda command, session, csv, params: captured.update(
            command=command, session=session, csv=csv, params=params
        ),
    )
    result = runner.invoke(
        app,
        [
            "artist-trajectories",
            "--session",
            "live",
            "--artist",
            "B",
            "--artist",
            "A",
            "--granularity",
            "year",
            "--start",
            "2020",
            "--end",
            "2024",
            "--min-period-plays",
            "2",
            "--dormancy-periods",
            "3",
        ],
    )
    assert result.exit_code == 0
    assert captured["command"] == "artist-trajectories"
    assert captured["params"] == {
        "artists": ["B", "A"],
        "granularity": "year",
        "start": "2020",
        "end": "2024",
        "min_period_plays": 2,
        "dormancy_periods": 3,
    }


def test_artist_cohort_cli_sorts_unique_offsets_and_forwards_options(monkeypatch):
    import lastfm.commands_agent

    captured = {}
    monkeypatch.setattr(
        lastfm.commands_agent,
        "_run_agent_command",
        lambda command, session, csv, params: captured.update(
            command=command, params=params
        ),
    )
    result = runner.invoke(
        app,
        [
            "artist-cohort-retention",
            "--session",
            "live",
            "--cohort-granularity",
            "year",
            "--activity-granularity",
            "month",
            "--start",
            "2020",
            "--end",
            "2024",
            "--min-discovery-plays",
            "2",
            "--min-active-plays",
            "3",
            "--offset",
            "6",
            "--offset",
            "1",
            "--offset",
            "6",
        ],
    )
    assert result.exit_code == 0
    assert captured == {
        "command": "artist-cohort-retention",
        "params": {
            "cohort_granularity": "year",
            "activity_granularity": "month",
            "start": "2020",
            "end": "2024",
            "min_discovery_plays": 2,
            "min_active_plays": 3,
            "offsets": [1, 6],
        },
    }


@pytest.mark.parametrize(
    "command_and_options",
    [
        [
            "artist-trajectories",
            "--session",
            "live",
            "--artist",
            "A",
            "--granularity",
            "week",
        ],
        [
            "artist-trajectories",
            "--session",
            "live",
            "--artist",
            "A",
            "--min-period-plays",
            "0",
        ],
        ["artist-cohort-retention", "--session", "live", "--offset", "-1"],
        [
            "artist-cohort-retention",
            "--session",
            "live",
            "--start",
            "2024-02",
            "--end",
            "2024-01",
        ],
    ],
)
def test_trajectory_clis_reject_invalid_parameters(command_and_options):
    result = runner.invoke(app, command_and_options)
    assert result.exit_code == 2


def test_artist_trajectories_one_shot_json_real_dispatch(monkeypatch, sample_csv):
    import lastfm.analysis_state

    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_user_embeddings", lambda self: None
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState,
        "_build_critics_embeddings",
        lambda self: None,
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_critic_vectors", lambda self: None
    )
    result = runner.invoke(
        app,
        [
            "artist-trajectories",
            "--csv",
            str(sample_csv),
            "--artist",
            "Artist C",
            "--artist",
            "Artist A",
            "--start",
            "2024-01",
            "--end",
            "2025-01",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "artist-trajectories"
    assert [item["query_artist"] for item in payload["result"]["artists"]] == [
        "Artist C",
        "Artist A",
    ]
    assert json.dumps(payload, allow_nan=False)


def test_artist_cohort_retention_one_shot_json_real_dispatch(monkeypatch, sample_csv):
    import lastfm.analysis_state

    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_user_embeddings", lambda self: None
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState,
        "_build_critics_embeddings",
        lambda self: None,
    )
    monkeypatch.setattr(
        lastfm.analysis_state.AnalysisState, "_build_critic_vectors", lambda self: None
    )
    result = runner.invoke(
        app,
        [
            "artist-cohort-retention",
            "--csv",
            str(sample_csv),
            "--offset",
            "12",
            "--offset",
            "1",
            "--offset",
            "12",
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "artist-cohort-retention"
    assert payload["result"]["parameters"]["offsets"] == [1, 12]
    assert json.dumps(payload, allow_nan=False)
