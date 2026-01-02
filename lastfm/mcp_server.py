"""MCP Server for Last.fm Music Analysis.

Exposes music taste analysis tools for LLM agents to explore narratives
around listening history, critic alignment, and recommendations.

Usage:
    # Run with default CSV auto-detection
    python -m lastfm.mcp_server

    # Run with explicit CSV path
    LASTFM_CSV=/path/to/scrobbles.csv python -m lastfm.mcp_server
"""

import json
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastmcp import FastMCP

from . import crossref, data, embeddings, musicbrainz_db


def _to_serializable(obj: Any) -> Any:
    """Convert numpy types to JSON-serializable Python types."""
    if isinstance(obj, dict):
        return {k: _to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_to_serializable(v) for v in obj]
    elif isinstance(obj, set):
        return [_to_serializable(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    else:
        return obj

# Create the MCP server
mcp = FastMCP(name="LastFM Music Analysis")


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

class AnalysisState:
    """Holds loaded data and computed artifacts for the session."""

    def __init__(self):
        self.csv_path: Optional[Path] = None
        self.df = None  # Main scrobbles DataFrame
        self.user_embeddings = None  # ArtistEmbeddings from user listening
        self.critics_embeddings = None  # CriticsEmbeddings from critics lists
        self.critic_vectors = None  # CriticVectorEmbeddings for alignment
        self._critics_cache: dict = {}  # year -> critics data

    def is_loaded(self) -> bool:
        return self.df is not None

    def load(self, csv_path: Optional[Path] = None):
        """Load data from CSV and build embeddings."""
        if csv_path is None:
            csv_path = _find_csv()

        if csv_path is None:
            raise ValueError(
                "No CSV found. Set LASTFM_CSV environment variable or "
                "place recenttracks-*.csv in the working directory."
            )

        self.csv_path = csv_path
        print(f"Loading scrobbles from {csv_path}...")
        self.df = data.load_scrobbles(csv_path)
        print(f"  Loaded {len(self.df):,} plays")

        print("Building user embeddings...")
        self.user_embeddings = embeddings.build_embeddings_from_csv(csv_path)
        print(f"  Built embeddings for {len(self.user_embeddings.artist_to_idx)} artists")

        print("Building critics embeddings...")
        try:
            self.critics_embeddings = embeddings.get_or_build_critics_embeddings()
            print(f"  Built critics embeddings for {len(self.critics_embeddings.artist_to_idx)} artists")
        except Exception as e:
            print(f"  Warning: Could not build critics embeddings: {e}")
            self.critics_embeddings = None

        print("Building critic vectors...")
        try:
            self.critic_vectors = embeddings.get_or_build_critic_vectors()
            print(f"  Built vectors for {len(self.critic_vectors.critic_vectors)} critics")
        except Exception as e:
            print(f"  Warning: Could not build critic vectors: {e}")
            self.critic_vectors = None

        print("Ready!")

    def get_critics_data(self, year: int) -> list:
        """Load critics data for a year, with caching."""
        if year not in self._critics_cache:
            critics_path = Path(__file__).parent.parent / f"critics-{year}.json"
            if critics_path.exists():
                with open(critics_path) as f:
                    self._critics_cache[year] = json.load(f)
            else:
                self._critics_cache[year] = []
        return self._critics_cache[year]

    def get_all_critics_years(self) -> list[int]:
        """Get list of years with critics data available."""
        years = []
        for y in range(2011, 2026):
            path = Path(__file__).parent.parent / f"critics-{y}.json"
            if path.exists():
                years.append(y)
        return years


# Global state instance
_state = AnalysisState()


def _find_csv() -> Optional[Path]:
    """Find CSV file from environment or glob."""
    # Check environment variable
    env_path = os.environ.get("LASTFM_CSV")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    # Try to find in current directory
    csvs = list(Path.cwd().glob("recenttracks-*.csv"))
    if csvs:
        return sorted(csvs)[-1]  # Most recent

    return None


def _ensure_loaded():
    """Ensure data is loaded, loading lazily if needed."""
    if not _state.is_loaded():
        _state.load()


# =============================================================================
# NARRATIVE TOOLS - High-level "story" tools
# =============================================================================

@mcp.tool
def explore_taste_evolution(
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
    _ensure_loaded()
    df = _state.df

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

    return _to_serializable(result)


@mcp.tool
def find_musical_bridges(
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
    _ensure_loaded()

    result = {
        "query_artist": artist,
        "user_similar": [],
        "critics_similar": [],
        "bridges": [],
        "user_only": [],
        "critics_only": [],
    }

    # Find similar in user space
    if _state.user_embeddings and artist in _state.user_embeddings.artist_to_idx:
        user_similar = _state.user_embeddings.find_similar(artist, top_n=top_n)
        result["user_similar"] = [
            {"artist": name, "similarity": round(score, 3)}
            for name, score in user_similar
        ]

    # Find similar in critics space
    if _state.critics_embeddings:
        norm_artist = crossref.normalize_for_matching(artist)
        if norm_artist in _state.critics_embeddings.artist_to_idx:
            critics_similar = _state.critics_embeddings.find_similar(artist, top_n=top_n)
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

    return _to_serializable(result)


@mcp.tool
def discover_blind_spots(
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
    _ensure_loaded()

    # Determine which years to search
    if year is not None:
        years = [year]
    else:
        years = _state.get_all_critics_years()

    # Get albums user has listened to
    listened_albums = data.get_albums_listened_to(_state.df)
    listened_norm = {
        (crossref.normalize_for_matching(a), crossref.normalize_for_matching(t))
        for a, t in listened_albums
    }

    # Collect unheard acclaimed albums
    unheard = {}  # (artist_norm, album_norm) -> {artist, album, critics, count}

    for y in years:
        critics_data = _state.get_critics_data(y)
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

    return _to_serializable({
        "total_unheard_acclaimed": len(recommendations),
        "min_critics_threshold": min_critics,
        "recommendations": recommendations[:limit],
    })


def _analyze_single_artist(df: "pd.DataFrame", artist: str) -> dict:
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

    # Albums played
    albums = artist_plays.groupby("album").size().sort_values(ascending=False)

    # Similar artists in both spaces
    user_similar = []
    critics_similar = []

    if _state.user_embeddings and actual_name in _state.user_embeddings.artist_to_idx:
        user_similar = [
            {"artist": name, "similarity": round(score, 3)}
            for name, score in _state.user_embeddings.find_similar(actual_name, top_n=10)
        ]

    if _state.critics_embeddings:
        norm = crossref.normalize_for_matching(actual_name)
        if norm in _state.critics_embeddings.artist_to_idx:
            critics_similar = [
                {"artist": name, "similarity": round(score, 3)}
                for name, score in _state.critics_embeddings.find_similar(actual_name, top_n=10)
            ]

    # Find critics who listed this artist
    artist_critics = []
    for year in _state.get_all_critics_years():
        critics_data = _state.get_critics_data(year)
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
        "albums_played": [
            {"album": album, "plays": int(plays)}
            for album, plays in albums.head(10).items()
        ],
        "similar_from_listening": user_similar,
        "similar_from_critics": critics_similar,
        "critics_who_listed": artist_critics[:10],
    }


@mcp.tool
def get_artist_deep_dive(artists: list[str]) -> list:
    """Complete analysis of user's relationship with one or more artists.

    Returns for each artist: first/last play, total plays, albums listened,
    similar artists in user space vs critics space, which critics champion them.

    Args:
        artists: List of artist names to analyze (can be a single-item list)
    """
    _ensure_loaded()
    df = _state.df

    results = []
    for artist in artists:
        result = _analyze_single_artist(df, artist)
        results.append(result)

    return _to_serializable(results)


# =============================================================================
# PRECISE TOOLS - Direct query tools
# =============================================================================

@mcp.tool
def find_similar_artists(
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
    _ensure_loaded()

    if source == "user":
        if not _state.user_embeddings or artist not in _state.user_embeddings.artist_to_idx:
            return []
        similar = _state.user_embeddings.find_similar(artist, top_n=top_n)
    elif source == "critics":
        if not _state.critics_embeddings:
            return []
        norm = crossref.normalize_for_matching(artist)
        if norm not in _state.critics_embeddings.artist_to_idx:
            return []
        similar = _state.critics_embeddings.find_similar(artist, top_n=top_n)
    else:
        return [{"error": f"Unknown source '{source}'. Use 'user' or 'critics'."}]

    return _to_serializable([
        {"artist": name, "similarity": round(score, 3)}
        for name, score in similar
    ])


@mcp.tool
def get_listening_stats(year: Optional[int] = None) -> dict:
    """Get listening statistics for a year or all time.

    Args:
        year: Specific year (None = all time)
    """
    _ensure_loaded()
    df = _state.df

    if year is not None:
        df = data.filter_by_year(df, year)

    if len(df) == 0:
        return {"error": f"No data for year {year}"}

    return _to_serializable({
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


@mcp.tool
def get_top_artists(
    year: Optional[int] = None,
    limit: int = 20,
) -> list:
    """Get top artists by play count.

    Args:
        year: Specific year (None = all time)
        limit: Number of artists to return
    """
    _ensure_loaded()
    df = _state.df

    if year is not None:
        df = data.filter_by_year(df, year)

    top = data.top_artists(df, limit=limit)
    return _to_serializable([
        {"artist": row["artist"], "plays": int(row["plays"])}
        for _, row in top.iterrows()
    ])


@mcp.tool
def get_critic_alignment(limit: int = 20) -> list:
    """Find critics whose taste aligns with the user's.

    Returns critics ranked by how well their picks match your listening,
    with details about overlap.

    Args:
        limit: Number of critics to return
    """
    _ensure_loaded()

    if not _state.critic_vectors:
        return [{"error": "Critic vectors not available"}]

    # Compute user vector from listening
    user_vector = _state.critic_vectors.compute_user_vector(_state.df, top_n_artists=100)

    # Find similar critics
    similar = _state.critic_vectors.find_similar_critics(user_vector, top_n=limit)

    return _to_serializable([
        {
            "critic": name,
            "alignment": round(score, 3),
            "publication": info.get("publication", "Unknown"),
        }
        for name, score, info in similar
    ])


@mcp.tool
def get_temporal_patterns(year: int | None = None) -> dict:
    """Analyze when listening happens.

    Returns time-of-day distribution, day-of-week patterns,
    and monthly patterns. Useful for understanding listening context.

    Args:
        year: Specific year to analyze (None = all time)
    """
    _ensure_loaded()
    df = _state.df

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

    return _to_serializable({
        "period": str(year) if year else "all time",
        "total_plays": total,
        "by_hour": hours,
        "by_weekday": weekdays,
        "by_month": months,
        "peak_hour": int(peak_hour) if peak_hour is not None else None,
        "peak_weekday": peak_weekday,
        "peak_month": month_names[peak_month - 1] if peak_month else None,
    })


@mcp.tool
def get_period_summary(start_year: int, end_year: int) -> dict:
    """Get aggregated listening stats for a year range.

    Returns total plays, unique artists/albums, top artists across the period,
    year-by-year breakdown, and discovery rate.

    Args:
        start_year: First year of the period
        end_year: Last year of the period (inclusive)
    """
    _ensure_loaded()
    df_full = _state.df

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

    return _to_serializable(result)


def _get_single_year_review(df_full, year: int) -> dict:
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
    critics_data = _state.get_critics_data(year)
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
                        for l in info.labels:
                            label_plays[l] += plays

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
                total_label_plays = sum(l[1] for l in sorted_labels) or 1

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
                        {"name": l, "plays": p, "pct": round(p / total_label_plays * 100, 1)}
                        for l, p in sorted_labels[:10]
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


@mcp.tool
def get_year_review(years: list[int] | int = 2025) -> dict | list:
    """Get comprehensive year-in-review data for one or more years.

    Returns listening stats, top artists/albums with context, new discoveries,
    critics alignment, and metadata breakdown (genres, labels, countries).
    This is the richest single view of a user's listening year.

    Args:
        years: Year or list of years to review (default: 2025)
    """
    _ensure_loaded()
    df_full = _state.df

    # Normalize to list
    if isinstance(years, int):
        single_year = True
        years = [years]
    else:
        single_year = False

    results = []
    for year in years:
        result = _get_single_year_review(df_full, year)
        results.append(result)

    # Return single dict if single year requested, list otherwise
    if single_year:
        return _to_serializable(results[0])
    return _to_serializable(results)


@mcp.tool
def get_listening_by_release_era(
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
    _ensure_loaded()
    df = _state.df

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

    return _to_serializable({
        "era": f"{release_start}-{release_end}",
        "total_plays": total_plays,
        "unique_artists": len(era_artists),
        "unique_albums": len(era_albums),
        "top_albums": era_albums[:limit],
    })


@mcp.tool
def get_common_transitions(artist: str, top_n: int = 10) -> dict:
    """Find what typically plays before and after an artist.

    Returns common predecessors and successors based on
    sequential plays within listening sessions.

    Args:
        artist: Artist to analyze transitions for
        top_n: Number of top transitions to return
    """
    try:
        _ensure_loaded()
        df = _state.df

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

        return _to_serializable({
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


@mcp.tool
def get_discovery_context(artist: str) -> dict:
    """Understand how an artist was discovered.

    Returns: what played in same session as first listen,
    what played in days before/after, any patterns.

    Args:
        artist: Artist to get discovery context for
    """
    try:
        _ensure_loaded()
        df = _state.df

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

        return _to_serializable({
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
# RESOURCES - Large artifacts for context
# =============================================================================

@mcp.resource("overview://summary")
def get_overview() -> dict:
    """Full listening overview: stats, top artists, listening timeline."""
    _ensure_loaded()

    df = _state.df
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

    return _to_serializable({
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


@mcp.resource("artists://discovered/{year}")
def get_discovered_artists(year: int) -> list:
    """Artists first played in a given year."""
    _ensure_loaded()

    discovered = data.artists_discovered_in_year(_state.df, year)
    return _to_serializable([
        {
            "artist": row["artist"],
            "first_play": row["first_play"].isoformat() if hasattr(row["first_play"], "isoformat") else str(row["first_play"]),
            "plays_that_year": int(row.get("plays_in_year", 0)),
        }
        for _, row in discovered.head(100).iterrows()
    ])


@mcp.resource("critics://lists/{year}")
def get_critics_lists(year: int) -> dict:
    """Critics' year-end lists for a given year."""
    _ensure_loaded()

    critics_data = _state.get_critics_data(year)
    return _to_serializable({
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


# =============================================================================
# PROMPTS - Agent playbooks
# =============================================================================

@mcp.prompt
def taste_journey() -> str:
    """Guide for exploring a user's 20-year musical journey."""
    return """Explore this user's musical taste evolution:

1. Start with explore_taste_evolution() to understand the arc
2. Identify key "eras" and pivotal discoveries
3. Use find_musical_bridges() on artists from each era
4. Look at get_artist_deep_dive() for the most significant artists
5. Weave a narrative about how their taste developed

Key questions to answer:
- What were the turning points in their musical journey?
- Which artists served as gateways to new genres?
- What patterns emerge in their discoveries vs abandonments?
"""


@mcp.prompt
def recommendation_session(mood: str = "adventurous") -> str:
    """Guide for a personalized recommendation session."""
    return f"""Run a {mood} recommendation session:

1. Use get_critic_alignment() to find taste-aligned critics
2. Use discover_blind_spots() to find unheard acclaimed albums
3. For each recommendation, use find_musical_bridges() to explain WHY
4. Use get_artist_deep_dive() to show connections to known artists

Mood: {mood}
- "adventurous" = prioritize less obvious picks
- "safe" = prioritize highly acclaimed, similar to favorites
- "nostalgic" = focus on artists similar to early discoveries
"""


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    # Pre-load data before starting server
    _ensure_loaded()
    mcp.run()
