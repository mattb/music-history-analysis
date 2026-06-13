import json

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
    ]
    output = runner.invoke(app, ["--help"]).output
    for command in expected:
        assert command in output


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
