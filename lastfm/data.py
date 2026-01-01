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
