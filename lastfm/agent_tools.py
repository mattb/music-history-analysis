"""Plain agent-callable Last.fm analysis tools."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Optional

import pandas as pd

from . import crossref, data, listening_graph, musicbrainz_db
from .analysis_state import AnalysisState, to_serializable

COMMANDS = {
    "taste-evolution": "explore_taste_evolution",
    "musical-bridges": "find_musical_bridges",
    "blind-spots": "discover_blind_spots",
    "artist-deep-dive": "get_artist_deep_dive",
    "similar-artists": "find_similar_artists",
    "listening-stats": "get_listening_stats",
    "top-artists": "get_top_artists",
    "critic-alignment": "get_critic_alignment",
    "temporal-patterns": "get_temporal_patterns",
    "period-summary": "get_period_summary",
    "year-review": "get_year_review",
    "listening-by-release-era": "get_listening_by_release_era",
    "common-transitions": "get_common_transitions",
    "discovery-context": "get_discovery_context",
    "critics-world": "explore_critics_world",
    "album-acclaim": "get_album_acclaim",
    "validated-albums": "get_my_validated_albums",
    "critic-profile": "get_critic_profile",
    "search-critics-artist": "search_critics_for_artist",
    "obsession-tracks": "get_obsession_tracks",
    "one-track-artists": "get_one_track_artists",
    "ep-single-artists": "get_ep_single_artists",
    "overview-summary": "get_overview",
    "discovered-artists": "get_discovered_artists",
    "critics-lists": "get_critics_lists",
    "listening-graph": "get_listening_graph",
}


def dispatch(state: AnalysisState, command: str, params: dict[str, Any]) -> Any:
    if command not in COMMANDS:
        raise ValueError(f"Unknown agent command: {command}")
    fn = globals()[COMMANDS[command]]
    return to_serializable(fn(state, **params))


def get_listening_graph(
    state: AnalysisState,
    gap_minutes: int = 30,
    min_artist_plays: int = 10,
    min_shared_sessions: int = 2,
    start_year: int | None = None,
    end_year: int | None = None,
    community_resolution: float = 1.0,
    community_seed: int = 0,
    betweenness_samples: int = 100,
    focus_artist: str | None = None,
    hops: int = 1,
    output_format: str = "json",
) -> dict[str, Any]:
    """Return an artist graph without adding editorial interpretation."""
    config = listening_graph.GraphConfig(
        gap_minutes=gap_minutes,
        min_artist_plays=min_artist_plays,
        min_shared_sessions=min_shared_sessions,
        start_year=start_year,
        end_year=end_year,
        community_resolution=community_resolution,
        community_seed=community_seed,
        betweenness_samples=betweenness_samples,
    )
    return listening_graph.analyze_listening_graph(
        state.df,
        config,
        focus_artist=focus_artist,
        hops=hops,
        output_format=output_format,
    )

def explore_taste_evolution(
    state: AnalysisState,
    start_year: int = 2005,
    end_year: int = 2025,
) -> dict:
    """Analyze how musical taste has evolved over time.

    Returns discovered artists per era, abandoned artists, loyalty patterns,
    and listening intensity. Great for understanding the user's musical journey.

    Args:
        start_year: First year to analyze
        end_year: Last year to analyze
    """
    df = state.df

    result = {
        "period": f"{start_year}-{end_year}",
        "total_plays": len(df),
        "total_artists": df["artist"].nunique(),
        "years": {},
    }

    for year in range(start_year, end_year + 1):
        year_df = data.filter_by_year(df, year)
        if len(year_df) == 0:
            continue

        # Get discoveries and abandonments for this year
        discovered = data.artists_discovered_in_year(df, year)
        abandoned = data.artists_abandoned_in_year(df, year)

        # Top artists for the year
        top = data.top_artists(year_df, limit=10)

        result["years"][year] = {
            "plays": len(year_df),
            "unique_artists": year_df["artist"].nunique(),
            "discovered": len(discovered),
            "abandoned": len(abandoned),
            "top_discoveries": discovered.head(5)["artist"].tolist() if len(discovered) > 0 else [],
            "top_artists": top["artist"].tolist() if len(top) > 0 else [],
        }

    # Session stats for overall listening intensity
    session_stats = data.get_session_stats(df)
    result["session_stats"] = session_stats

    return to_serializable(result)


def find_musical_bridges(
    state: AnalysisState,
    artist: str,
    top_n: int = 10,
) -> dict:
    """Find artists that bridge from a known artist to new discoveries.

    Uses both user listening patterns AND critics consensus to find
    artists that connect the user's taste to unexplored territory.
    Identifies "bridge" artists that appear in both spaces.

    Args:
        artist: Artist name to find bridges from
        top_n: Number of similar artists to return per source
    """

    result = {
        "query_artist": artist,
        "user_similar": [],
        "critics_similar": [],
        "bridges": [],
        "user_only": [],
        "critics_only": [],
    }

    # Find similar in user space
    if state.user_embeddings and artist in state.user_embeddings.artist_to_idx:
        user_similar = state.user_embeddings.find_similar(artist, top_n=top_n)
        result["user_similar"] = [
            {"artist": name, "similarity": round(score, 3)}
            for name, score in user_similar
        ]

    # Find similar in critics space
    if state.critics_embeddings:
        norm_artist = crossref.normalize_for_matching(artist)
        if norm_artist in state.critics_embeddings.artist_to_idx:
            critics_similar = state.critics_embeddings.find_similar(artist, top_n=top_n)
            result["critics_similar"] = [
                {"artist": name, "similarity": round(score, 3)}
                for name, score in critics_similar
            ]

    # Find bridges (in both spaces)
    user_set = {crossref.normalize_for_matching(x["artist"]) for x in result["user_similar"]}
    critics_set = {crossref.normalize_for_matching(x["artist"]) for x in result["critics_similar"]}

    bridges = user_set & critics_set
    user_only = user_set - critics_set
    critics_only = critics_set - user_set

    result["bridges"] = list(bridges)[:top_n]
    result["user_only"] = list(user_only)[:top_n]
    result["critics_only"] = list(critics_only)[:top_n]

    return to_serializable(result)


def discover_blind_spots(
    state: AnalysisState,
    year: Optional[int] = None,
    min_critics: int = 3,
    limit: int = 20,
) -> dict:
    """Find critically acclaimed albums the user hasn't heard.

    Returns recommendations weighted by critic alignment - albums loved by
    critics who share the user's taste rank higher.

    Args:
        year: Specific year to check (None = all available years)
        min_critics: Minimum critics who listed the album
        limit: Maximum recommendations to return
    """

    # Determine which years to search
    if year is not None:
        years = [year]
    else:
        years = state.get_all_critics_years()

    # Get albums user has listened to
    listened_albums = data.get_albums_listened_to(state.df)
    listened_norm = {
        (crossref.normalize_for_matching(a), crossref.normalize_for_matching(t))
        for a, t in listened_albums
    }

    # Collect unheard acclaimed albums
    unheard = {}  # (artist_norm, album_norm) -> {artist, album, critics, count}

    for y in years:
        critics_data = state.get_critics_data(y)
        for critic_list in critics_data:
            critic = critic_list.get("critic", "Unknown")
            for album in critic_list.get("albums", []):
                artist = album.get("artist", "")
                title = album.get("title", "")
                if not artist or not title:
                    continue

                key = (
                    crossref.normalize_for_matching(artist),
                    crossref.normalize_for_matching(title),
                )
                if key in listened_norm:
                    continue

                if key not in unheard:
                    unheard[key] = {
                        "artist": artist,
                        "album": title,
                        "critics": [],
                        "count": 0,
                        "years": set(),
                    }
                unheard[key]["critics"].append(critic)
                unheard[key]["count"] += 1
                unheard[key]["years"].add(y)

    # Filter by min_critics and sort
    recommendations = [
        {
            "artist": v["artist"],
            "album": v["album"],
            "critics_count": v["count"],
            "years": sorted(v["years"]),
            "sample_critics": v["critics"][:3],
        }
        for v in unheard.values()
        if v["count"] >= min_critics
    ]
    recommendations.sort(key=lambda x: -x["critics_count"])

    return to_serializable({
        "total_unheard_acclaimed": len(recommendations),
        "min_critics_threshold": min_critics,
        "recommendations": recommendations[:limit],
    })


def _analyze_single_artist(state: AnalysisState, df: pd.DataFrame, artist: str) -> dict:
    """Analyze a single artist - internal helper."""
    # Filter to this artist
    artist_plays = df[df["artist"].str.lower() == artist.lower()]

    if len(artist_plays) == 0:
        return {"artist": artist, "error": "not found in listening history"}

    # Get the actual artist name with correct casing
    actual_name = artist_plays["artist"].iloc[0]

    # Basic stats
    first_play = artist_plays["timestamp"].min()
    last_play = artist_plays["timestamp"].max()
    total_plays = len(artist_plays)

    # Year-by-year breakdown
    plays_by_year = artist_plays.groupby("year").size().to_dict()
    peak_year = max(plays_by_year, key=plays_by_year.get) if plays_by_year else None

    # Albums played with detailed familiarity metrics
    albums = artist_plays.groupby("album").size().sort_values(ascending=False)

    # Get detailed familiarity breakdown for this artist's albums
    familiarity_details = data.get_album_familiarity_details(artist_plays)

    albums_with_metrics = []
    for album, plays in albums.head(10).items():
        album_info = {
            "album": album,
            "plays": int(plays),
        }
        # Add familiarity metrics if available
        key = (actual_name, album)
        if key in familiarity_details:
            metrics = familiarity_details[key]
            album_info.update({
                "familiarity": metrics["familiarity"],
                "coverage": metrics["coverage"],
                "depth": metrics["depth"],
                "dispersion": metrics["dispersion"],
                "unique_tracks": metrics["unique_tracks"],
                "avg_plays_per_track": metrics["avg_plays_per_track"],
            })
        albums_with_metrics.append(album_info)

    # Similar artists in both spaces
    user_similar = []
    critics_similar = []

    if state.user_embeddings and actual_name in state.user_embeddings.artist_to_idx:
        user_similar = [
            {"artist": name, "similarity": round(score, 3)}
            for name, score in state.user_embeddings.find_similar(actual_name, top_n=10)
        ]

    if state.critics_embeddings:
        norm = crossref.normalize_for_matching(actual_name)
        if norm in state.critics_embeddings.artist_to_idx:
            critics_similar = [
                {"artist": name, "similarity": round(score, 3)}
                for name, score in state.critics_embeddings.find_similar(actual_name, top_n=10)
            ]

    # Find critics who listed this artist
    artist_critics = []
    for year in state.get_all_critics_years():
        critics_data = state.get_critics_data(year)
        for critic_list in critics_data:
            critic = critic_list.get("critic", "Unknown")
            for album in critic_list.get("albums", []):
                if crossref.normalize_for_matching(album.get("artist", "")) == crossref.normalize_for_matching(actual_name):
                    artist_critics.append({
                        "critic": critic,
                        "year": year,
                        "album": album.get("title", ""),
                    })

    return {
        "artist": actual_name,
        "first_play": first_play.isoformat() if first_play else None,
        "last_play": last_play.isoformat() if last_play else None,
        "total_plays": total_plays,
        "years_active": (last_play.year - first_play.year + 1) if first_play and last_play else 0,
        "plays_by_year": plays_by_year,
        "peak_year": peak_year,
        "albums_played": albums_with_metrics,
        "similar_from_listening": user_similar,
        "similar_from_critics": critics_similar,
        "critics_who_listed": artist_critics[:10],
    }


def get_artist_deep_dive(state: AnalysisState, artists: list[str]) -> list:
    """Complete analysis of user's relationship with one or more artists.

    Returns for each artist: first/last play, total plays, albums listened,
    similar artists in user space vs critics space, which critics champion them.

    Args:
        artists: List of artist names to analyze (can be a single-item list)
    """
    df = state.df

    results = []
    for artist in artists:
        result = _analyze_single_artist(state, df, artist)
        results.append(result)

    return to_serializable(results)


# =============================================================================
# PRECISE TOOLS - Direct query tools
# =============================================================================

def find_similar_artists(
    state: AnalysisState,
    artist: str,
    source: str = "user",
    top_n: int = 10,
) -> list:
    """Find artists similar to the given artist.

    Args:
        artist: Artist name to find similar artists for
        source: "user" (your listening patterns) or "critics" (critical consensus)
        top_n: Number of results to return
    """

    if source == "user":
        if not state.user_embeddings or artist not in state.user_embeddings.artist_to_idx:
            return []
        similar = state.user_embeddings.find_similar(artist, top_n=top_n)
    elif source == "critics":
        if not state.critics_embeddings:
            return []
        norm = crossref.normalize_for_matching(artist)
        if norm not in state.critics_embeddings.artist_to_idx:
            return []
        similar = state.critics_embeddings.find_similar(artist, top_n=top_n)
    else:
        return [{"error": f"Unknown source '{source}'. Use 'user' or 'critics'."}]

    return to_serializable([
        {"artist": name, "similarity": round(score, 3)}
        for name, score in similar
    ])


def get_listening_stats(state: AnalysisState, year: Optional[int] = None) -> dict:
    """Get listening statistics for a year or all time.

    Args:
        year: Specific year (None = all time)
    """
    df = state.df

    if year is not None:
        df = data.filter_by_year(df, year)

    if len(df) == 0:
        return {"error": f"No data for year {year}"}

    return to_serializable({
        "period": str(year) if year else "all time",
        "total_plays": len(df),
        "unique_artists": df["artist"].nunique(),
        "unique_albums": df["album"].nunique() if "album" in df.columns else 0,
        "unique_tracks": df["track"].nunique() if "track" in df.columns else 0,
        "date_range": {
            "first": df["timestamp"].min().isoformat(),
            "last": df["timestamp"].max().isoformat(),
        },
    })


def get_top_artists(
    state: AnalysisState,
    year: Optional[int] = None,
    limit: int = 20,
) -> list:
    """Get top artists by play count.

    Args:
        year: Specific year (None = all time)
        limit: Number of artists to return
    """
    df = state.df

    if year is not None:
        df = data.filter_by_year(df, year)

    top = data.top_artists(df, limit=limit)
    return to_serializable([
        {"artist": row["artist"], "plays": int(row["plays"])}
        for _, row in top.iterrows()
    ])


def get_critic_alignment(state: AnalysisState, limit: int = 20) -> list:
    """Find critics whose taste aligns with the user's.

    Returns critics ranked by how well their picks match your listening,
    with details about overlap.

    Args:
        limit: Number of critics to return
    """

    if not state.critic_vectors:
        return [{"error": "Critic vectors not available"}]

    # Compute user vector from listening
    user_vector = state.critic_vectors.compute_user_vector(state.df, top_n_artists=100)

    # Find similar critics
    similar = state.critic_vectors.find_similar_critics(user_vector, top_n=limit)

    return to_serializable([
        {
            "critic": name,
            "alignment": round(score, 3),
            "publication": info.get("publication", "Unknown"),
        }
        for name, score, info in similar
    ])


def get_temporal_patterns(state: AnalysisState, year: int | None = None) -> dict:
    """Analyze when listening happens.

    Returns time-of-day distribution, day-of-week patterns,
    and monthly patterns. Useful for understanding listening context.

    Args:
        year: Specific year to analyze (None = all time)
    """
    df = state.df

    if year is not None:
        df = data.filter_by_year(df, year)

    if len(df) == 0:
        return {"error": f"No data for year {year}"}

    total = len(df)

    # Hour of day (0-23)
    hour_counts = df.groupby("hour").size()
    hours = {
        int(h): {"plays": int(c), "pct": round(c / total * 100, 1)}
        for h, c in hour_counts.items()
    }

    # Day of week
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday_counts = df.groupby("weekday").size()
    weekdays = {
        day: {"plays": int(weekday_counts.get(day, 0)), "pct": round(weekday_counts.get(day, 0) / total * 100, 1)}
        for day in weekday_order
    }

    # Month (1-12)
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    month_counts = df.groupby("month").size()
    months = {
        month_names[m - 1]: {"plays": int(month_counts.get(m, 0)), "pct": round(month_counts.get(m, 0) / total * 100, 1)}
        for m in range(1, 13)
    }

    # Peak times
    peak_hour = hour_counts.idxmax() if len(hour_counts) > 0 else None
    peak_weekday = weekday_counts.idxmax() if len(weekday_counts) > 0 else None
    peak_month = month_counts.idxmax() if len(month_counts) > 0 else None

    return to_serializable({
        "period": str(year) if year else "all time",
        "total_plays": total,
        "by_hour": hours,
        "by_weekday": weekdays,
        "by_month": months,
        "peak_hour": int(peak_hour) if peak_hour is not None else None,
        "peak_weekday": peak_weekday,
        "peak_month": month_names[peak_month - 1] if peak_month else None,
    })


def get_period_summary(state: AnalysisState, start_year: int, end_year: int) -> dict:
    """Get aggregated listening stats for a year range.

    Returns total plays, unique artists/albums, top artists across the period,
    year-by-year breakdown, and discovery rate.

    Args:
        start_year: First year of the period
        end_year: Last year of the period (inclusive)
    """
    df_full = state.df

    # Filter to date range
    df = df_full[(df_full["year"] >= start_year) & (df_full["year"] <= end_year)]

    if len(df) == 0:
        return {"error": f"No data for period {start_year}-{end_year}"}

    # Aggregate stats
    result = {
        "period": f"{start_year}-{end_year}",
        "total_plays": len(df),
        "unique_artists": df["artist"].nunique(),
        "unique_albums": df[df["album"] != ""]["album"].nunique(),
        "unique_tracks": df["track"].nunique(),
        "years": {},
        "top_artists": [],
        "discoveries_per_year": {},
    }

    # Year-by-year breakdown
    for year in range(start_year, end_year + 1):
        year_df = data.filter_by_year(df_full, year)
        if len(year_df) > 0:
            result["years"][year] = {
                "plays": len(year_df),
                "artists": year_df["artist"].nunique(),
            }

            # Count discoveries for this year
            discovered = data.artists_discovered_in_year(df_full, year)
            result["discoveries_per_year"][year] = len(discovered)

    # Top artists across the period
    top = data.top_artists(df, limit=20)
    result["top_artists"] = [
        {"artist": row["artist"], "plays": int(row["plays"])}
        for _, row in top.iterrows()
    ]

    return to_serializable(result)


def _get_single_year_review(state: AnalysisState, df_full: pd.DataFrame, year: int) -> dict:
    """Internal helper for single year review."""
    df = data.filter_by_year(df_full, year)

    if len(df) == 0:
        return {"year": year, "error": f"No listening data for {year}"}

    result = {
        "year": year,
        "stats": {},
        "top_artists": [],
        "top_albums": [],
        "discoveries": [],
        "critics": None,
        "metadata": None,
    }

    # Basic stats
    result["stats"] = {
        "total_plays": len(df),
        "unique_artists": df["artist"].nunique(),
        "unique_albums": df[df["album"] != ""]["album"].nunique(),
        "unique_tracks": df["track"].nunique(),
    }

    # Previous year comparison
    df_prev = data.filter_by_year(df_full, year - 1)
    if len(df_prev) > 0:
        prev_plays = len(df_prev)
        diff = result["stats"]["total_plays"] - prev_plays
        result["stats"]["vs_previous_year"] = {
            "previous_plays": prev_plays,
            "change": diff,
            "change_pct": round(diff / prev_plays * 100, 1),
        }

    # Top artists with context
    top_artists_df = data.top_artists(df, 15)
    for _, row in top_artists_df.iterrows():
        artist_name = row["artist"]
        artist_plays = row["plays"]
        artist_df = df_full[df_full["artist"] == artist_name]
        first_play = artist_df["timestamp"].min()
        yearly = artist_df.groupby("year").size()
        peak_year = yearly.idxmax()

        result["top_artists"].append({
            "name": artist_name,
            "plays": int(artist_plays),
            "first_year": int(first_play.year),
            "total_all_time": len(artist_df),
            "is_peak_year": (peak_year == year),
            "is_new_discovery": (first_play.year == year),
        })

    # Top albums with context
    top_albums_df = data.top_albums(df, 15)
    for _, row in top_albums_df.iterrows():
        artist_name = row["artist"]
        album_name = row["album"]
        plays = row["plays"]
        album_df = df_full[(df_full["artist"] == artist_name) & (df_full["album"] == album_name)]
        first_play = album_df["timestamp"].min()

        result["top_albums"].append({
            "artist": artist_name,
            "album": album_name,
            "plays": int(plays),
            "first_play": first_play.isoformat() if first_play else None,
            "discovered_this_year": (first_play.year == year) if first_play else False,
        })

    # New discoveries
    discovered = data.artists_discovered_in_year(df_full, year)
    result["stats"]["new_artists_discovered"] = len(discovered)

    for _, row in discovered.head(10).iterrows():
        result["discoveries"].append({
            "name": row["artist"],
            "plays": int(row["plays_in_year"]),
            "first_track": row["track"],
            "first_date": row["timestamp"].isoformat() if hasattr(row["timestamp"], "isoformat") else str(row["timestamp"]),
        })

    # Critics data
    critics_data = state.get_critics_data(year)
    if critics_data:
        listened_albums = data.get_albums_listened_to(df_full)
        listened_norm = {
            (crossref.normalize_for_matching(a), crossref.normalize_for_matching(t))
            for a, t in listened_albums
        }

        # Count matches and build critic overlap
        matched_count = 0
        total_critics_albums = 0
        critic_overlap = {}

        for critic_list in critics_data:
            critic = critic_list.get("critic", "Unknown")
            albums = critic_list.get("albums", [])
            total = len(albums)
            overlap = 0

            for album in albums:
                artist = album.get("artist", "")
                title = album.get("title", "")
                if artist and title:
                    total_critics_albums += 1
                    key = (crossref.normalize_for_matching(artist),
                           crossref.normalize_for_matching(title))
                    if key in listened_norm:
                        matched_count += 1
                        overlap += 1

            if total > 0:
                critic_overlap[critic] = {
                    "overlap": overlap,
                    "total": total,
                    "pct": round(overlap / total * 100, 1),
                }

        # Top aligned critics
        top_critics = sorted(
            [{"name": k, **v} for k, v in critic_overlap.items()],
            key=lambda x: -x["overlap"]
        )[:10]

        result["critics"] = {
            "year": year,
            "total_critics": len(critics_data),
            "your_overlap_pct": round(matched_count / total_critics_albums * 100, 1) if total_critics_albums > 0 else 0,
            "matched_albums": matched_count,
            "total_critics_albums": total_critics_albums,
            "top_aligned_critics": top_critics,
        }

    # MusicBrainz metadata
    db_stats = musicbrainz_db.get_database_stats()
    if db_stats and db_stats.get("has_full_schema"):
        try:
            conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)

            # Get album plays for this year
            df_albums = df[df["album"] != ""].copy()
            df_albums = df_albums[df_albums["artist"].notna()]
            album_plays = df_albums.groupby(["artist", "album"]).size().reset_index(name="plays")

            # Collect metadata
            genre_plays = defaultdict(int)
            label_plays = defaultdict(int)
            country_plays = defaultdict(int)
            new_releases = 0
            catalog_releases = 0
            albums_matched = 0

            for _, row in album_plays.iterrows():
                artist = row["artist"]
                album_name = row["album"]
                plays = row["plays"]

                info = musicbrainz_db.lookup_release(artist, album_name, conn)
                if info:
                    albums_matched += 1

                    if info.genres:
                        for g in info.genres:
                            genre_plays[g] += plays

                    if info.labels:
                        for label in info.labels:
                            label_plays[label] += plays

                    if info.country:
                        country_plays[info.country] += plays

                    if info.year == year:
                        new_releases += plays
                    elif info.year:
                        catalog_releases += plays

            conn.close()

            if albums_matched > 0:
                total_matched_plays = new_releases + catalog_releases

                # Genre breakdown (top 10)
                sorted_genres = sorted(genre_plays.items(), key=lambda x: -x[1])
                total_genre_plays = sum(g[1] for g in sorted_genres) or 1

                # Label breakdown (top 10)
                sorted_labels = sorted(label_plays.items(), key=lambda x: -x[1])
                total_label_plays = sum(label[1] for label in sorted_labels) or 1

                # Country breakdown
                country_names = {
                    "US": "United States", "GB": "United Kingdom", "JP": "Japan",
                    "DE": "Germany", "FR": "France", "CA": "Canada", "AU": "Australia",
                    "SE": "Sweden", "NL": "Netherlands", "XW": "Worldwide", "XE": "Europe",
                }
                sorted_countries = sorted(country_plays.items(), key=lambda x: -x[1])
                total_country_plays = sum(c[1] for c in sorted_countries) or 1

                result["metadata"] = {
                    "albums_matched": albums_matched,
                    "genres": [
                        {"name": g, "plays": p, "pct": round(p / total_genre_plays * 100, 1)}
                        for g, p in sorted_genres[:10]
                    ],
                    "labels": [
                        {"name": label, "plays": p, "pct": round(p / total_label_plays * 100, 1)}
                        for label, p in sorted_labels[:10]
                    ],
                    "countries": [
                        {"code": c, "name": country_names.get(c, c), "plays": p,
                         "pct": round(p / total_country_plays * 100, 1)}
                        for c, p in sorted_countries[:8]
                    ],
                    "new_vs_catalog": {
                        "new_pct": round(new_releases / total_matched_plays * 100, 1) if total_matched_plays > 0 else 0,
                        "catalog_pct": round(catalog_releases / total_matched_plays * 100, 1) if total_matched_plays > 0 else 0,
                    },
                }
        except Exception:
            pass  # MusicBrainz not available, metadata stays None

    return result


def get_year_review(state: AnalysisState, years: list[int] | int = 2025) -> dict | list:
    """Get comprehensive year-in-review data for one or more years.

    Returns listening stats, top artists/albums with context, new discoveries,
    critics alignment, and metadata breakdown (genres, labels, countries).
    This is the richest single view of a user's listening year.

    Args:
        years: Year or list of years to review (default: 2025)
    """
    df_full = state.df

    # Normalize to list
    if isinstance(years, int):
        single_year = True
        years = [years]
    else:
        single_year = False

    results = []
    for year in years:
        result = _get_single_year_review(state, df_full, year)
        results.append(result)

    # Return single dict if single year requested, list otherwise
    if single_year:
        return to_serializable(results[0])
    return to_serializable(results)


def get_listening_by_release_era(
    state: AnalysisState,
    release_start: int,
    release_end: int,
    limit: int = 50,
) -> dict:
    """Get all plays of music released in a specific era.

    Answers: "What's my relationship with music from the 90s?"
    Uses MusicBrainz release years to filter.

    Args:
        release_start: First release year to include
        release_end: Last release year to include (inclusive)
        limit: Maximum albums to return
    """
    df = state.df

    # Check MusicBrainz availability
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats or not db_stats.get("has_full_schema"):
        return {"error": "MusicBrainz database not available. Run: lastfm metadata download"}

    conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)

    # Get unique albums
    df_albums = df[df["album"] != ""].copy()
    df_albums = df_albums[df_albums["artist"].notna()]
    album_plays = df_albums.groupby(["artist", "album"]).size().reset_index(name="plays")

    era_albums = []
    era_artists = set()
    total_plays = 0

    for _, row in album_plays.iterrows():
        artist = row["artist"]
        album_name = row["album"]
        plays = row["plays"]

        info = musicbrainz_db.lookup_release(artist, album_name, conn)
        if info and info.year and release_start <= info.year <= release_end:
            era_albums.append({
                "artist": artist,
                "album": album_name,
                "release_year": info.year,
                "plays": plays,
                "genres": info.genres[:3] if info.genres else [],
            })
            era_artists.add(artist)
            total_plays += plays

    conn.close()

    # Sort by plays
    era_albums.sort(key=lambda x: -x["plays"])

    return to_serializable({
        "era": f"{release_start}-{release_end}",
        "total_plays": total_plays,
        "unique_artists": len(era_artists),
        "unique_albums": len(era_albums),
        "top_albums": era_albums[:limit],
    })


def get_common_transitions(state: AnalysisState, artist: str, top_n: int = 10) -> dict:
    """Find what typically plays before and after an artist.

    Returns common predecessors and successors based on
    sequential plays within listening sessions.

    Args:
        artist: Artist to analyze transitions for
        top_n: Number of top transitions to return
    """
    try:
        df = state.df

        # Find rows where this artist plays
        artist_lower = artist.lower()
        artist_mask = df["artist"].str.lower() == artist_lower

        if not artist_mask.any():
            return {"error": f"Artist '{artist}' not found in listening history"}

        # Get actual artist name
        actual_name = df[artist_mask]["artist"].iloc[0]
        total_plays = artist_mask.sum()

        # Use a simplified approach: look at adjacent plays without full session detection
        # This is much faster and still captures the pattern
        df_sorted = df.sort_values("timestamp").reset_index(drop=True)
        artist_indices = df_sorted[df_sorted["artist"].str.lower() == artist_lower].index.tolist()

        # Track before/after artists (only count if within 30 min gap)
        before_counts = defaultdict(int)
        after_counts = defaultdict(int)

        for idx in artist_indices:
            current_time = df_sorted.loc[idx, "timestamp"]

            # Get previous row
            if idx > 0:
                prev_row = df_sorted.loc[idx - 1]
                gap = (current_time - prev_row["timestamp"]).total_seconds() / 60
                if gap <= 30:  # Same session (30 min gap)
                    prev_artist = prev_row["artist"]
                    if prev_artist.lower() != artist_lower:
                        before_counts[prev_artist] += 1

            # Get next row
            if idx < len(df_sorted) - 1:
                next_row = df_sorted.loc[idx + 1]
                gap = (next_row["timestamp"] - current_time).total_seconds() / 60
                if gap <= 30:  # Same session
                    next_artist = next_row["artist"]
                    if next_artist.lower() != artist_lower:
                        after_counts[next_artist] += 1

        # Sort and format
        top_before = sorted(before_counts.items(), key=lambda x: -x[1])[:top_n]
        top_after = sorted(after_counts.items(), key=lambda x: -x[1])[:top_n]

        return to_serializable({
            "artist": actual_name,
            "total_plays": total_plays,
            "plays_before": [
                {"artist": a, "count": c} for a, c in top_before
            ],
            "plays_after": [
                {"artist": a, "count": c} for a, c in top_after
            ],
        })
    except Exception as e:
        return {"error": f"Exception in get_common_transitions: {type(e).__name__}: {str(e)}"}


def get_discovery_context(state: AnalysisState, artist: str) -> dict:
    """Understand how an artist was discovered.

    Returns: what played in same session as first listen,
    what played in days before/after, any patterns.

    Args:
        artist: Artist to get discovery context for
    """
    try:
        df = state.df

        # Find first play of this artist
        artist_lower = artist.lower()
        artist_plays = df[df["artist"].str.lower() == artist_lower].copy()

        if len(artist_plays) == 0:
            return {"error": f"Artist '{artist}' not found in listening history"}

        # Get actual name and first play
        first_play = artist_plays.loc[artist_plays["timestamp"].idxmin()]
        actual_name = first_play["artist"]
        first_timestamp = first_play["timestamp"]
        first_track = first_play["track"]
        first_album = first_play["album"]

        # Get plays from day before and after (for context, doesn't need sessions)
        day_before = first_timestamp - pd.Timedelta(days=1)
        day_after = first_timestamp + pd.Timedelta(days=1)

        before_plays = df[(df["timestamp"] >= day_before) & (df["timestamp"] < first_timestamp)]
        after_plays = df[(df["timestamp"] > first_timestamp) & (df["timestamp"] <= day_after)]

        # Get unique artists from before/after (excluding the discovered artist)
        artists_before = before_plays[before_plays["artist"].str.lower() != artist_lower]["artist"].value_counts().head(5)
        artists_after = after_plays[after_plays["artist"].str.lower() != artist_lower]["artist"].value_counts().head(5)

        # For session analysis, only process a narrow window around first play (faster)
        window_start = first_timestamp - pd.Timedelta(hours=6)
        window_end = first_timestamp + pd.Timedelta(hours=6)
        df_window = df[(df["timestamp"] >= window_start) & (df["timestamp"] <= window_end)].copy()

        # Detect sessions only in the small window
        df_sessions = data.detect_sessions(df_window, gap_minutes=30)
        session_matches = df_sessions[df_sessions["timestamp"] == first_timestamp]

        session_artists = []
        session_track_count = 0
        if len(session_matches) > 0:
            first_session_idx = session_matches.index[0]
            first_session_id = df_sessions.loc[first_session_idx, "session_id"]
            session_plays = df_sessions[df_sessions["session_id"] == first_session_id]
            session_artists = session_plays[session_plays["artist"].str.lower() != artist_lower]["artist"].unique().tolist()
            session_track_count = len(session_plays)

        return to_serializable({
            "artist": actual_name,
            "first_play": {
                "timestamp": first_timestamp.isoformat(),
                "track": first_track,
                "album": first_album,
            },
            "discovery_session": {
                "other_artists": session_artists[:10],
                "total_tracks_in_session": session_track_count,
            },
            "context_day_before": [
                {"artist": a, "plays": int(c)} for a, c in artists_before.items()
            ],
            "context_day_after": [
                {"artist": a, "plays": int(c)} for a, c in artists_after.items()
            ],
        })
    except Exception as e:
        return {"error": f"Exception in get_discovery_context: {type(e).__name__}: {str(e)}"}


# =============================================================================
# CRITICS NARRATIVE TOOLS - Rich, coarse-grained tools for storytelling
# =============================================================================

def explore_critics_world(state: AnalysisState, year: Optional[int] = None) -> dict:
    """Your complete relationship with music criticism in one call.

    Returns everything needed to tell the story of how your taste aligns
    with critics: overall stats, taste-twin critics with their picks,
    weighted recommendations, and albums where you matched the critics.

    Args:
        year: Focus on a specific year (None = all years 2011-2025)
    """

    # Determine years to analyze
    if year is not None:
        years = [year] if year in state.get_all_critics_years() else []
    else:
        years = state.get_all_critics_years()

    if not years:
        return {"error": f"No critics data available for year {year}"}

    # Get user's listened albums
    listened_albums = state.get_listened_albums()

    # Build album index and critic picks index
    album_index = state.get_album_critics_index()
    critic_index = state.get_critic_picks_index()

    # Filter indices by year if specified
    if year is not None:
        # Filter album index to only include critics from specified year
        filtered_album_index = {}
        for key, album_data in album_index.items():
            filtered_critics = [c for c in album_data["critics"] if c["year"] == year]
            if filtered_critics:
                filtered_album_index[key] = {
                    "artist": album_data["artist"],
                    "album": album_data["album"],
                    "critics": filtered_critics,
                }
        album_index = filtered_album_index

    # Calculate overall stats
    total_acclaimed = len(album_index)
    you_heard = sum(1 for key in album_index if key in listened_albums)
    you_missed = total_acclaimed - you_heard

    # Calculate per-critic alignment
    critic_alignment = []
    for critic_name, critic_data in critic_index.items():
        picks = critic_data["picks"]
        if year is not None:
            picks = [p for p in picks if p["year"] == year]

        if not picks:
            continue

        heard = 0
        missed = 0
        shared_albums = []
        recommendations = []

        for pick in picks:
            key = (
                crossref.normalize_for_matching(pick["artist"]),
                crossref.normalize_for_matching(pick["album"]),
            )
            if key in listened_albums:
                heard += 1
                if len(shared_albums) < 5:
                    shared_albums.append({
                        "artist": pick["artist"],
                        "album": pick["album"],
                        "year": pick["year"],
                    })
            else:
                missed += 1
                if len(recommendations) < 5:
                    recommendations.append({
                        "artist": pick["artist"],
                        "album": pick["album"],
                        "year": pick["year"],
                    })

        total_picks = heard + missed
        if total_picks > 0:
            critic_alignment.append({
                "name": critic_name,
                "publication": critic_data["publication"],
                "alignment_score": round(heard / total_picks, 3),
                "their_picks_you_heard": heard,
                "their_picks_you_missed": missed,
                "top_shared_albums": shared_albums,
                "top_recommendations": recommendations,
            })

    # Sort by alignment score
    critic_alignment.sort(key=lambda x: -x["alignment_score"])
    taste_twins = critic_alignment[:15]

    # Build weighted recommendations (albums endorsed by aligned critics)
    unheard_albums = {}
    for key, album_data in album_index.items():
        if key in listened_albums:
            continue

        critics_list = album_data["critics"]
        if year is not None:
            critics_list = [c for c in critics_list if c["year"] == year]

        if not critics_list:
            continue

        # Calculate weighted score based on aligned critics
        score = 0
        endorsers = []
        for c in critics_list:
            # Find this critic's alignment score
            critic_info = next((x for x in critic_alignment if x["name"] == c["critic"]), None)
            if critic_info:
                score += critic_info["alignment_score"]
                if len(endorsers) < 5:
                    endorsers.append(c["critic"])

        unheard_albums[key] = {
            "artist": album_data["artist"],
            "album": album_data["album"],
            "year": critics_list[0]["year"] if critics_list else None,
            "score": round(score, 2),
            "endorsed_by": endorsers,
            "total_critics": len(critics_list),
        }

    # Sort by weighted score
    weighted_recs = sorted(unheard_albums.values(), key=lambda x: -x["score"])[:20]

    # Build validated taste (albums you loved that critics also loved)
    validated = []
    df = state.df
    for key, album_data in album_index.items():
        if key not in listened_albums:
            continue

        critics_list = album_data["critics"]
        if year is not None:
            critics_list = [c for c in critics_list if c["year"] == year]

        if not critics_list:
            continue

        # Get user's play data for this album
        artist_norm, album_norm = key
        album_plays = df[
            (df["artist"].str.lower().str.strip() == artist_norm) &
            (df["album"].str.lower().str.strip() == album_norm)
        ]

        if len(album_plays) == 0:
            continue

        first_play = album_plays["timestamp"].min()
        total_plays = len(album_plays)

        validated.append({
            "artist": album_data["artist"],
            "album": album_data["album"],
            "your_plays": total_plays,
            "critics_count": len(critics_list),
            "your_discovery": first_play.strftime("%Y-%m-%d"),
            "critics_year": critics_list[0]["year"],
        })

    # Sort by play count
    validated.sort(key=lambda x: -x["your_plays"])
    validated = validated[:20]

    # Count unique critics
    unique_critics = set()
    for album_data in album_index.values():
        for c in album_data["critics"]:
            if year is None or c["year"] == year:
                unique_critics.add(c["critic"])

    return to_serializable({
        "summary": {
            "total_critics": len(unique_critics),
            "years_covered": years,
            "your_overall_alignment": round(you_heard / total_acclaimed * 100, 1) if total_acclaimed > 0 else 0,
            "total_acclaimed_albums": total_acclaimed,
            "you_heard": you_heard,
            "you_missed": you_missed,
        },
        "taste_twin_critics": taste_twins,
        "weighted_recommendations": weighted_recs,
        "your_validated_taste": validated,
    })


def get_album_acclaim(state: AnalysisState, artist: str, album: str, year: Optional[int] = None) -> dict:
    """The critical story of a specific album.

    Returns all critics who listed this album, your listening relationship
    with it, and similar acclaimed albums you might like.

    Args:
        artist: Artist name
        album: Album name
        year: Filter to a specific year's lists (None = all years)
    """

    album_index = state.get_album_critics_index()
    listened_albums = state.get_listened_albums()

    # Find the album
    key = (
        crossref.normalize_for_matching(artist),
        crossref.normalize_for_matching(album),
    )

    if key not in album_index:
        return {
            "artist": artist,
            "album": album,
            "acclaimed": False,
            "message": "This album was not found in any critics' year-end lists (2011-2025)",
        }

    album_data = album_index[key]
    critics_list = album_data["critics"]

    if year is not None:
        critics_list = [c for c in critics_list if c["year"] == year]
        if not critics_list:
            return {
                "artist": album_data["artist"],
                "album": album_data["album"],
                "acclaimed": False,
                "message": f"This album was not listed by critics in {year}",
            }

    # Sort critics by rank (if available)
    critics_list_sorted = sorted(critics_list, key=lambda x: (x["year"], x["rank"] or 999))

    # Get years listed
    years_listed = sorted(set(c["year"] for c in critics_list))

    # Check if user has listened
    in_library = key in listened_albums
    user_relationship = None

    if in_library:
        df = state.df
        album_plays = df[
            (df["artist"].str.lower().str.strip() == key[0]) &
            (df["album"].str.lower().str.strip() == key[1])
        ]
        if len(album_plays) > 0:
            first_play = album_plays["timestamp"].min()
            total_plays = len(album_plays)

            # Find peak month
            monthly = album_plays.groupby(album_plays["timestamp"].dt.to_period("M")).size()
            peak_month = monthly.idxmax() if len(monthly) > 0 else None

            # Calculate familiarity
            familiarity_scores = data.get_album_familiarity(df)
            familiarity = familiarity_scores.get((album_data["artist"], album_data["album"]), 0)

            user_relationship = {
                "first_play": first_play.strftime("%Y-%m-%d"),
                "total_plays": total_plays,
                "peak_month": str(peak_month) if peak_month else None,
                "familiarity_score": round(familiarity, 2),
            }

    # Find similar acclaimed albums
    similar_acclaimed = []
    if state.critics_embeddings:
        norm_artist = crossref.normalize_for_matching(artist)
        if norm_artist in state.critics_embeddings.artist_to_idx:
            similar_artists = state.critics_embeddings.find_similar(artist, top_n=10)
            for sim_artist, _ in similar_artists:
                sim_artist_norm = crossref.normalize_for_matching(sim_artist)
                # Find acclaimed albums by this artist
                for other_key, other_album in album_index.items():
                    if other_key[0] == sim_artist_norm and other_key != key:
                        similar_acclaimed.append({
                            "artist": other_album["artist"],
                            "album": other_album["album"],
                            "critics": len(other_album["critics"]),
                        })
                        break
                if len(similar_acclaimed) >= 5:
                    break

    return to_serializable({
        "artist": album_data["artist"],
        "album": album_data["album"],
        "acclaimed": True,
        "total_critics": len(critics_list),
        "years_listed": years_listed,
        "critics_who_listed": [
            {
                "name": c["critic"],
                "publication": c["publication"],
                "year": c["year"],
                "rank": c["rank"],
            }
            for c in critics_list_sorted
        ],
        "in_your_library": in_library,
        "your_relationship": user_relationship,
        "similar_acclaimed": similar_acclaimed,
    })


def get_my_validated_albums(state: AnalysisState, year: Optional[int] = None, limit: int = 50) -> dict:
    """Albums where your taste matched the critics.

    Returns albums you've listened to that were also critically acclaimed,
    with rich context about your relationship with each.

    Args:
        year: Filter to albums from a specific critics year (None = all years)
        limit: Maximum albums to return
    """

    album_index = state.get_album_critics_index()
    listened_albums = state.get_listened_albums()
    df = state.df

    validated = []
    artist_counts = defaultdict(int)

    for key, album_data in album_index.items():
        if key not in listened_albums:
            continue

        critics_list = album_data["critics"]
        if year is not None:
            critics_list = [c for c in critics_list if c["year"] == year]

        if not critics_list:
            continue

        # Get user's play data
        artist_norm, album_norm = key
        album_plays = df[
            (df["artist"].str.lower().str.strip() == artist_norm) &
            (df["album"].str.lower().str.strip() == album_norm)
        ]

        if len(album_plays) == 0:
            continue

        first_play = album_plays["timestamp"].min()
        total_plays = len(album_plays)
        critics_years = sorted(set(c["year"] for c in critics_list))

        # Get top critics who listed it
        top_critics = list(set(c["critic"] for c in critics_list))[:5]

        # Check if discovered before year-end lists
        first_critics_year = min(critics_years)
        discovered_before = first_play.year < first_critics_year

        validated.append({
            "artist": album_data["artist"],
            "album": album_data["album"],
            "your_plays": total_plays,
            "your_first_play": first_play.strftime("%Y-%m-%d"),
            "critics_count": len(critics_list),
            "critics_years": critics_years,
            "top_critics": top_critics,
            "you_discovered_before_critics": discovered_before,
        })

        artist_counts[album_data["artist"]] += 1

    # Sort by play count
    validated.sort(key=lambda x: -x["your_plays"])
    validated = validated[:limit]

    # Calculate insights
    if validated:
        avg_critics = sum(v["critics_count"] for v in validated) / len(validated)
        most_validated_artist = max(artist_counts.items(), key=lambda x: x[1])[0] if artist_counts else None

        # Find most aligned year
        year_matches = defaultdict(int)
        for v in validated:
            for y in v["critics_years"]:
                year_matches[y] += 1
        most_aligned_year = max(year_matches.items(), key=lambda x: x[1])[0] if year_matches else None
    else:
        avg_critics = 0
        most_aligned_year = None
        most_validated_artist = None

    return to_serializable({
        "period": str(year) if year else "all-time",
        "total_matches": len(validated),
        "albums": validated,
        "insights": {
            "avg_critics_per_match": round(avg_critics, 1),
            "most_aligned_year": most_aligned_year,
            "your_most_validated_artist": most_validated_artist,
        },
    })


def get_critic_profile(state: AnalysisState, critic_name: str, year: Optional[int] = None) -> dict:
    """Deep dive on a specific critic's taste vs yours.

    Returns their picks, your overlap, their recommendations for you,
    and their signature artists.

    Args:
        critic_name: Name of the critic to analyze
        year: Focus on a specific year (None = all years)
    """

    critic_index = state.get_critic_picks_index()
    listened_albums = state.get_listened_albums()
    df = state.df

    # Find the critic (case-insensitive)
    critic_key = None
    for name in critic_index:
        if name.lower() == critic_name.lower():
            critic_key = name
            break

    if critic_key is None:
        # Try partial match
        for name in critic_index:
            if critic_name.lower() in name.lower():
                critic_key = name
                break

    if critic_key is None:
        return {"error": f"Critic '{critic_name}' not found. Try searching with a partial name."}

    critic_data = critic_index[critic_key]
    picks = critic_data["picks"]

    if year is not None:
        picks = [p for p in picks if p["year"] == year]
        if not picks:
            return {
                "critic": critic_key,
                "publication": critic_data["publication"],
                "error": f"No picks found for {year}",
            }

    # Calculate alignment
    heard = []
    missed = []
    for pick in picks:
        key = (
            crossref.normalize_for_matching(pick["artist"]),
            crossref.normalize_for_matching(pick["album"]),
        )
        if key in listened_albums:
            # Get play count
            album_plays = df[
                (df["artist"].str.lower().str.strip() == key[0]) &
                (df["album"].str.lower().str.strip() == key[1])
            ]
            heard.append({
                "artist": pick["artist"],
                "album": pick["album"],
                "year": pick["year"],
                "your_plays": len(album_plays),
            })
        else:
            missed.append({
                "artist": pick["artist"],
                "album": pick["album"],
                "year": pick["year"],
            })

    # Sort heard by plays, missed by year
    heard.sort(key=lambda x: -x["your_plays"])
    missed.sort(key=lambda x: -x["year"])

    # Calculate rank among all critics
    all_critics_alignment = []
    for name, cdata in critic_index.items():
        cpicks = cdata["picks"]
        if year is not None:
            cpicks = [p for p in cpicks if p["year"] == year]
        if not cpicks:
            continue

        overlap = sum(
            1 for p in cpicks
            if (crossref.normalize_for_matching(p["artist"]),
                crossref.normalize_for_matching(p["album"])) in listened_albums
        )
        all_critics_alignment.append((name, overlap / len(cpicks)))

    all_critics_alignment.sort(key=lambda x: -x[1])
    rank = next((i + 1 for i, (name, _) in enumerate(all_critics_alignment) if name == critic_key), None)

    # Find signature artists (artists this critic picks more than others)
    artist_mentions = defaultdict(int)
    for pick in picks:
        artist_mentions[pick["artist"]] += 1

    # Get artists they've listed multiple times
    signature_artists = [
        {
            "artist": artist,
            "times_listed": count,
            "you_know": any(
                (crossref.normalize_for_matching(artist), crossref.normalize_for_matching(p["album"])) in listened_albums
                for p in picks if p["artist"] == artist
            ),
        }
        for artist, count in sorted(artist_mentions.items(), key=lambda x: -x[1])
        if count >= 2
    ][:10]

    # Build picks by year
    picks_by_year = defaultdict(list)
    for pick in sorted(picks, key=lambda x: (x["year"], x["rank"] or 999)):
        picks_by_year[pick["year"]].append({
            "artist": pick["artist"],
            "album": pick["album"],
            "rank": pick["rank"],
        })

    years_active = sorted(set(p["year"] for p in critic_data["picks"]))

    return to_serializable({
        "critic": critic_key,
        "publication": critic_data["publication"],
        "years_active": years_active,
        "total_albums_picked": len(critic_data["picks"]),
        "alignment_with_you": {
            "score": round(len(heard) / (len(heard) + len(missed)), 3) if (heard or missed) else 0,
            "albums_you_heard": len(heard),
            "albums_you_missed": len(missed),
            "your_rank_among_critics": rank,
        },
        "picks_you_loved": heard[:15],
        "picks_you_missed": missed[:15],
        "their_signature_artists": signature_artists,
        "picks_by_year": dict(picks_by_year),
    })


def search_critics_for_artist(state: AnalysisState, artist: str, year: Optional[int] = None) -> dict:
    """An artist's complete critical history.

    Returns all albums by this artist that were critically acclaimed,
    which critics championed them, and your relationship with the artist.

    Args:
        artist: Artist name to search for
        year: Filter to a specific year (None = all years)
    """

    album_index = state.get_album_critics_index()
    critic_index = state.get_critic_picks_index()
    listened_albums = state.get_listened_albums()
    df = state.df

    artist_norm = crossref.normalize_for_matching(artist)

    # Find all albums by this artist in critics lists
    artist_albums = []
    for key, album_data in album_index.items():
        if key[0] != artist_norm:
            continue

        critics_list = album_data["critics"]
        if year is not None:
            critics_list = [c for c in critics_list if c["year"] == year]

        if not critics_list:
            continue

        # Check if in user's library
        in_library = key in listened_albums
        user_plays = 0
        if in_library:
            album_plays = df[
                (df["artist"].str.lower().str.strip() == key[0]) &
                (df["album"].str.lower().str.strip() == key[1])
            ]
            user_plays = len(album_plays)

        # Get year and top critics
        album_year = critics_list[0]["year"]
        top_critics = list(set(c["critic"] for c in critics_list))[:5]

        artist_albums.append({
            "album": album_data["album"],
            "year": album_year,
            "critics_count": len(critics_list),
            "top_critics": top_critics,
            "in_your_library": in_library,
            "your_plays": user_plays,
        })

    if not artist_albums:
        return {
            "artist": artist,
            "found": False,
            "message": f"No albums by '{artist}' found in critics' year-end lists",
        }

    # Sort by critics count
    artist_albums.sort(key=lambda x: -x["critics_count"])

    # Find actual artist name from data
    actual_artist = album_index.get(
        (artist_norm, crossref.normalize_for_matching(artist_albums[0]["album"])),
        {}
    ).get("artist", artist)

    # Get years listed
    years_listed = sorted(set(a["year"] for a in artist_albums))

    # Find critics who champion this artist (listed multiple albums)
    critic_artist_counts = defaultdict(int)
    for key, album_data in album_index.items():
        if key[0] != artist_norm:
            continue
        for c in album_data["critics"]:
            if year is None or c["year"] == year:
                critic_artist_counts[c["critic"]] += 1

    champions = []
    for critic_name, count in sorted(critic_artist_counts.items(), key=lambda x: -x[1]):
        if count >= 1:
            # Get critic alignment
            critic_data = critic_index.get(critic_name, {})
            picks = critic_data.get("picks", [])
            if picks:
                overlap = sum(
                    1 for p in picks
                    if (crossref.normalize_for_matching(p["artist"]),
                        crossref.normalize_for_matching(p["album"])) in listened_albums
                )
                alignment = overlap / len(picks)
            else:
                alignment = 0

            champions.append({
                "name": critic_name,
                "times_listed": count,
                "alignment_with_you": round(alignment, 3),
            })

    # Get user's relationship with artist
    artist_plays = df[df["artist"].str.lower().str.strip() == artist_norm]
    user_relationship = None
    if len(artist_plays) > 0:
        first_play = artist_plays["timestamp"].min()
        peak_year = artist_plays.groupby("year").size().idxmax()
        user_relationship = {
            "first_play": first_play.strftime("%Y-%m-%d"),
            "total_plays": len(artist_plays),
            "peak_year": int(peak_year),
        }

    return to_serializable({
        "artist": actual_artist,
        "found": True,
        "total_critical_mentions": sum(a["critics_count"] for a in artist_albums),
        "years_listed": years_listed,
        "albums_listed": artist_albums,
        "critics_who_champion": champions[:10],
        "your_relationship": user_relationship,
    })


# =============================================================================
# TRACK OBSESSION TOOLS - Identify single-track fixations
# =============================================================================

def get_obsession_tracks(
    state: AnalysisState,
    year: Optional[int] = None,
    min_plays: int = 20,
) -> dict:
    """Find tracks you obsessed over without exploring their albums.

    Returns tracks with high play counts where album familiarity is low -
    songs you put on repeat but never explored further.

    Args:
        year: Filter to specific year (None = all time)
        min_plays: Minimum plays for a track to be considered (default: 20)
    """

    df = state.df
    if year:
        df = data.filter_by_year(df, year)

    result = data.get_obsession_tracks(df, min_plays=min_plays, max_familiarity=0.4)

    if result.empty:
        return to_serializable({
            "period": year or "all-time",
            "obsession_tracks": [],
            "count": 0,
        })

    tracks = []
    for _, row in result.head(50).iterrows():
        tracks.append({
            "artist": row["artist"],
            "album": row["album"],
            "track": row["track"],
            "plays": int(row["plays"]),
            "peak_years": row.get("peak_years", []),
            "album_familiarity": round(float(row["album_familiarity"]), 3),
            "tracks_on_album": int(row["tracks_on_album"]),
            "pct_of_album_plays": round(float(row["pct_of_album_plays"]), 1),
        })

    return to_serializable({
        "period": year or "all-time",
        "obsession_tracks": tracks,
        "count": len(result),
        "insight": f"Found {len(result)} tracks with {min_plays}+ plays from low-familiarity albums",
    })


def get_one_track_artists(
    state: AnalysisState,
    year: Optional[int] = None,
    min_concentration: float = 0.7,
) -> dict:
    """Find artists where one track dominates your listening.

    Returns artists where you've only really engaged with a single song -
    your "one-hit" relationships.

    Args:
        year: Filter to specific year (None = all time)
        min_concentration: Min % of plays on top track (default: 0.7 = 70%)
    """

    df = state.df
    if year:
        df = data.filter_by_year(df, year)

    result = data.get_one_track_artists(
        df,
        min_concentration=min_concentration,
        min_top_track_plays=10,
    )

    if result.empty:
        return to_serializable({
            "period": year or "all-time",
            "one_track_artists": [],
            "count": 0,
        })

    artists = []
    for _, row in result.head(50).iterrows():
        artists.append({
            "artist": row["artist"],
            "top_track": row["top_track"],
            "top_track_album": row["top_track_album"],
            "top_track_plays": int(row["top_track_plays"]),
            "total_plays": int(row["total_plays"]),
            "other_tracks": int(row["other_tracks"]),
            "concentration": round(float(row["concentration"]), 3),
            "peak_years": row.get("peak_years", []),
            "first_year": row.get("first_year"),
            "last_year": row.get("last_year"),
        })

    return to_serializable({
        "period": year or "all-time",
        "one_track_artists": artists,
        "count": len(result),
        "insight": f"Found {len(result)} artists where {int(min_concentration*100)}%+ of plays are on one track",
    })


def get_ep_single_artists(state: AnalysisState, year: Optional[int] = None) -> dict:
    """Find artists where you mainly listen to EPs/singles, not albums.

    Returns artists who primarily release EPs and singles rather than
    traditional albums - typical for electronic producers and remixers.

    Requires MusicBrainz database to be downloaded.

    Args:
        year: Filter to specific year (None = all time)
    """

    # Check if MusicBrainz DB exists
    if not musicbrainz_db.database_exists():
        return {
            "error": "MusicBrainz database not found",
            "setup": "Run 'lastfm metadata download' to enable release type lookups",
        }

    df = state.df
    if year:
        df = data.filter_by_year(df, year)

    conn = musicbrainz_db.get_connection()

    def lookup(artist: str, album: str):
        return musicbrainz_db.lookup_release(artist, album, conn)

    result = data.get_ep_single_artists(
        df,
        musicbrainz_lookup=lookup,
        min_non_album_ratio=0.5,
        min_total_plays=20,
    )

    conn.close()

    if result.empty:
        return to_serializable({
            "period": year or "all-time",
            "ep_single_artists": [],
            "count": 0,
        })

    artists = []
    for _, row in result.head(50).iterrows():
        artists.append({
            "artist": row["artist"],
            "album_plays": int(row["album_plays"]),
            "ep_single_plays": int(row["ep_single_plays"]),
            "non_album_ratio": round(float(row["non_album_ratio"]), 3),
            "top_non_album": row["top_non_album"],
            "top_non_album_track": row["top_non_album_track"],
        })

    return to_serializable({
        "period": year or "all-time",
        "ep_single_artists": artists,
        "count": len(result),
        "insight": f"Found {len(result)} artists where 50%+ of plays come from EPs/singles",
    })


# =============================================================================
# RESOURCES - Large artifacts for context
# =============================================================================

def get_overview(state: AnalysisState) -> dict:
    """Full listening overview: stats, top artists, listening timeline."""

    df = state.df
    years = sorted(df["year"].unique())

    year_stats = []
    for year in years:
        year_df = data.filter_by_year(df, year)
        year_stats.append({
            "year": int(year),
            "plays": len(year_df),
            "artists": year_df["artist"].nunique(),
        })

    top = data.top_artists(df, limit=50)

    return to_serializable({
        "total_plays": len(df),
        "total_artists": df["artist"].nunique(),
        "date_range": {
            "first": df["timestamp"].min().isoformat(),
            "last": df["timestamp"].max().isoformat(),
        },
        "year_by_year": year_stats,
        "top_50_artists": [
            {"artist": row["artist"], "plays": int(row["plays"])}
            for _, row in top.iterrows()
        ],
    })


def get_discovered_artists(state: AnalysisState, year: int) -> list:
    """Artists first played in a given year."""

    discovered = data.artists_discovered_in_year(state.df, year)
    return to_serializable([
        {
            "artist": row["artist"],
            "first_play": row["first_play"].isoformat() if hasattr(row["first_play"], "isoformat") else str(row["first_play"]),
            "plays_that_year": int(row.get("plays_in_year", 0)),
        }
        for _, row in discovered.head(100).iterrows()
    ])


def get_critics_lists(state: AnalysisState, year: int) -> dict:
    """Critics' year-end lists for a given year."""

    critics_data = state.get_critics_data(year)
    return to_serializable({
        "year": year,
        "total_critics": len(critics_data),
        "critics": [
            {
                "name": c.get("critic", "Unknown"),
                "publication": c.get("publication", "Unknown"),
                "album_count": len(c.get("albums", [])),
            }
            for c in critics_data
        ],
    })
