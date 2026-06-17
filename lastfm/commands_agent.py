"""Top-level agent-facing CLI commands."""

from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any

import typer

from . import agent_tools, analysis_state, data
from .agent_output import error_envelope, print_json, success_envelope
from .session_client import (
    dispatch_to_session,
    list_sessions,
    read_session_status,
    remove_session_files,
    start_session,
    stop_session,
)


AGENT_ANALYSIS_HELP_SUFFIX = (
    "Prerequisites: use either --session for a running daemon or --csv for one-shot mode.\n\n"
    "Expired named sessions restart automatically from persisted metadata.\n\n"
    "Output contract: --json writes a single JSON envelope to stdout; diagnostics go to stderr.\n\n"
    "Failure behavior: non-zero exit with ok=false JSON envelope for runtime failures."
)

SESSION_START_HELP = (
    "Start a named daemon session.\n\n"
    "Workflow: loads CSV, builds cached embeddings, writes metadata, then listens on a Unix socket.\n\n"
    "Output contract: --json writes NDJSON lifecycle events including start, load_csv, and ready.\n\n"
    "Failure behavior: startup failures exit non-zero before the ready event."
)


def _agent_help(first_sentence: str) -> str:
    return f"{first_sentence}\n\n{AGENT_ANALYSIS_HELP_SUFFIX}"


# fmt: off
def _resolve_target(
    session: str | None,
    csv: Path | None,
    *,
    lightweight: bool = False,
) -> tuple[str | None, analysis_state.AnalysisState | None]:
    if bool(session) == bool(csv):
        raise typer.BadParameter("Provide exactly one of --session or --csv")
    if session:
        return session, None

    state = analysis_state.AnalysisState()
    if lightweight:
        state.csv_path = csv
        state.df = data.load_scrobbles(csv)
    else:
        state.load(csv)
    return None, state


def _run_agent_command(command: str, session: str | None, csv: Path | None, params: dict[str, Any]) -> None:
    try:
        with redirect_stdout(StringIO()):
            session_id, state = _resolve_target(
                session,
                csv,
                lightweight=command
                in {
                    "listening-graph",
                    "artist-trajectories",
                    "artist-cohort-retention",
                    "life-event-window",
                    "listening-change-points",
                },
            )
            if session_id:
                result = dispatch_to_session(session_id, command, params)
            else:
                result = agent_tools.dispatch(state, command, params)
        print_json(success_envelope(command=command, result=result, session_id=session_id))
    except typer.BadParameter:
        raise
    except Exception as exc:
        print_json(error_envelope(
            command=command,
            code=getattr(exc, "code", type(exc).__name__.upper()),
            message=str(exc),
            retryable=getattr(exc, "retryable", False),
            session_id=session,
        ))
        raise typer.Exit(1)


