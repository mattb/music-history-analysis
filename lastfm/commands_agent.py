"""Top-level agent-facing CLI commands."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import typer

from . import agent_tools, analysis_state
from .agent_output import error_envelope, print_json, success_envelope


def _resolve_target(session: str | None, csv: Path | None) -> tuple[str | None, analysis_state.AnalysisState]:
    if bool(session) == bool(csv):
        raise typer.BadParameter("Provide exactly one of --session or --csv")
    if session:
        raise RuntimeError("session dispatch is added in the daemon task")

    state = analysis_state.AnalysisState()
    state.load(csv)
    return None, state


def _run_agent_command(command: str, session: str | None, csv: Path | None, params: dict[str, Any]) -> None:
    try:
        with redirect_stdout(StringIO()):
            session_id, state = _resolve_target(session, csv)
            result = agent_tools.dispatch(state, command, params)
        print_json(success_envelope(command=command, result=result, session_id=session_id))
    except typer.BadParameter:
        raise
    except Exception as exc:
        print_json(error_envelope(
            command=command,
            code=type(exc).__name__.upper(),
            message=str(exc),
            retryable=False,
            session_id=session,
        ))
        raise typer.Exit(1)


def register(app: typer.Typer) -> None:
    @app.command("listening-stats", help="Agent command: return listening statistics as JSON.")
    def listening_stats(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("listening-stats", session, csv, {"year": year})

    @app.command("top-artists", help="Agent command: return top artists as JSON.")
    def top_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        limit: int = typer.Option(20, "--limit", help="Maximum artists to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("top-artists", session, csv, {"year": year, "limit": limit})
