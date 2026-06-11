import json

from typer.testing import CliRunner

from lastfm.cli import app


runner = CliRunner()


def test_agent_command_requires_session_or_csv():
    result = runner.invoke(app, ["listening-stats", "--json"])
    assert result.exit_code == 2
    assert "Provide exactly one of --session or --csv" in result.output


def test_listening_stats_one_shot_json(monkeypatch, sample_csv):
    import lastfm.analysis_state

    monkeypatch.setattr(lastfm.analysis_state.AnalysisState, "_build_user_embeddings", lambda self: None)
    monkeypatch.setattr(lastfm.analysis_state.AnalysisState, "_build_critics_embeddings", lambda self: None)
    monkeypatch.setattr(lastfm.analysis_state.AnalysisState, "_build_critic_vectors", lambda self: None)

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
