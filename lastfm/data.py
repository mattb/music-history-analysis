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


def get_album_familiarity_details(
    df: pd.DataFrame,
    coverage_weight: float = 0.4,
    depth_weight: float = 0.4,
    dispersion_weight: float = 0.2,
    max_tracks_for_coverage: int = 10,
    max_avg_plays_for_depth: int = 10,
) -> dict[tuple[str, str], dict]:
    """Get detailed familiarity breakdown for each album.

    Returns the component scores (coverage, depth, dispersion) along with
    the final familiarity score, useful for understanding WHY an album
    has a particular familiarity level.

    Args:
        df: DataFrame of scrobbles
        coverage_weight: Weight for track coverage component (default: 0.4)
        depth_weight: Weight for play depth component (default: 0.4)
        dispersion_weight: Weight for play dispersion component (default: 0.2)
        max_tracks_for_coverage: Tracks needed for full coverage score (default: 10)
        max_avg_plays_for_depth: Avg plays/track for full depth score (default: 10)

    Returns:
        Dict mapping (artist, album) -> {
            "familiarity": float,  # Final score 0-1
            "coverage": float,     # Track coverage 0-1
            "depth": float,        # Play depth 0-1
            "dispersion": float,   # Play distribution evenness 0-1
            "unique_tracks": int,  # Number of different tracks played
            "total_plays": int,    # Total plays across all tracks
            "avg_plays_per_track": float,
        }
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
        play_list=("plays", list),
    ).reset_index()

    results = {}

    for _, row in album_stats.iterrows():
        artist = row["artist"]
        album = row["album"]
        unique_tracks = row["unique_tracks"]
        total_plays = row["total_plays"]
        play_list = row["play_list"]

        # Component 1: Track coverage (0-1)
        coverage = min(unique_tracks / max_tracks_for_coverage, 1.0)

        # Component 2: Play depth (0-1)
        avg_plays = total_plays / unique_tracks if unique_tracks > 0 else 0
        depth = min(avg_plays / max_avg_plays_for_depth, 1.0)

        # Component 3: Dispersion (0-1)
        if unique_tracks > 1:
            plays_array = np.array(play_list, dtype=float)
            probs = plays_array / plays_array.sum()
            entropy = -np.sum(probs * np.log2(probs + 1e-10))
            max_entropy = np.log2(unique_tracks)
            dispersion = entropy / max_entropy if max_entropy > 0 else 1.0
        else:
            dispersion = 1.0

        # Final familiarity score
        familiarity = (
            coverage_weight * coverage +
            depth_weight * depth +
            dispersion_weight * dispersion
        )

        results[(artist, album)] = {
            "familiarity": round(familiarity, 4),
            "coverage": round(coverage, 4),
            "depth": round(depth, 4),
            "dispersion": round(dispersion, 4),
            "unique_tracks": unique_tracks,
            "total_plays": total_plays,
            "avg_plays_per_track": round(avg_plays, 2),
        }

    return results


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


def get_obsession_tracks(
    df: pd.DataFrame,
    min_plays: int = 20,
    max_familiarity: float = 0.4,
    max_tracks_heard: int = 3,
    min_track_dominance: float = 0.5,
) -> pd.DataFrame:
    """Find tracks with high plays from albums with low familiarity.

    These are "obsession tracks" - songs you put on repeat without
    exploring the rest of the album.

    A track qualifies as an obsession if ANY of these are true:
    1. Album familiarity is low (< max_familiarity)
    2. You've only heard a few tracks from the album (< max_tracks_heard)
       AND this track dominates plays (> min_track_dominance)

    Args:
        df: DataFrame of scrobbles
        min_plays: Minimum plays for a track to be considered (default: 20)
        max_familiarity: Maximum album familiarity score (default: 0.4)
        max_tracks_heard: Max tracks heard to count as "unexplored" (default: 3)
        min_track_dominance: Min % of album plays for single-track obsession (default: 0.5)

    Returns:
        DataFrame with columns:
        - artist, album, track, plays
        - album_familiarity: Score 0-1 for the album
        - tracks_on_album: How many tracks you've played from this album
        - pct_of_album_plays: What % of album plays this track represents
        - peak_years: Dominant year(s) of listening (years with 25%+ of plays)
    """
    # Filter to rows with albums
    df_albums = df[df["album"] != ""].copy()

    if df_albums.empty:
        return pd.DataFrame()

    # Get album familiarity scores
    familiarity = get_album_familiarity(df)

    # Get track-level play counts
    track_plays = (
        df_albums.groupby(["artist", "album", "track"])
        .size()
        .reset_index(name="plays")
    )

    # Get year breakdown for each track (for peak years calculation)
    track_years = (
        df_albums.groupby(["artist", "album", "track", "year"])
        .size()
        .reset_index(name="year_plays")
    )

    # Get album-level stats
    album_stats = track_plays.groupby(["artist", "album"]).agg(
        tracks_on_album=("track", "count"),
        total_album_plays=("plays", "sum"),
    ).reset_index()

    # Merge track plays with album stats
    result = track_plays.merge(album_stats, on=["artist", "album"])

    # Add familiarity scores
    result["album_familiarity"] = result.apply(
        lambda r: familiarity.get((r["artist"], r["album"]), 0.0), axis=1
    )

    # Calculate % of album plays
    result["pct_of_album_plays"] = (
        result["plays"] / result["total_album_plays"] * 100
    ).round(1)

    # Filter to obsession tracks:
    # Either low familiarity OR (few tracks heard AND this track dominates)
    obsessions = result[
        (result["plays"] >= min_plays) &
        (
            (result["album_familiarity"] < max_familiarity) |
            (
                (result["tracks_on_album"] <= max_tracks_heard) &
                (result["pct_of_album_plays"] >= min_track_dominance * 100)
            )
        )
    ].copy()

    # Calculate peak years for each obsession track
    def get_peak_years(row):
        track_year_data = track_years[
            (track_years["artist"] == row["artist"]) &
            (track_years["album"] == row["album"]) &
            (track_years["track"] == row["track"])
        ]
        if track_year_data.empty:
            return []
        total = row["plays"]
        peak_threshold = total * 0.25
        peaks = track_year_data[track_year_data["year_plays"] >= peak_threshold]
        return sorted(peaks["year"].astype(int).tolist())

    obsessions["peak_years"] = obsessions.apply(get_peak_years, axis=1)

    # Sort by plays descending
    obsessions = obsessions.sort_values("plays", ascending=False)

    # Select and reorder columns
    return obsessions[[
        "artist", "album", "track", "plays",
        "album_familiarity", "tracks_on_album", "pct_of_album_plays", "peak_years"
    ]]


def get_one_track_artists(
    df: pd.DataFrame,
    min_concentration: float = 0.7,
    max_other_tracks: int = 3,
    min_top_track_plays: int = 10,
) -> pd.DataFrame:
    """Find artists where one track dominates all plays.

    These are "one-track artists" - artists where you've only really
    engaged with a single song.

    Args:
        df: DataFrame of scrobbles
        min_concentration: Min % of plays on top track (default: 0.7 = 70%)
        max_other_tracks: Max other tracks played (default: 3)
        min_top_track_plays: Min plays on the top track (default: 10)

    Returns:
        DataFrame with columns:
        - artist, top_track, top_track_album
        - top_track_plays, total_plays, other_tracks
        - concentration: % of plays on top track
        - peak_years: Dominant year(s) of listening (years with 25%+ of plays)
        - first_year, last_year: Range of listening
    """
    # Get track-level play counts per artist
    track_plays = (
        df.groupby(["artist", "track", "album"])
        .size()
        .reset_index(name="plays")
    )

    # For each artist, find their top track and stats
    artist_stats = []

    for artist in track_plays["artist"].unique():
        artist_tracks = track_plays[track_plays["artist"] == artist].copy()
        artist_tracks = artist_tracks.sort_values("plays", ascending=False)

        total_plays = artist_tracks["plays"].sum()
        top_row = artist_tracks.iloc[0]
        top_track = top_row["track"]
        top_track_album = top_row["album"]
        top_track_plays = top_row["plays"]
        other_tracks = len(artist_tracks) - 1

        concentration = top_track_plays / total_plays if total_plays > 0 else 0

        # Get year breakdown for the top track
        top_track_plays_df = df[
            (df["artist"] == artist) & (df["track"] == top_track)
        ]
        year_counts = top_track_plays_df.groupby("year").size()

        # Find peak years (years with 25%+ of the track's plays)
        peak_threshold = top_track_plays * 0.25
        peak_years = sorted([
            int(y) for y, c in year_counts.items() if c >= peak_threshold
        ])

        # Get year range
        first_year = int(year_counts.index.min()) if len(year_counts) > 0 else None
        last_year = int(year_counts.index.max()) if len(year_counts) > 0 else None

        artist_stats.append({
            "artist": artist,
            "top_track": top_track,
            "top_track_album": top_track_album,
            "top_track_plays": top_track_plays,
            "total_plays": total_plays,
            "other_tracks": other_tracks,
            "concentration": round(concentration, 3),
            "peak_years": peak_years,
            "first_year": first_year,
            "last_year": last_year,
        })

    result = pd.DataFrame(artist_stats)

    if result.empty:
        return result

    # Filter to one-track artists
    one_track = result[
        (result["concentration"] >= min_concentration) &
        (result["other_tracks"] <= max_other_tracks) &
        (result["top_track_plays"] >= min_top_track_plays)
    ].copy()

    # Sort by top track plays descending
    return one_track.sort_values("top_track_plays", ascending=False)


def get_ep_single_artists(
    df: pd.DataFrame,
    musicbrainz_lookup: callable,
    min_non_album_ratio: float = 0.5,
    min_total_plays: int = 20,
) -> pd.DataFrame:
    """Find artists where most plays come from non-album releases.

    These are "EP/single artists" - typically electronic producers,
    remixers, or artists who don't make traditional albums.

    Args:
        df: DataFrame of scrobbles
        musicbrainz_lookup: Function(artist, album) -> ReleaseInfo or None
        min_non_album_ratio: Min ratio of non-album plays (default: 0.5 = 50%)
        min_total_plays: Min total plays for artist (default: 20)

    Returns:
        DataFrame with columns:
        - artist
        - album_plays: Plays from release_type="album"
        - ep_single_plays: Plays from EPs, singles, etc.
        - unknown_plays: Plays from releases not in MusicBrainz
        - non_album_ratio: Ratio of non-album plays
        - top_non_album: Top non-album release
        - top_non_album_track: Top track from non-album releases
    """
    # Filter to rows with albums (releases)
    df_albums = df[df["album"] != ""].copy()

    if df_albums.empty:
        return pd.DataFrame()

    # Get play counts per artist/album
    album_plays = (
        df_albums.groupby(["artist", "album"])
        .size()
        .reset_index(name="plays")
    )

    # Get top track per album for display
    track_plays = (
        df_albums.groupby(["artist", "album", "track"])
        .size()
        .reset_index(name="track_plays")
    )
    top_tracks = (
        track_plays.sort_values("track_plays", ascending=False)
        .groupby(["artist", "album"])
        .first()
        .reset_index()[["artist", "album", "track"]]
    )
    album_plays = album_plays.merge(top_tracks, on=["artist", "album"], how="left")

    # Look up release types (this is the slow part)
    release_types = {}
    for _, row in album_plays.iterrows():
        artist, album = row["artist"], row["album"]
        key = (artist, album)
        if key not in release_types:
            info = musicbrainz_lookup(artist, album)
            release_types[key] = info.release_type if info else None

    album_plays["release_type"] = album_plays.apply(
        lambda r: release_types.get((r["artist"], r["album"])), axis=1
    )

    # Categorize: album vs non-album (ep, single, etc.)
    album_types = {"album"}
    non_album_types = {"ep", "single", "broadcast"}

    def categorize(rt):
        if rt is None:
            return "unknown"
        elif rt in album_types:
            return "album"
        elif rt in non_album_types:
            return "non_album"
        else:
            return "other"  # compilation, live, remix, etc.

    album_plays["category"] = album_plays["release_type"].apply(categorize)

    # Aggregate by artist
    artist_stats = []

    for artist in album_plays["artist"].unique():
        artist_data = album_plays[album_plays["artist"] == artist]

        album_play_count = artist_data[artist_data["category"] == "album"]["plays"].sum()
        non_album_play_count = artist_data[artist_data["category"] == "non_album"]["plays"].sum()
        unknown_play_count = artist_data[artist_data["category"] == "unknown"]["plays"].sum()
        other_play_count = artist_data[artist_data["category"] == "other"]["plays"].sum()

        total = album_play_count + non_album_play_count + unknown_play_count + other_play_count

        # Find top non-album release
        non_album_releases = artist_data[artist_data["category"] == "non_album"]
        if not non_album_releases.empty:
            top_row = non_album_releases.sort_values("plays", ascending=False).iloc[0]
            top_non_album = top_row["album"]
            top_non_album_track = top_row["track"]
        else:
            top_non_album = None
            top_non_album_track = None

        # Calculate ratio (excluding unknown)
        known_total = album_play_count + non_album_play_count
        if known_total > 0:
            non_album_ratio = non_album_play_count / known_total
        else:
            non_album_ratio = 0

        artist_stats.append({
            "artist": artist,
            "album_plays": int(album_play_count),
            "ep_single_plays": int(non_album_play_count),
            "unknown_plays": int(unknown_play_count),
            "total_plays": int(total),
            "non_album_ratio": round(non_album_ratio, 3),
            "top_non_album": top_non_album,
            "top_non_album_track": top_non_album_track,
        })

    result = pd.DataFrame(artist_stats)

    if result.empty:
        return result

    # Filter to EP/single-heavy artists
    ep_artists = result[
        (result["non_album_ratio"] >= min_non_album_ratio) &
        (result["total_plays"] >= min_total_plays) &
        (result["ep_single_plays"] > 0)  # Must have some EP/single plays
    ].copy()

    # Sort by EP/single plays descending
    return ep_artists.sort_values("ep_single_plays", ascending=False)
