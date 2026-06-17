"""Release year enrichment via MusicBrainz API."""

import json
import time
from pathlib import Path
from dataclasses import dataclass

import musicbrainzngs
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn

console = Console()

# Set up MusicBrainz
musicbrainzngs.set_useragent("music-history-analysis", "1.0", "https://github.com/example/music-history-analysis")

# Cache for release years
CACHE_DIR = Path.home() / ".cache" / "music-history-analysis"
RELEASE_CACHE_FILE = CACHE_DIR / "release_years.json"


def load_cache() -> dict:
    """Load cached release years."""
    if RELEASE_CACHE_FILE.exists():
        try:
            return json.loads(RELEASE_CACHE_FILE.read_text())
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_cache(cache: dict) -> None:
    """Save release years cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RELEASE_CACHE_FILE.write_text(json.dumps(cache, indent=2))


def get_release_year_by_mbid(mbid: str, cache: dict) -> int | None:
    """Get release year from MusicBrainz by release MBID."""
    if not mbid:
        return None

    # Check cache
    if mbid in cache:
        return cache[mbid]

    try:
        result = musicbrainzngs.get_release_by_id(mbid, includes=[])
        release = result.get("release", {})
        date = release.get("date", "")

        if date:
            # Date can be YYYY, YYYY-MM, or YYYY-MM-DD
            year = int(date[:4])
            cache[mbid] = year
            return year
    except Exception:
        pass

    return None


def get_release_year_by_search(artist: str, album: str, cache: dict) -> int | None:
    """Search MusicBrainz for release year by artist and album name."""
    cache_key = f"{artist.lower()}|||{album.lower()}"

    if cache_key in cache:
        return cache[cache_key]

    try:
        result = musicbrainzngs.search_releases(
            artist=artist,
            release=album,
            limit=5,
        )

        releases = result.get("release-list", [])
        for release in releases:
            date = release.get("date", "")
            if date:
                year = int(date[:4])
                cache[cache_key] = year
                return year
    except Exception:
        pass

    return None


def enrich_albums_with_release_years(
    albums: list[tuple[str, str, str]],  # List of (artist, album, mbid)
    delay: float = 1.0,
) -> dict[tuple[str, str], int]:
    """
    Enrich a list of albums with release years.

    Args:
        albums: List of (artist, album, mbid) tuples
        delay: Delay between API requests (MusicBrainz rate limit)

    Returns:
        Dict mapping (artist, album) to release year
    """
    cache = load_cache()
    results = {}
    lookups_needed = []

    # First pass: check cache
    for artist, album, mbid in albums:
        key = (artist, album)

        # Try MBID first
        if mbid and mbid in cache:
            results[key] = cache[mbid]
            continue

        # Try search cache
        cache_key = f"{artist.lower()}|||{album.lower()}"
        if cache_key in cache:
            results[key] = cache[cache_key]
            continue

        lookups_needed.append((artist, album, mbid))

    if not lookups_needed:
        return results

    # Second pass: API lookups
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Fetching release years...", total=len(lookups_needed))
        processed = 0

        for artist, album, mbid in lookups_needed:
            key = (artist, album)
            year = None

            # Try MBID first
            if mbid:
                year = get_release_year_by_mbid(mbid, cache)

            # Fall back to search
            if year is None:
                time.sleep(delay)  # Rate limit
                year = get_release_year_by_search(artist, album, cache)

            if year:
                results[key] = year

            progress.advance(task)
            processed += 1

            # Save cache incrementally every 25 albums
            if processed % 25 == 0:
                save_cache(cache)

            time.sleep(delay)  # Rate limit between requests

    # Save final cache
    save_cache(cache)

    return results


def analyze_listening_by_release_year(
    scrobbles_with_years: list[dict],  # List of {artist, album, play_year, release_year}
) -> dict:
    """
    Analyze listening patterns by release year.

    Returns dict with:
    - new_release_pct: % of plays that are albums from that year
    - decade_breakdown: plays by decade of release
    - discovery_lag: average years between release and first listen
    - catalog_vs_new: breakdown of new vs catalog
    """
    from collections import defaultdict

    total_plays = len(scrobbles_with_years)
    if total_plays == 0:
        return {}

    new_releases = 0  # Played in same year as release
    by_decade = defaultdict(int)
    by_release_year = defaultdict(int)
    discovery_lags = []

    # Track first listen per album
    album_first_play = {}  # (artist, album) -> first play year

    for s in scrobbles_with_years:
        play_year = s["play_year"]
        release_year = s.get("release_year")

        if release_year:
            # Is this a new release?
            if release_year == play_year:
                new_releases += 1

            # Decade breakdown
            decade = (release_year // 10) * 10
            by_decade[decade] += 1
            by_release_year[release_year] += 1

            # Track discovery lag (first listen)
            key = (s["artist"], s["album"])
            if key not in album_first_play:
                album_first_play[key] = (play_year, release_year)

    # Calculate discovery lag
    for (play_year, release_year) in album_first_play.values():
        lag = play_year - release_year
        if lag >= 0:  # Ignore negative lags (bad data)
            discovery_lags.append(lag)

    avg_discovery_lag = sum(discovery_lags) / len(discovery_lags) if discovery_lags else 0

    # Catalog breakdown (how old is what you listen to)
    plays_with_year = sum(by_release_year.values())
    within_1_year = sum(v for y, v in by_release_year.items()
                        if any(s["play_year"] - y <= 1 for s in scrobbles_with_years if s.get("release_year") == y))

    return {
        "total_plays": total_plays,
        "plays_with_release_year": plays_with_year,
        "new_release_count": new_releases,
        "new_release_pct": (new_releases / plays_with_year * 100) if plays_with_year else 0,
        "by_decade": dict(sorted(by_decade.items())),
        "by_release_year": dict(sorted(by_release_year.items())),
        "avg_discovery_lag_years": avg_discovery_lag,
        "unique_albums_analyzed": len(album_first_play),
    }
