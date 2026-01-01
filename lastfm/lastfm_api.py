"""Last.fm API client for fetching scrobble history."""

import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

console = Console()


class LastFMAPI:
    """Client for Last.fm API v2.0."""

    BASE_URL = "http://ws.audioscrobbler.com/2.0/"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.Client(timeout=30.0)

    def get_recent_tracks(
        self,
        username: str,
        limit: int = 200,
        page: int = 1,
        time_from: Optional[int] = None,
        max_retries: int = 3,
    ) -> dict:
        """Fetch a page of recent tracks for a user.

        Args:
            username: Last.fm username
            limit: Tracks per page (max 200)
            page: Page number (1-indexed)
            time_from: Unix timestamp to start from (optional)
            max_retries: Maximum number of retry attempts on 500 errors (default: 3)

        Returns:
            API response dict
        """
        params = {
            "method": "user.getrecenttracks",
            "user": username,
            "api_key": self.api_key,
            "format": "json",
            "limit": limit,
            "page": page,
            # Note: extended=1 actually REMOVES artist MBIDs, so we don't use it
        }

        if time_from:
            params["from"] = time_from

        # Retry logic for 500 errors
        for attempt in range(max_retries):
            try:
                response = self.client.get(self.BASE_URL, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 500 and attempt < max_retries - 1:
                    # Last.fm backend failure - wait and retry
                    console.print(f"[yellow]API error (500), retrying in 10 seconds... (attempt {attempt + 1}/{max_retries})[/yellow]")
                    time.sleep(10)
                    continue
                else:
                    # Not a 500 error, or we've exhausted retries
                    raise

    def fetch_all_scrobbles(
        self,
        username: str,
        output_path: Path,
        delay: float = 0.2,
        max_pages: Optional[int] = None,
        start_year: Optional[int] = None,
    ) -> int:
        """Fetch all scrobbles and write to CSV.

        Args:
            username: Last.fm username
            output_path: Path to output CSV file
            delay: Delay between API requests in seconds (respect rate limits)
            max_pages: Maximum number of pages to fetch (for testing, None = all)
            start_year: Only fetch scrobbles from this year onwards (optional)

        Returns:
            Total number of scrobbles fetched
        """
        console.print(f"[cyan]Fetching scrobbles for user:[/cyan] {username}")

        # Convert start_year to Unix timestamp (Jan 1, 00:00:00 UTC)
        time_from = None
        if start_year:
            from datetime import datetime, timezone
            time_from = int(datetime(start_year, 1, 1, tzinfo=timezone.utc).timestamp())
            console.print(f"[dim]Starting from: January 1, {start_year}[/dim]")

        # Get first page to determine total
        first_page = self.get_recent_tracks(username, limit=200, page=1, time_from=time_from)

        if "error" in first_page:
            console.print(f"[red]API Error:[/red] {first_page.get('message', 'Unknown error')}")
            return 0

        recenttracks = first_page.get("recenttracks", {})
        total_tracks = int(recenttracks.get("@attr", {}).get("total", 0))
        total_pages = int(recenttracks.get("@attr", {}).get("totalPages", 0))

        if max_pages:
            total_pages = min(total_pages, max_pages)

        console.print(f"[green]Total scrobbles:[/green] {total_tracks:,}")
        console.print(f"[dim]Fetching {total_pages:,} pages (200 tracks/page)[/dim]\n")

        # Open CSV for writing
        with open(output_path, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile, quoting=csv.QUOTE_ALL)

            # Write header
            writer.writerow([
                "uts",
                "utc_time",
                "artist",
                "artist_mbid",
                "album",
                "album_mbid",
                "track",
                "track_mbid",
            ])

            scrobbles_written = 0

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(
                    f"Downloading scrobbles...",
                    total=total_pages,
                )

                for page in range(1, total_pages + 1):
                    # Fetch page (reuse first page data)
                    if page == 1:
                        data = first_page
                    else:
                        data = self.get_recent_tracks(username, limit=200, page=page, time_from=time_from)
                        time.sleep(delay)  # Rate limiting

                    tracks = data.get("recenttracks", {}).get("track", [])

                    # Handle single track response (not a list)
                    if isinstance(tracks, dict):
                        tracks = [tracks]

                    for track in tracks:
                        # Skip "now playing" tracks (no timestamp)
                        if "@attr" in track and track["@attr"].get("nowplaying") == "true":
                            continue

                        # Extract timestamp
                        timestamp = track.get("date", {})
                        uts = timestamp.get("uts", "")
                        utc_time = timestamp.get("#text", "")

                        # Extract artist (non-extended mode: {"mbid": "...", "#text": "name"})
                        artist = track.get("artist", {})
                        if isinstance(artist, dict):
                            artist_name = artist.get("#text", "")
                            artist_mbid = artist.get("mbid", "")
                        else:
                            artist_name = str(artist) if artist else ""
                            artist_mbid = ""

                        # Extract album ({"mbid": "...", "#text": "name"})
                        album = track.get("album", {})
                        if isinstance(album, dict):
                            album_name = album.get("#text", "")
                            album_mbid = album.get("mbid", "")
                        else:
                            album_name = str(album) if album else ""
                            album_mbid = ""

                        # Extract track
                        track_name = track.get("name", "")
                        track_mbid = track.get("mbid", "")

                        writer.writerow([
                            uts,
                            utc_time,
                            artist_name,
                            artist_mbid,
                            album_name,
                            album_mbid,
                            track_name,
                            track_mbid,
                        ])
                        scrobbles_written += 1

                    progress.update(task, advance=1)

        console.print(f"\n[green]✓ Successfully wrote {scrobbles_written:,} scrobbles to:[/green]")
        console.print(f"  {output_path}")

        return scrobbles_written


def get_api_key() -> Optional[str]:
    """Get Last.fm API key from cache."""
    cache_dir = Path.home() / ".cache" / "lastfm-analysis"
    cache_file = cache_dir / "lastfm_api_key.txt"

    if cache_file.exists():
        return cache_file.read_text().strip()
    return None


def save_api_key(api_key: str) -> None:
    """Save Last.fm API key to cache."""
    cache_dir = Path.home() / ".cache" / "lastfm-analysis"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "lastfm_api_key.txt"
    cache_file.write_text(api_key)
