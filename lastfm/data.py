"""Data loading and parsing for Last.fm exports."""

import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from functools import lru_cache


def load_scrobbles(csv_path: Path) -> pd.DataFrame:
    """Load scrobbles from a Last.fm export CSV.

    Returns a DataFrame with parsed timestamps and cleaned data.
    """
    df = pd.read_csv(
        csv_path,
        dtype={
            "uts": int,
            "utc_time": str,
            "artist": str,
            "artist_mbid": str,
            "album": str,
            "album_mbid": str,
            "track": str,
            "track_mbid": str,
        },
    )

    # Convert Unix timestamp to datetime
    df["timestamp"] = pd.to_datetime(df["uts"], unit="s", utc=True)

    # Extract useful time components
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    df["day"] = df["timestamp"].dt.day
    df["hour"] = df["timestamp"].dt.hour
    df["weekday"] = df["timestamp"].dt.day_name()

    # Fill NaN in string columns with empty string
    for col in ["artist_mbid", "album_mbid", "track_mbid", "album"]:
        df[col] = df[col].fillna("")

    return df


def filter_by_date_range(
    df: pd.DataFrame,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Filter scrobbles to a date range."""
    if start:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        df = df[df["timestamp"] >= start]
    if end:
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        df = df[df["timestamp"] <= end]
    return df


def filter_by_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Filter scrobbles to a specific year."""
    return df[df["year"] == year]


def top_artists(df: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    """Get top artists by play count."""
    counts = df.groupby("artist").size().reset_index(name="plays")
    return counts.sort_values("plays", ascending=False).head(limit)


def top_albums(df: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    """Get top albums by play count."""
    # Filter out empty albums
    df_with_albums = df[df["album"] != ""]
    counts = df_with_albums.groupby(["artist", "album"]).size().reset_index(name="plays")
    return counts.sort_values("plays", ascending=False).head(limit)


def top_tracks(df: pd.DataFrame, limit: int = 20) -> pd.DataFrame:
    """Get top tracks by play count."""
    counts = df.groupby(["artist", "track"]).size().reset_index(name="plays")
    return counts.sort_values("plays", ascending=False).head(limit)


def first_plays(df: pd.DataFrame) -> pd.DataFrame:
    """Get the first play of each artist."""
    # Sort by timestamp ascending to get earliest plays first
    sorted_df = df.sort_values("timestamp")
    first = sorted_df.groupby("artist").first().reset_index()
    return first[["artist", "timestamp", "track", "album"]].sort_values("timestamp")


def last_plays(df: pd.DataFrame) -> pd.DataFrame:
    """Get the last play of each artist."""
    # Sort by timestamp descending to get most recent plays first
    sorted_df = df.sort_values("timestamp", ascending=False)
    last = sorted_df.groupby("artist").first().reset_index()
    return last[["artist", "timestamp", "track", "album"]].sort_values("timestamp", ascending=False)


def artists_discovered_in_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Find artists first played in a given year.

    Returns artists whose first ever scrobble was in the specified year.
    """
    first = first_plays(df)
    first["discovery_year"] = first["timestamp"].dt.year
    discovered = first[first["discovery_year"] == year].copy()

    # Add play counts for these artists in the discovery year
    year_df = filter_by_year(df, year)
    year_counts = year_df.groupby("artist").size().reset_index(name="plays_in_year")

    discovered = discovered.merge(year_counts, on="artist", how="left")
    return discovered.sort_values("plays_in_year", ascending=False)


def artists_abandoned_in_year(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Find artists last played in a given year.

    Returns artists whose last ever scrobble was in the specified year.
    This reveals artists you stopped listening to in that year.
    """
    last = last_plays(df)
    last["abandon_year"] = last["timestamp"].dt.year
    abandoned = last[last["abandon_year"] == year].copy()

    # Add total lifetime play counts for these artists
    total_counts = df.groupby("artist").size().reset_index(name="total_plays")
    abandoned = abandoned.merge(total_counts, on="artist", how="left")

    # Add play counts in the abandon year
    year_df = filter_by_year(df, year)
    year_counts = year_df.groupby("artist").size().reset_index(name="plays_in_year")
    abandoned = abandoned.merge(year_counts, on="artist", how="left")

    return abandoned.sort_values("total_plays", ascending=False)


def new_artists_in_period(
    df: pd.DataFrame,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Find artists first played within a date range."""
    first = first_plays(df)

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    mask = (first["timestamp"] >= start) & (first["timestamp"] <= end)
    return first[mask].sort_values("timestamp", ascending=False)


def get_albums_listened_to(
    df: pd.DataFrame,
    min_unique_tracks: int = 5,
    min_plays_per_track: int = 5,
) -> set[tuple[str, str]]:
    """Get albums that have been properly listened to.

    An album is considered "listened to" only if you've played at least
    min_unique_tracks different tracks from it, each at least min_plays_per_track times.

    This prevents albums from being counted as "heard" when you've only
    played one or two tracks from them once or twice.

    Args:
        df: DataFrame of scrobbles
        min_unique_tracks: Minimum number of different tracks required (default: 5)
        min_plays_per_track: Minimum plays required per track (default: 5)

    Returns:
        Set of (artist, album) tuples that meet the criteria
    """
    # Filter to rows with albums
    df_albums = df[df["album"] != ""].copy()

    # Group by artist/album/track and count plays per track
    track_plays = (
        df_albums.groupby(["artist", "album", "track"])
        .size()
        .reset_index(name="plays")
    )

    # Filter to tracks played at least min_plays_per_track times
    qualified_tracks = track_plays[track_plays["plays"] >= min_plays_per_track]

    # Count how many qualified tracks per album
    qualified_track_counts = (
        qualified_tracks.groupby(["artist", "album"])
        .size()
        .reset_index(name="qualified_tracks")
    )

    # Filter to albums with at least min_unique_tracks qualified tracks
    listened_albums = qualified_track_counts[
        qualified_track_counts["qualified_tracks"] >= min_unique_tracks
    ]

    # Return as set of tuples for easy membership testing
    return set(
        zip(listened_albums["artist"], listened_albums["album"])
    )


def get_album_familiarity(
    df: pd.DataFrame,
    coverage_weight: float = 0.4,
    depth_weight: float = 0.4,
    dispersion_weight: float = 0.2,
    max_tracks_for_coverage: int = 10,
    max_avg_plays_for_depth: int = 10,
) -> dict[tuple[str, str], float]:
    """Calculate continuous familiarity score (0-1) for each album.

    Replaces the binary 5x5 rule with a smooth score based on:
    - Coverage: How many different tracks you've played (0-1)
    - Depth: Average plays per track (0-1)
    - Dispersion: How evenly distributed plays are across tracks (0-1)

    Args:
        df: DataFrame of scrobbles
        coverage_weight: Weight for track coverage component (default: 0.4)
        depth_weight: Weight for play depth component (default: 0.4)
        dispersion_weight: Weight for play dispersion component (default: 0.2)
        max_tracks_for_coverage: Tracks needed for full coverage score (default: 10)
        max_avg_plays_for_depth: Avg plays/track for full depth score (default: 10)

    Returns:
        Dict mapping (artist, album) -> familiarity score (0.0 to 1.0)

    Example scores:
        - 10+ tracks, 10+ avg plays, even distribution: ~1.0
        - 5 tracks, 5 avg plays, even distribution: ~0.5 (old 5x5 threshold)
        - 2 tracks, 3 avg plays, uneven: ~0.2
        - 1 track, 1 play: ~0.1
    """
    import numpy as np

    # Filter to rows with albums
    df_albums = df[df["album"] != ""].copy()

    if df_albums.empty:
        return {}

    # Group by artist/album/track and count plays per track
    track_plays = (
        df_albums.groupby(["artist", "album", "track"])
        .size()
        .reset_index(name="plays")
    )

    # Aggregate at album level
    album_stats = track_plays.groupby(["artist", "album"]).agg(
        unique_tracks=("track", "count"),
        total_plays=("plays", "sum"),
        play_list=("plays", list),  # Keep individual track plays for dispersion
    ).reset_index()

    familiarity_scores = {}

    for _, row in album_stats.iterrows():
        artist = row["artist"]
        album = row["album"]
        unique_tracks = row["unique_tracks"]
        total_plays = row["total_plays"]
        play_list = row["play_list"]

        # Component 1: Track coverage (0-1)
        # Full credit at max_tracks_for_coverage tracks
        coverage = min(unique_tracks / max_tracks_for_coverage, 1.0)

        # Component 2: Play depth (0-1)
        # Average plays per track, capped at max_avg_plays_for_depth
        avg_plays = total_plays / unique_tracks if unique_tracks > 0 else 0
        depth = min(avg_plays / max_avg_plays_for_depth, 1.0)

        # Component 3: Dispersion (0-1)
        # Use normalized entropy: how evenly distributed are plays across tracks?
        # High entropy = even distribution = good
        # Low entropy = concentrated on few tracks = less familiar with album as a whole
        if unique_tracks > 1:
            plays_array = np.array(play_list, dtype=float)
            probs = plays_array / plays_array.sum()
            # Shannon entropy
            entropy = -np.sum(probs * np.log2(probs + 1e-10))
            # Normalize by max entropy (uniform distribution)
            max_entropy = np.log2(unique_tracks)
            dispersion = entropy / max_entropy if max_entropy > 0 else 1.0
        else:
            # Only 1 track: dispersion is perfect (can't be more even)
            dispersion = 1.0

        # Combine components with weights
        familiarity = (
            coverage_weight * coverage +
            depth_weight * depth +
            dispersion_weight * dispersion
        )

        familiarity_scores[(artist, album)] = round(familiarity, 4)

    return familiarity_scores


def get_albums_by_familiarity(
    df: pd.DataFrame,
    min_familiarity: float = 0.5,
    **kwargs,
) -> set[tuple[str, str]]:
    """Get albums meeting a minimum familiarity threshold.

    Drop-in replacement for get_albums_listened_to() using continuous scoring.

    Args:
        df: DataFrame of scrobbles
        min_familiarity: Minimum familiarity score (0-1) to count as "listened"
                        Default 0.5 roughly corresponds to old 5x5 rule
        **kwargs: Additional arguments passed to get_album_familiarity()

    Returns:
        Set of (artist, album) tuples meeting the threshold
    """
    familiarity = get_album_familiarity(df, **kwargs)
    return {album for album, score in familiarity.items() if score >= min_familiarity}


def get_listened_albums(
    df: pd.DataFrame,
    min_familiarity: float | None = None,
) -> set[tuple[str, str]]:
    """Get albums you've listened to, using either binary or familiarity scoring.

    This is the recommended function for commands to use. It checks if a
    familiarity threshold is provided and uses the appropriate method.

    Args:
        df: DataFrame of scrobbles
        min_familiarity: If provided, use continuous familiarity scoring.
                        If None, use binary 5x5 rule.
                        Recommended value: 0.4

    Returns:
        Set of (artist, album) tuples that count as "listened to"
    """
    if min_familiarity is not None:
        return get_albums_by_familiarity(df, min_familiarity=min_familiarity)
    else:
        return get_albums_listened_to(df)


def detect_sessions(
    df: pd.DataFrame,
    gap_minutes: int = 30,
) -> pd.DataFrame:
    """Add session_id column based on time gaps between plays.

    A new session starts when the gap between consecutive plays exceeds gap_minutes.
    This captures intentional listening sessions rather than arbitrary time windows.

    Args:
        df: DataFrame with 'timestamp' column
        gap_minutes: Minutes of inactivity to define session boundary (default: 30)

    Returns:
        DataFrame with added 'session_id' column (sorted by timestamp)
    """
    df_sorted = df.sort_values("timestamp").copy()
    time_gaps = df_sorted["timestamp"].diff().dt.total_seconds() / 60
    is_new_session = (time_gaps > gap_minutes) | time_gaps.isna()
    df_sorted["session_id"] = is_new_session.cumsum()
    return df_sorted


def get_session_stats(df: pd.DataFrame, gap_minutes: int = 30) -> dict:
    """Get statistics about detected sessions.

    Args:
        df: DataFrame with 'timestamp' column
        gap_minutes: Minutes of inactivity to define session boundary

    Returns:
        Dict with session statistics
    """
    df_sessions = detect_sessions(df, gap_minutes=gap_minutes)

    # Group by session
    session_groups = df_sessions.groupby("session_id")

    # Calculate stats
    total_sessions = df_sessions["session_id"].nunique()
    tracks_per_session = session_groups.size()
    artists_per_session = session_groups["artist"].nunique()

    # Session durations (first to last track in session)
    session_durations = session_groups["timestamp"].agg(lambda x: (x.max() - x.min()).total_seconds() / 60)

    return {
        "total_sessions": total_sessions,
        "total_tracks": len(df_sessions),
        "avg_tracks_per_session": tracks_per_session.mean(),
        "median_tracks_per_session": tracks_per_session.median(),
        "avg_artists_per_session": artists_per_session.mean(),
        "median_artists_per_session": artists_per_session.median(),
        "avg_session_duration_minutes": session_durations.mean(),
        "median_session_duration_minutes": session_durations.median(),
        "single_track_sessions": (tracks_per_session == 1).sum(),
        "multi_artist_sessions": (artists_per_session > 1).sum(),
    }
