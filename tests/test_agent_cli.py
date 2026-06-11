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