# fmt: on
def register(app: typer.Typer) -> None:
    @app.command(
        "listening-change-points",
        help=_agent_help("Detect candidate changes in listening composition."),
    )
    def listening_change_points(
        session: str | None = typer.Option(
            None, "--session", help="Named daemon session ID."
        ),
        csv: Path | None = typer.Option(
            None, "--csv", help="Run one-shot against this scrobbles CSV."
        ),
        frequency: str = typer.Option(
            "month", "--frequency", help="Calendar bins: week or month."
        ),
        vector_mode: str = typer.Option(
            "shares", "--vector-mode", help="Vector representation: shares or counts."
        ),
        top_artists: int = typer.Option(
            100, "--top-artists", help="Artist dimensions retained before __OTHER__."
        ),
        min_segment_bins: int = typer.Option(
            6, "--min-segment-bins", help="Minimum bins in every segment."
        ),
        penalty_multiplier: float = typer.Option(
            1.0,
            "--penalty-multiplier",
            help="Positive multiplier for the estimated-noise penalty.",
        ),
        top_deltas: int = typer.Option(
            20, "--top-deltas", help="Largest artist-share changes to report."
        ),
        _json_output: bool = typer.Option(
            True, "--json", help="Emit structured JSON on stdout."
        ),
    ):
        _run_agent_command(
            "listening-change-points",
            session,
            csv,
            {
                "frequency": frequency,
                "vector_mode": vector_mode,
                "top_artists": top_artists,
                "min_segment_bins": min_segment_bins,
                "penalty_multiplier": penalty_multiplier,
                "top_deltas": top_deltas,
            },
        )

    @app.command(
        "life-event-window",
        help=_agent_help("Measure listening around a user-supplied life-event date."),
    )
    def life_event_window(
        session: str | None = typer.Option(
            None, "--session", help="Named daemon session ID."
        ),
        csv: Path | None = typer.Option(
            None, "--csv", help="Run one-shot against this scrobbles CSV."
        ),
        event_date: str = typer.Option(
            ..., "--event-date", help="Local event date in YYYY-MM-DD format."
        ),
        timezone: str = typer.Option(
            "UTC", "--timezone", help="IANA timezone for local-calendar windows."
        ),
        pre_days: int = typer.Option(28, "--pre-days", help="Days before the event."),
        event_days: int = typer.Option(
            1, "--event-days", help="Days in the event window."
        ),
        post_days: int = typer.Option(28, "--post-days", help="Days after the event."),
        baseline_days: int = typer.Option(
            84, "--baseline-days", help="Days in each before/after baseline."
        ),
        entity: str = typer.Option(
            "artist", "--entity", help="Entity grouping: artist, album, or track."
        ),
        top_n: int = typer.Option(
            50, "--top-n", help="Top entities retained from each comparison period."
        ),
        _json_output: bool = typer.Option(
            True, "--json", help="Emit structured JSON on stdout."
        ),
    ):
        _run_agent_command(
            "life-event-window",
            session,
            csv,
            {
                "event_date": event_date,
                "timezone": timezone,
                "pre_days": pre_days,
                "event_days": event_days,
                "post_days": post_days,
                "baseline_days": baseline_days,
                "entity": entity,
                "top_n": top_n,
            },
        )

    @app.command(
        "artist-trajectories",
        help=_agent_help("Measure artist relationship trajectories."),
    )
    def artist_trajectories(
        session: str | None = typer.Option(
            None, "--session", help="Named daemon session ID."
        ),
        csv: Path | None = typer.Option(
            None, "--csv", help="Run one-shot against this scrobbles CSV."
        ),
        artists: list[str] = typer.Option(
            ..., "--artist", help="Exact artist name. Repeat to preserve query order."
        ),
        granularity: str = typer.Option(
            "month", "--granularity", help="month or year."
        ),
        start: str | None = typer.Option(
            None, "--start", help="Inclusive first period."
        ),
        end: str | None = typer.Option(None, "--end", help="Inclusive last period."),
        min_period_plays: int = typer.Option(
            1, "--min-period-plays", help="Minimum plays for an active period."
        ),
        dormancy_periods: int = typer.Option(
            6,
            "--dormancy-periods",
            help="Consecutive inactive periods defining dormancy.",
        ),
        _json_output: bool = typer.Option(
            True, "--json", help="Emit structured JSON on stdout."
        ),
    ):
        _run_agent_command(
            "artist-trajectories",
            session,
            csv,
            {
                "artists": artists,
                "granularity": granularity,
                "start": start,
                "end": end,
                "min_period_plays": min_period_plays,
                "dormancy_periods": dormancy_periods,
            },
        )

    @app.command(
        "artist-cohort-retention",
        help=_agent_help("Measure discovery-cohort retention."),
    )
    def artist_cohort_retention(
        session: str | None = typer.Option(
            None, "--session", help="Named daemon session ID."
        ),
        csv: Path | None = typer.Option(
            None, "--csv", help="Run one-shot against this scrobbles CSV."
        ),
        cohort_granularity: str = typer.Option(
            "month", "--cohort-granularity", help="Cohort period: month or year."
        ),
        activity_granularity: str = typer.Option(
            "month", "--activity-granularity", help="Retention period: month or year."
        ),
        start: str | None = typer.Option(
            None, "--start", help="Inclusive first cohort period."
        ),
        end: str | None = typer.Option(
            None, "--end", help="Inclusive last cohort period."
        ),
        min_discovery_plays: int = typer.Option(
            1, "--min-discovery-plays", help="Minimum plays in the discovery period."
        ),
        min_active_plays: int = typer.Option(
            1, "--min-active-plays", help="Minimum plays at a retention offset."
        ),
        offsets: list[int] | None = typer.Option(
            None, "--offset", help="Nonnegative activity-period offset. Repeatable."
        ),
        _json_output: bool = typer.Option(
            True, "--json", help="Emit structured JSON on stdout."
        ),
    ):
        selected_offsets = (
            [1, 3, 6, 12, 24] if offsets is None else sorted(set(offsets))
        )
        _run_agent_command(
            "artist-cohort-retention",
            session,
            csv,
            {
                "cohort_granularity": cohort_granularity,
                "activity_granularity": activity_granularity,
                "start": start,
                "end": end,
                "min_discovery_plays": min_discovery_plays,
                "min_active_plays": min_active_plays,
                "offsets": selected_offsets,
            },
        )

    # Legacy registrations below predate Ruff formatting. Keep new commands scoped.
    # fmt: off
    @app.command("listening-graph", help=_agent_help("Measure artist co-listening sessions."))
    def listening_graph(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        gap_minutes: int = typer.Option(30, "--gap-minutes", help="Session inactivity boundary."),
        min_artist_plays: int = typer.Option(10, "--min-artist-plays", help="Minimum plays per artist."),
        min_shared_sessions: int = typer.Option(2, "--min-shared-sessions", help="Minimum shared sessions per edge."),
        start_year: int | None = typer.Option(None, "--start-year", help="Inclusive first year."),
        end_year: int | None = typer.Option(None, "--end-year", help="Inclusive last year."),
        community_resolution: float = typer.Option(1.0, "--community-resolution", help="Louvain resolution."),
        community_seed: int = typer.Option(0, "--community-seed", help="Louvain and sampling seed."),
        betweenness_samples: int = typer.Option(100, "--betweenness-samples", help="Maximum sampled pivots."),
        artist: str | None = typer.Option(None, "--artist", help="Exact display name to focus on."),
        hops: int = typer.Option(1, "--hops", help="Neighborhood radius."),
        output_format: str = typer.Option("json", "--format", help="Result format: json or graphml."),
        _json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command(
            "listening-graph",
            session,
            csv,
            {
                "gap_minutes": gap_minutes,
                "min_artist_plays": min_artist_plays,
                "min_shared_sessions": min_shared_sessions,
                "start_year": start_year,
                "end_year": end_year,
                "community_resolution": community_resolution,
                "community_seed": community_seed,
                "betweenness_samples": betweenness_samples,
                "focus_artist": artist,
                "hops": hops,
                "output_format": output_format,
            },
        )

    @app.command("session-start", help=SESSION_START_HELP)
    def session_start(
        session_id: str = typer.Option(..., "--session-id", help="Unique session ID."),
        csv: Path = typer.Option(..., "--csv", help="Scrobbles CSV for this session."),
        json_output: bool = typer.Option(True, "--json", help="Emit NDJSON startup events."),
    ):
        try:
            process = start_session(session_id=session_id, csv_path=csv, json_output=json_output)
            if not json_output:
                typer.echo(f"Started session {session_id} with pid {process.pid}")
        except Exception as exc:
            if json_output:
                typer.echo(json.dumps(error_envelope(
                    command="session-start",
                    code=type(exc).__name__.upper(),
                    message=str(exc),
                    retryable=False,
                    session_id=session_id,
                ), sort_keys=True))
            else:
                typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

    @app.command("session-status", help="Read metadata for a named daemon session.")
    def session_status(
        session: str = typer.Option(..., "--session", help="Session ID."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON."),
    ):
        try:
            payload = success_envelope(
                "session-status", read_session_status(session), session_id=session
            )
            if json_output:
                print_json(payload)
            else:
                typer.echo(payload["result"])
        except Exception as exc:
            if json_output:
                print_json(error_envelope(
                    command="session-status",
                    code=type(exc).__name__.upper(),
                    message=str(exc),
                    retryable=False,
                    session_id=session,
                ))
            else:
                typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

    @app.command("session-list", help="List known Music History daemon sessions.")
    def session_list(json_output: bool = typer.Option(True, "--json", help="Emit structured JSON.")):
        payload = success_envelope("session-list", {"sessions": list_sessions()}, session_id=None)
        if json_output:
            print_json(payload)
        else:
            typer.echo(payload["result"])

    @app.command("session-stop", help="Stop a named daemon session.")
    def session_stop(
        session: str = typer.Option(..., "--session", help="Session ID."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON."),
    ):
        try:
            result = stop_session(session)
            payload = success_envelope("session-stop", result, session_id=session)
            if json_output:
                print_json(payload)
            else:
                typer.echo(f"Stopped session {session} with pid {result['pid']}")
        except Exception as exc:
            if json_output:
                print_json(error_envelope(
                    command="session-stop",
                    code=type(exc).__name__.upper(),
                    message=str(exc),
                    retryable=False,
                    session_id=session,
                ))
            else:
                typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1)

    @app.command("session-cleanup", help="Remove files for stopped or stale sessions.")
    def session_cleanup(
        session: str | None = typer.Option(None, "--session", help="Clean one session ID."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON."),
    ):
        cleaned = []
        skipped = []
        errors = []

        def cleanup_one(session_id: str) -> None:
            outcome = remove_session_files(session_id)
            if outcome == "missing":
                errors.append({"session_id": session_id, "code": "SESSION_NOT_FOUND"})
                return
            if outcome == "live":
                skipped.append({"session_id": session_id, "reason": "live_session"})
                return
            cleaned.append(session_id)

        if session:
            cleanup_one(session)
        else:
            for item in list_sessions():
                session_id = item.get("session_id")
                if not session_id:
                    errors.append({"metadata": item, "code": "MISSING_SESSION_ID"})
                    continue
                cleanup_one(session_id)

        result = {"cleaned": cleaned, "skipped": skipped, "errors": errors}
        payload = success_envelope("session-cleanup", result, session_id=session)
        if json_output:
            print_json(payload)
        else:
            typer.echo(result)

    @app.command("taste-evolution", help=_agent_help("Analyze taste evolution."))
    def taste_evolution(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        start_year: int = typer.Option(2005, "--start-year", help="First year to analyze."),
        end_year: int = typer.Option(2025, "--end-year", help="Last year to analyze."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("taste-evolution", session, csv, {"start_year": start_year, "end_year": end_year})

    @app.command("musical-bridges", help=_agent_help("Find musical bridges."))
    def musical_bridges(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name to find bridges from."),
        top_n: int = typer.Option(10, "--top-n", help="Number of similar artists per source."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("musical-bridges", session, csv, {"artist": artist, "top_n": top_n})

    @app.command("blind-spots", help=_agent_help("Find critically acclaimed blind spots."))
    def blind_spots(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        min_critics: int = typer.Option(3, "--min-critics", help="Minimum critics who listed the album."),
        limit: int = typer.Option(20, "--limit", help="Maximum recommendations to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("blind-spots", session, csv, {"year": year, "min_critics": min_critics, "limit": limit})

    @app.command("artist-deep-dive", help=_agent_help("Analyze one or more artists."))
    def artist_deep_dive(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artists: list[str] = typer.Option(..., "--artist", help="Artist name. Repeat for multiple artists."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("artist-deep-dive", session, csv, {"artists": artists})

    @app.command("similar-artists", help=_agent_help("Find similar artists."))
    def similar_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name to find similar artists for."),
        source: str = typer.Option("user", "--source", help="Similarity source: user or critics."),
        top_n: int = typer.Option(10, "--top-n", help="Number of similar artists to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("similar-artists", session, csv, {"artist": artist, "source": source, "top_n": top_n})

    @app.command("listening-stats", help=_agent_help("Return listening statistics."))
    def listening_stats(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("listening-stats", session, csv, {"year": year})

    @app.command("top-artists", help=_agent_help("Return top artists."))
    def top_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        limit: int = typer.Option(20, "--limit", help="Maximum artists to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("top-artists", session, csv, {"year": year, "limit": limit})

    @app.command("critic-alignment", help=_agent_help("Find aligned critics."))
    def critic_alignment(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        limit: int = typer.Option(20, "--limit", help="Number of critics to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critic-alignment", session, csv, {"limit": limit})

    @app.command("temporal-patterns", help=_agent_help("Analyze temporal listening patterns."))
    def temporal_patterns(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("temporal-patterns", session, csv, {"year": year})

    @app.command("period-summary", help=_agent_help("Summarize a listening period."))
    def period_summary(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        start_year: int = typer.Option(..., "--start-year", help="First year of the period."),
        end_year: int = typer.Option(..., "--end-year", help="Last year of the period."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("period-summary", session, csv, {"start_year": start_year, "end_year": end_year})

    @app.command("year-review", help=_agent_help("Generate year review data."))
    def year_review(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        years: list[int] = typer.Option([2025], "--year", help="Year to review. Repeat for multiple years."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("year-review", session, csv, {"years": years})

    @app.command("listening-by-release-era", help=_agent_help("Analyze listening by release era."))
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

    @app.command("common-transitions", help=_agent_help("Find common artist transitions."))
    def common_transitions(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist to analyze transitions for."),
        top_n: int = typer.Option(10, "--top-n", help="Number of top transitions to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("common-transitions", session, csv, {"artist": artist, "top_n": top_n})

    @app.command("discovery-context", help=_agent_help("Analyze artist discovery context."))
    def discovery_context(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist to get discovery context for."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("discovery-context", session, csv, {"artist": artist})

    @app.command("critics-world", help=_agent_help("Explore critics world."))
    def critics_world(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critics-world", session, csv, {"year": year})

    @app.command("album-acclaim", help=_agent_help("Analyze an album's critical acclaim."))
    def album_acclaim(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name."),
        album: str = typer.Option(..., "--album", help="Album name."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("album-acclaim", session, csv, {"artist": artist, "album": album, "year": year})

    @app.command("validated-albums", help=_agent_help("Find albums validated by critics."))
    def validated_albums(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional critics year filter."),
        limit: int = typer.Option(50, "--limit", help="Maximum albums to return."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("validated-albums", session, csv, {"year": year, "limit": limit})

    @app.command("critic-profile", help=_agent_help("Analyze a critic profile."))
    def critic_profile(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        critic_name: str = typer.Option(..., "--critic-name", help="Name of the critic to analyze."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critic-profile", session, csv, {"critic_name": critic_name, "year": year})

    @app.command("search-critics-artist", help=_agent_help("Search critics lists for an artist."))
    def search_critics_artist(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        artist: str = typer.Option(..., "--artist", help="Artist name to search for."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("search-critics-artist", session, csv, {"artist": artist, "year": year})

    @app.command("obsession-tracks", help=_agent_help("Find track obsessions."))
    def obsession_tracks(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        min_plays: int = typer.Option(20, "--min-plays", help="Minimum plays for a track to be considered."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("obsession-tracks", session, csv, {"year": year, "min_plays": min_plays})

    @app.command("one-track-artists", help=_agent_help("Find one-track artist relationships."))
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

    @app.command("ep-single-artists", help=_agent_help("Find EP/single-heavy artists."))
    def ep_single_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int | None = typer.Option(None, "--year", help="Optional year filter."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("ep-single-artists", session, csv, {"year": year})

    @app.command("overview-summary", help=_agent_help("Return overview summary."))
    def overview_summary(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("overview-summary", session, csv, {})

    @app.command("discovered-artists", help=_agent_help("List discovered artists."))
    def discovered_artists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int = typer.Option(..., "--year", help="Discovery year."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("discovered-artists", session, csv, {"year": year})

    @app.command("critics-lists", help=_agent_help("List critics for a year."))
    def critics_lists(
        session: str | None = typer.Option(None, "--session", help="Named daemon session ID."),
        csv: Path | None = typer.Option(None, "--csv", help="Run one-shot against this scrobbles CSV."),
        year: int = typer.Option(..., "--year", help="Critics list year."),
        json_output: bool = typer.Option(True, "--json", help="Emit structured JSON on stdout."),
    ):
        _run_agent_command("critics-lists", session, csv, {"year": year})
