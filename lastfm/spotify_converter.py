"""Convert Spotify Extended Streaming History to CSV format."""
import json
from datetime import datetime
from pathlib import Path

import pandas as pd


def load_spotify_json(directory: Path) -> list[dict]:
    """Load all Spotify streaming history JSON files from a directory."""
    records = []
    for json_path in sorted(directory.glob("Streaming_History_Audio_*.json")):
        data = json.loads(json_path.read_text())
        records.extend(data)
    return records


def convert_spotify_to_df(
    records: list[dict],
    min_ms: int = 30000,
    exclude_skipped: bool = True,
) -> pd.DataFrame:
    """Convert Spotify records to DataFrame in extended CSV format.

    Args:
        records: Raw Spotify streaming history records
        min_ms: Minimum play duration in milliseconds (default: 30000 = 30 seconds)
        exclude_skipped: Whether to exclude skipped tracks (default: True)

    Returns:
        DataFrame with core Last.fm columns + extended Spotify columns
    """
    rows = []
    for entry in records:
        # Skip non-music (podcasts, audiobooks)
        track = entry.get("master_metadata_track_name")
        if not track:
            continue

        # Apply duration filter
        ms_played = entry.get("ms_played", 0)
        if ms_played < min_ms:
            continue

        # Apply skip filter
        if exclude_skipped and entry.get("skipped"):
            continue

        # Parse timestamp
        ts = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))

        rows.append({
            # Core columns (Last.fm compatible)
            "uts": int(ts.timestamp()),
            "utc_time": ts.strftime("%d %b %Y, %H:%M"),
            "artist": entry.get("master_metadata_album_artist_name", "") or "",
            "artist_mbid": "",
            "album": entry.get("master_metadata_album_album_name", "") or "",
            "album_mbid": "",
            "track": track,
            "track_mbid": "",
            # Extended columns (Spotify-specific)
            "ms_played": ms_played,
            "shuffle": entry.get("shuffle", False),
            "reason_start": entry.get("reason_start", ""),
            "reason_end": entry.get("reason_end", ""),
            "platform": entry.get("platform", ""),
            "conn_country": entry.get("conn_country", ""),
            "source": "spotify",
        })

    df = pd.DataFrame(rows)
    if len(df) > 0:
        # Sort by timestamp (oldest first, matching Last.fm format)
        df = df.sort_values("uts").reset_index(drop=True)
    return df


def convert_spotify_directory(
    directory: Path,
    output: Path,
    min_ms: int = 30000,
    exclude_skipped: bool = True,
) -> tuple[int, int]:
    """Convert Spotify directory to CSV file.

    Args:
        directory: Directory containing Spotify JSON files
        output: Path to write the output CSV
        min_ms: Minimum play duration in milliseconds
        exclude_skipped: Whether to exclude skipped tracks

    Returns:
        Tuple of (total_records_processed, records_output)
    """
    records = load_spotify_json(directory)
    df = convert_spotify_to_df(records, min_ms=min_ms, exclude_skipped=exclude_skipped)
    df.to_csv(output, index=False)
    return len(records), len(df)
