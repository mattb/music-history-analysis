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
    @app.command("taste-evolution", help="Agent command: analyze taste evolution as JSON.")
    def taste_evolution(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        start_year: int = typer.Option(2005, "--start-year", help="First year to analyze."),
        end_year: int = typer.Option(2025, "--end-year", help="Last year to analyze."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("taste-evolution", session, csv, {"start_year": start_year, "end_year": end_year})

    @app.command("musical-bridges", help="Agent command: find musical bridges as JSON.")
    def musical_bridges(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name to find bridges from."),
        top_n: int = typer.Option(10, "--top-n", help="Number of similar artists per source."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("musical-bridges", session, csv, {"artist": artist, "top_n": top_n})

    @app.command("blind-spots", help="Agent command: find critically acclaimed blind spots as JSON.")
    def blind_spots(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        min_critics: int = typer.Option(3, "--min-critics", help="Minimum critics who listed the album."),
        limit: int = typer.Option(20, "--limit", help="Maximum recommendations to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("blind-spots", session, csv, {"year": year, "min_critics": min_critics, "limit": limit})

    @app.command("artist-deep-dive", help="Agent command: analyze one or more artists as JSON.")
    def artist_deep_dive(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artists: list[str] = typer.Option(..., "--artist", help="Artist name. Repeat for multiple artists."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("artist-deep-dive", session, csv, {"artists": artists})

    @app.command("similar-artists", help="Agent command: find similar artists as JSON.")
    def similar_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name to find similar artists for."),
        source: str = typer.Option("user", "--source", help="Similarity source: user or critics."),
        top_n: int = typer.Option(10, "--top-n", help="Number of similar artists to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("similar-artists", session, csv, {"artist": artist, "source": source, "top_n": top_n})

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

    @app.command("critic-alignment", help="Agent command: find aligned critics as JSON.")
    def critic_alignment(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        limit: int = typer.Option(20, "--limit", help="Number of critics to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critic-alignment", session, csv, {"limit": limit})

    @app.command("temporal-patterns", help="Agent command: analyze temporal listening patterns as JSON.")
    def temporal_patterns(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("temporal-patterns", session, csv, {"year": year})

    @app.command("period-summary", help="Agent command: summarize a listening period as JSON.")
    def period_summary(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        start_year: int = typer.Option(..., "--start-year", help="First year of the period."),
        end_year: int = typer.Option(..., "--end-year", help="Last year of the period."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("period-summary", session, csv, {"start_year": start_year, "end_year": end_year})

    @app.command("year-review", help="Agent command: generate year review data as JSON.")
    def year_review(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        years: list[int] = typer.Option([2025], "--year", help="Year to review. Repeat for multiple years."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("year-review", session, csv, {"years": years})

    @app.command("listening-by-release-era", help="Agent command: analyze listening by release era as JSON.")
    def listening_by_release_era(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        release_start: int = typer.Option(..., "--release-start", help="First release year to include."),
        release_end: int = typer.Option(..., "--release-end", help="Last release year to include."),
        limit: int = typer.Option(50, "--limit", help="Maximum albums to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command(
            "listening-by-release-era",
            session,
            csv,
            {"release_start": release_start, "release_end": release_end, "limit": limit},
        )

    @app.command("common-transitions", help="Agent command: find common artist transitions as JSON.")
    def common_transitions(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist to analyze transitions for."),
        top_n: int = typer.Option(10, "--top-n", help="Number of top transitions to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("common-transitions", session, csv, {"artist": artist, "top_n": top_n})

    @app.command("discovery-context", help="Agent command: analyze artist discovery context as JSON.")
    def discovery_context(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist to get discovery context for."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("discovery-context", session, csv, {"artist": artist})

    @app.command("critics-world", help="Agent command: explore critics world as JSON.")
    def critics_world(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critics-world", session, csv, {"year": year})

    @app.command("album-acclaim", help="Agent command: analyze an album's critical acclaim as JSON.")
    def album_acclaim(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name."),
        album: str = typer.Option(..., "--album", help="Album name."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("album-acclaim", session, csv, {"artist": artist, "album": album, "year": year})

    @app.command("validated-albums", help="Agent command: find albums validated by critics as JSON.")
    def validated_albums(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        limit: int = typer.Option(50, "--limit", help="Maximum albums to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("validated-albums", session, csv, {"year": year, "limit": limit})

    @app.command("critic-profile", help="Agent command: analyze a critic profile as JSON.")
    def critic_profile(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        critic_name: str = typer.Option(..., "--critic-name", help="Name of the critic to analyze."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critic-profile", session, csv, {"critic_name": critic_name, "year": year})

    @app.command("search-critics-artist", help="Agent command: search critics lists for an artist as JSON.")
    def search_critics_artist(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name to search for."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("search-critics-artist", session, csv, {"artist": artist, "year": year})

    @app.command("obsession-tracks", help="Agent command: find track obsessions as JSON.")
    def obsession_tracks(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        min_plays: int = typer.Option(20, "--min-plays", help="Minimum plays for a track to be considered."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("obsession-tracks", session, csv, {"year": year, "min_plays": min_plays})

    @app.command("one-track-artists", help="Agent command: find one-track artist relationships as JSON.")
    def one_track_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        min_concentration: float = typer.Option(0.7, "--min-concentration", help="Minimum share of plays on top track."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command(
            "one-track-artists",
            session,
            csv,
            {"year": year, "min_concentration": min_concentration},
        )

    @app.command("ep-single-artists", help="Agent command: find EP/single-heavy artists as JSON.")
    def ep_single_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("ep-single-artists", session, csv, {"year": year})

    @app.command("overview-summary", help="Agent command: return overview summary as JSON.")
    def overview_summary(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("overview-summary", session, csv, {})

    @app.command("discovered-artists", help="Agent command: list discovered artists as JSON.")
    def discovered_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int = typer.Option(..., "--year", help="Discovery year."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("discovered-artists", session, csv, {"year": year})

    @app.command("critics-lists", help="Agent command: list critics for a year as JSON.")
    def critics_lists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int = typer.Option(..., "--year", help="Critics list year."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critics-lists", session, csv, {"year": year})
