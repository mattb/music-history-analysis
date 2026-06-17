"""Metadata commands - MusicBrainz metadata enrichment."""

import typer
from pathlib import Path
from typing import Optional
from collections import defaultdict
import sqlite3
import pandas as pd
from rich.console import Console

from .. import data

app = typer.Typer(help="MusicBrainz metadata enrichment")
console = Console()


def get_csv_path(csv: Optional[Path] = None) -> Path:
    """Get CSV path from argument, glob, or error."""
    if csv and csv.exists():
        return csv

    # Auto-detect from glob
    csvs = list(Path.cwd().glob("recenttracks-*.csv"))
    if csvs:
        return sorted(csvs)[-1]  # Most recent

    console.print("[red]No CSV found. Provide --csv or place recenttracks-*.csv in current dir[/red]")
    raise typer.Exit(1)


@app.command(name="download")
def metadata_download(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", "-f", help="Force re-download even if cached"),
):
    """Download MusicBrainz database for rich music metadata lookups.

    Downloads the full MusicBrainz release dump (~2-3GB compressed) and builds
    a local SQLite database with release years, genres, labels, countries, and more.

    The dump file is cached locally, so re-running only re-processes (no re-download).
    Use --force to bypass the cache and re-download.

    The database is stored at ~/.cache/music-history-analysis/musicbrainz_releases.db
    """
    from .. import musicbrainz_db

    stats = musicbrainz_db.get_database_stats()
    if stats:
        console.print(f"[yellow]Existing database found:[/yellow]")
        console.print(f"  Releases: {stats['releases']:,}")
        console.print(f"  Years: {stats['year_range'][0]}-{stats['year_range'][1]}")
        if stats.get('has_full_schema'):
            console.print(f"  Genres: {stats['unique_genres']:,}")
            console.print(f"  Labels: {stats['unique_labels']:,}")
        else:
            console.print(f"  [dim]Old schema - re-download to get genres, labels, countries[/dim]")
        console.print(f"  Size: {stats['size_mb']:.1f} MB")
        console.print()

    console.print("[bold]This will download ~2-3GB (first time) and take 5-15 minutes to process.[/bold]")
    console.print()

    count = musicbrainz_db.download_and_build_database(force_download=force)

    # Show final stats
    stats = musicbrainz_db.get_database_stats()
    console.print(f"\n[bold green]Done! Database ready:[/bold green]")
    console.print(f"  Releases: {stats['releases']:,}")
    console.print(f"  Years: {stats['year_range'][0]}-{stats['year_range'][1]}")
    console.print(f"  Genres: {stats['unique_genres']:,}")
    console.print(f"  Labels: {stats['unique_labels']:,}")
    if stats['release_types']:
        console.print(f"  Types: {', '.join(f'{k} ({v:,})' for k, v in list(stats['release_types'].items())[:5])}")
    console.print(f"  Size: {stats['size_mb']:.1f} MB")


@app.command(name="enrich")
def metadata_enrich(
    ctx: typer.Context,
    limit: int = typer.Option(500, "--limit", "-n", help="Max albums to look up via API"),
):
    """Fetch release years for albums.

    Uses local MusicBrainz database if available (instant), otherwise falls
    back to API (rate-limited to 1 req/sec).

    Run 'metadata download' first for best performance.
    """
    from .. import release_years, musicbrainz_db

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)

    # Get unique albums with MBIDs
    df_albums = df[df["album"] != ""].copy()
    df_albums = df_albums[df_albums["artist"].notna()]

    unique_albums = df_albums.groupby(["artist", "album"]).agg({
        "album_mbid": "first"
    }).reset_index()

    console.print(f"[bold]Found {len(unique_albums)} unique albums in your library[/bold]")

    # Load existing cache
    cache = release_years.load_cache()
    initial_cache_size = len(cache)

    # Check for local MusicBrainz database
    db_stats = musicbrainz_db.get_database_stats()
    has_local_db = db_stats is not None

    if has_local_db:
        console.print(f"[green]Using local MusicBrainz database ({db_stats['releases']:,} releases)[/green]")
    else:
        console.print("[yellow]No local database. Run 'metadata download' for faster lookups.[/yellow]")

    # First pass: check cache and local DB
    cached_count = 0
    local_db_found = 0
    to_lookup = []

    conn = None
    if has_local_db:
        conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)

    for _, row in unique_albums.iterrows():
        artist = row["artist"]
        album = row["album"]
        mbid = row["album_mbid"] if pd.notna(row["album_mbid"]) else ""

        cache_key = f"{artist.lower()}|||{album.lower()}"

        # Check cache first
        if mbid in cache or cache_key in cache:
            cached_count += 1
            continue

        # Check local DB
        if conn:
            year_found = musicbrainz_db.lookup_release_year(artist, album, conn)
            if year_found:
                cache[cache_key] = year_found
                local_db_found += 1
                continue

        to_lookup.append((artist, album, mbid))

    if conn:
        conn.close()

    # Save any new entries found from local DB
    if local_db_found > 0:
        release_years.save_cache(cache)
        console.print(f"[green]{local_db_found}[/green] found in local database")

    console.print(f"[green]{cached_count}[/green] already cached, [yellow]{len(to_lookup)}[/yellow] need API lookup")

    if not to_lookup:
        console.print("[green]All albums already enriched![/green]")
        return

    # Limit API lookups
    to_lookup = to_lookup[:limit]
    console.print(f"\n[dim]Looking up {len(to_lookup)} albums via API (limit: {limit})...[/dim]")
    console.print("[dim]This will take ~{} minutes due to rate limiting[/dim]\n".format(len(to_lookup) // 60 + 1))

    results = release_years.enrich_albums_with_release_years(to_lookup, delay=1.1)

    console.print(f"\n[green]Found release years for {len(results)} albums[/green]")
    console.print(f"[dim]Cache saved to {release_years.RELEASE_CACHE_FILE}[/dim]")


@app.command(name="catalog")
def metadata_catalog(
    ctx: typer.Context,
):
    """Analyze your new vs catalog listening patterns.

    Shows:
    - What % of your listening is new releases vs older catalog
    - Decade breakdown (are you a 70s person? 90s? 2020s?)
    - Average discovery lag (how long after release do you find albums?)

    Run 'metadata enrich' first to populate release year data.
    """
    from .. import release_years

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)
        title = f"CATALOG ANALYSIS ({year})"
    else:
        title = "CATALOG ANALYSIS (All Time)"

    # Load release year cache
    cache = release_years.load_cache()

    if not cache:
        console.print("[yellow]No release year data found.[/yellow]")
        console.print("Run [cyan]music-history metadata enrich[/cyan] first to fetch release years.")
        raise typer.Exit(1)

    # Match scrobbles with release years
    df_albums = df[df["album"] != ""].copy()
    df_albums = df_albums[df_albums["artist"].notna()]

    matched = 0
    unmatched = 0
    scrobbles_with_years = []

    # Stats by year
    by_play_year = defaultdict(lambda: {"total": 0, "new": 0, "by_decade": defaultdict(int)})

    for _, row in df_albums.iterrows():
        artist = str(row["artist"])
        album = str(row["album"])
        mbid = row["album_mbid"] if pd.notna(row["album_mbid"]) else ""
        play_year = row["year"]

        # Look up release year
        release_year = None
        if mbid and mbid in cache:
            release_year = cache[mbid]
        else:
            cache_key = f"{artist.lower()}|||{album.lower()}"
            if cache_key in cache:
                release_year = cache[cache_key]

        if release_year:
            matched += 1
            scrobbles_with_years.append({
                "artist": artist,
                "album": album,
                "play_year": play_year,
                "release_year": release_year,
            })

            # Update stats
            by_play_year[play_year]["total"] += 1
            if release_year == play_year:
                by_play_year[play_year]["new"] += 1

            decade = (release_year // 10) * 10
            by_play_year[play_year]["by_decade"][decade] += 1
        else:
            unmatched += 1

    total = matched + unmatched
    console.print(f"\n[bold magenta]═══ {title} ═══[/bold magenta]")
    console.print(f"[dim]Matched {matched:,} of {total:,} plays ({100*matched/total:.0f}%) with release years[/dim]\n")

    if matched == 0:
        console.print("[yellow]No release year data matched. Run 'metadata enrich' with more albums.[/yellow]")
        return

    # Overall decade breakdown
    decade_totals = defaultdict(int)
    for s in scrobbles_with_years:
        decade = (s["release_year"] // 10) * 10
        decade_totals[decade] += 1

    console.print("[bold cyan]🎵 DECADE BREAKDOWN[/bold cyan]")
    console.print("[dim]What era is your music from?[/dim]\n")

    max_decade = max(decade_totals.values()) if decade_totals else 1
    for decade in sorted(decade_totals.keys()):
        count = decade_totals[decade]
        pct = count / matched * 100
        bar_width = int((count / max_decade) * 25)
        bar = "█" * bar_width
        console.print(f"  {decade}s [green]{bar}[/green] {pct:.1f}% ({count:,})")

    # New release percentage by year
    console.print(f"\n[bold cyan]📅 NEW RELEASE % BY YEAR[/bold cyan]")
    console.print("[dim]What % of plays were albums released that same year?[/dim]\n")

    all_years = sorted(by_play_year.keys())
    # Show last 20 years, or all if less than 20
    years_to_show = all_years[-20:] if len(all_years) > 20 else all_years

    for play_year in years_to_show:
        stats = by_play_year[play_year]
        if stats["total"] > 0:
            new_pct = stats["new"] / stats["total"] * 100
            bar_width = int(new_pct / 4)  # Scale to ~25 chars for 100%
            bar = "█" * bar_width
            console.print(f"  {play_year} [green]{bar}[/green] {new_pct:.1f}% new releases")

    # Discovery lag
    console.print(f"\n[bold cyan]⏱️  DISCOVERY LAG[/bold cyan]")
    console.print("[dim]How long after release do you typically discover albums?[/dim]\n")

    # Calculate per-album discovery lag
    album_first_play = {}
    for s in scrobbles_with_years:
        key = (s["artist"], s["album"])
        if key not in album_first_play:
            album_first_play[key] = {
                "play_year": s["play_year"],
                "release_year": s["release_year"],
            }

    lag_buckets = defaultdict(int)
    for info in album_first_play.values():
        lag = info["play_year"] - info["release_year"]
        if lag < 0:
            lag_buckets["Pre-release/error"] += 1
        elif lag == 0:
            lag_buckets["Same year"] += 1
        elif lag == 1:
            lag_buckets["1 year later"] += 1
        elif lag <= 3:
            lag_buckets["2-3 years"] += 1
        elif lag <= 5:
            lag_buckets["4-5 years"] += 1
        elif lag <= 10:
            lag_buckets["6-10 years"] += 1
        else:
            lag_buckets["10+ years"] += 1

    total_albums = len(album_first_play)
    order = ["Same year", "1 year later", "2-3 years", "4-5 years", "6-10 years", "10+ years"]
    for bucket in order:
        if bucket in lag_buckets:
            count = lag_buckets[bucket]
            pct = count / total_albums * 100
            console.print(f"  {bucket}: {count:,} albums ({pct:.1f}%)")

    # Average lag
    lags = [info["play_year"] - info["release_year"]
            for info in album_first_play.values()
            if info["play_year"] >= info["release_year"]]
    if lags:
        avg_lag = sum(lags) / len(lags)
        console.print(f"\n  [bold]Average discovery lag: {avg_lag:.1f} years[/bold]")

    # Vintage vs Modern summary
    console.print(f"\n[bold cyan]📊 VINTAGE VS MODERN[/bold cyan]\n")

    current_decade = (max(s["play_year"] for s in scrobbles_with_years) // 10) * 10
    modern = sum(1 for s in scrobbles_with_years if s["release_year"] >= current_decade - 10)
    vintage = matched - modern

    modern_pct = modern / matched * 100
    vintage_pct = vintage / matched * 100

    console.print(f"  Modern (last 20 years): [green]{modern_pct:.1f}%[/green]")
    console.print(f"  Vintage (20+ years old): [yellow]{vintage_pct:.1f}%[/yellow]")


@app.command(name="genres")
def metadata_genres(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="Number of genres to show"),
):
    """Analyze your listening by genre.

    Shows which genres dominate your listening, how they've evolved over time,
    and identifies genre blind spots.

    Requires the MusicBrainz database - run 'metadata download' first.
    """
    from .. import musicbrainz_db

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    # Check for local database
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats:
        console.print("[yellow]No MusicBrainz database found.[/yellow]")
        console.print("Run [cyan]music-history metadata download[/cyan] first.")
        raise typer.Exit(1)

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)
        title = f"GENRE ANALYSIS ({year})"
    else:
        title = "GENRE ANALYSIS (All Time)"

    # Get unique albums
    df_albums = df[df["album"] != ""].copy()
    df_albums = df_albums[df_albums["artist"].notna()]

    # Count plays per album
    album_plays = df_albums.groupby(["artist", "album"]).size().reset_index(name="plays")

    # Look up genres for each album
    conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)
    genre_plays = defaultdict(int)  # genre -> total plays
    genre_albums = defaultdict(set)  # genre -> set of (artist, album)
    matched = 0
    total_plays_matched = 0

    for _, row in album_plays.iterrows():
        artist = row["artist"]
        album = row["album"]
        plays = row["plays"]

        info = musicbrainz_db.lookup_release(artist, album, conn)
        if info and info.genres:
            matched += 1
            total_plays_matched += plays
            for genre in info.genres:
                genre_plays[genre] += plays
                genre_albums[genre].add((artist, album))

    conn.close()

    console.print(f"\n[bold magenta]═══ {title} ═══[/bold magenta]")
    console.print(f"[dim]Matched {matched:,} of {len(album_plays):,} albums with genre data[/dim]\n")

    if not genre_plays:
        console.print("[yellow]No genre data found. Try running 'metadata download' first.[/yellow]")
        return

    # Sort genres by play count
    sorted_genres = sorted(genre_plays.items(), key=lambda x: x[1], reverse=True)
    top_genres = sorted_genres[:limit]

    console.print("[bold cyan]🎸 TOP GENRES BY PLAYS[/bold cyan]\n")

    max_plays = top_genres[0][1] if top_genres else 1
    for genre, plays in top_genres:
        pct = plays / total_plays_matched * 100
        bar_width = int((plays / max_plays) * 25)
        bar = "█" * bar_width
        album_count = len(genre_albums[genre])
        console.print(f"  {genre:<20} [green]{bar}[/green] {pct:>5.1f}% ({plays:,} plays, {album_count} albums)")

    # If analyzing all time, show genre evolution by year
    if not year:
        console.print(f"\n[bold cyan]📈 GENRE EVOLUTION[/bold cyan]")
        console.print("[dim]How have your top genres changed over time?[/dim]\n")

        # Get plays by year for top 5 genres
        top_5_genres = [g for g, _ in sorted_genres[:5]]

        # Re-scan with year info
        df_albums_with_year = df_albums.copy()
        genre_by_year = defaultdict(lambda: defaultdict(int))

        conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)
        for _, row in df_albums_with_year.iterrows():
            artist = row["artist"]
            album = row["album"]
            play_year = row["year"]

            info = musicbrainz_db.lookup_release(artist, album, conn)
            if info and info.genres:
                for genre in info.genres:
                    if genre in top_5_genres:
                        genre_by_year[play_year][genre] += 1
        conn.close()

        # Show sparkline-style evolution for each top genre
        years = sorted(genre_by_year.keys())[-10:]  # Last 10 years
        for genre in top_5_genres:
            values = [genre_by_year[y].get(genre, 0) for y in years]
            max_val = max(values) if values else 1
            # Create sparkline
            blocks = " ▁▂▃▄▅▆▇█"
            sparkline = ""
            for v in values:
                idx = int((v / max_val) * 8) if max_val > 0 else 0
                sparkline += blocks[idx]
            console.print(f"  {genre:<20} {sparkline} ({years[0]}-{years[-1]})")


@app.command(name="labels")
def metadata_labels(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="Number of labels to show"),
):
    """Analyze your listening by record label.

    Discover which labels' releases dominate your listening.

    Requires the MusicBrainz database - run 'metadata download' first.
    """
    from .. import musicbrainz_db

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    # Check for local database
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats:
        console.print("[yellow]No MusicBrainz database found.[/yellow]")
        console.print("Run [cyan]music-history metadata download[/cyan] first.")
        raise typer.Exit(1)

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)
        title = f"LABEL ANALYSIS ({year})"
    else:
        title = "LABEL ANALYSIS (All Time)"

    # Get unique albums
    df_albums = df[df["album"] != ""].copy()
    df_albums = df_albums[df_albums["artist"].notna()]

    # Count plays per album
    album_plays = df_albums.groupby(["artist", "album"]).size().reset_index(name="plays")

    # Look up labels for each album
    conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)
    label_plays = defaultdict(int)  # label -> total plays
    label_albums = defaultdict(set)  # label -> set of (artist, album)
    label_artists = defaultdict(set)  # label -> set of artists
    matched = 0
    total_plays_matched = 0

    for _, row in album_plays.iterrows():
        artist = row["artist"]
        album = row["album"]
        plays = row["plays"]

        info = musicbrainz_db.lookup_release(artist, album, conn)
        if info and info.labels:
            matched += 1
            total_plays_matched += plays
            for label in info.labels:
                label_plays[label] += plays
                label_albums[label].add((artist, album))
                label_artists[label].add(artist)

    conn.close()

    console.print(f"\n[bold magenta]═══ {title} ═══[/bold magenta]")
    console.print(f"[dim]Matched {matched:,} of {len(album_plays):,} albums with label data[/dim]\n")

    if not label_plays:
        console.print("[yellow]No label data found. Try running 'metadata download' first.[/yellow]")
        return

    # Sort labels by play count
    sorted_labels = sorted(label_plays.items(), key=lambda x: x[1], reverse=True)
    top_labels = sorted_labels[:limit]

    console.print("[bold cyan]🏷️  TOP LABELS BY PLAYS[/bold cyan]\n")

    max_plays = top_labels[0][1] if top_labels else 1
    for label, plays in top_labels:
        pct = plays / total_plays_matched * 100
        bar_width = int((plays / max_plays) * 25)
        bar = "█" * bar_width
        album_count = len(label_albums[label])
        artist_count = len(label_artists[label])
        console.print(f"  {label[:25]:<25} [green]{bar}[/green] {pct:>5.1f}% ({artist_count} artists, {album_count} albums)")

    # Show notable artists per top label
    console.print(f"\n[bold cyan]🎤 ARTISTS BY LABEL[/bold cyan]\n")

    for label, _ in top_labels[:10]:
        artists = sorted(label_artists[label])[:5]
        artists_str = ", ".join(artists)
        if len(label_artists[label]) > 5:
            artists_str += f" (+{len(label_artists[label]) - 5} more)"
        console.print(f"  [bold]{label}[/bold]")
        console.print(f"    {artists_str}")


@app.command(name="countries")
def metadata_countries(
    ctx: typer.Context,
    limit: int = typer.Option(15, "--limit", "-n", help="Number of countries to show"),
):
    """Analyze your listening by release country.

    See which countries' releases dominate your listening.

    Requires the MusicBrainz database - run 'metadata download' first.
    """
    from .. import musicbrainz_db

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    # Country code to name mapping (common ones)
    COUNTRY_NAMES = {
        "US": "United States", "GB": "United Kingdom", "JP": "Japan",
        "DE": "Germany", "FR": "France", "CA": "Canada", "AU": "Australia",
        "SE": "Sweden", "NL": "Netherlands", "IT": "Italy", "ES": "Spain",
        "NO": "Norway", "DK": "Denmark", "FI": "Finland", "BE": "Belgium",
        "AT": "Austria", "CH": "Switzerland", "IE": "Ireland", "NZ": "New Zealand",
        "BR": "Brazil", "MX": "Mexico", "KR": "South Korea", "XW": "Worldwide",
        "XE": "Europe", "RU": "Russia", "PL": "Poland", "PT": "Portugal",
    }

    # Check for local database
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats:
        console.print("[yellow]No MusicBrainz database found.[/yellow]")
        console.print("Run [cyan]music-history metadata download[/cyan] first.")
        raise typer.Exit(1)

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)
        title = f"COUNTRY ANALYSIS ({year})"
    else:
        title = "COUNTRY ANALYSIS (All Time)"

    # Get unique albums
    df_albums = df[df["album"] != ""].copy()
    df_albums = df_albums[df_albums["artist"].notna()]

    # Count plays per album
    album_plays = df_albums.groupby(["artist", "album"]).size().reset_index(name="plays")

    # Look up countries for each album
    conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)
    country_plays = defaultdict(int)
    country_albums = defaultdict(set)
    matched = 0
    total_plays_matched = 0

    for _, row in album_plays.iterrows():
        artist = row["artist"]
        album = row["album"]
        plays = row["plays"]

        info = musicbrainz_db.lookup_release(artist, album, conn)
        if info and info.country:
            matched += 1
            total_plays_matched += plays
            country_plays[info.country] += plays
            country_albums[info.country].add((artist, album))

    conn.close()

    console.print(f"\n[bold magenta]═══ {title} ═══[/bold magenta]")
    console.print(f"[dim]Matched {matched:,} of {len(album_plays):,} albums with country data[/dim]\n")

    if not country_plays:
        console.print("[yellow]No country data found. Try running 'metadata download' first.[/yellow]")
        return

    # Sort countries by play count
    sorted_countries = sorted(country_plays.items(), key=lambda x: x[1], reverse=True)
    top_countries = sorted_countries[:limit]

    console.print("[bold cyan]🌍 TOP RELEASE COUNTRIES[/bold cyan]\n")

    max_plays = top_countries[0][1] if top_countries else 1
    for country_code, plays in top_countries:
        pct = plays / total_plays_matched * 100
        bar_width = int((plays / max_plays) * 25)
        bar = "█" * bar_width
        album_count = len(country_albums[country_code])
        country_name = COUNTRY_NAMES.get(country_code, country_code)
        console.print(f"  {country_name:<20} [green]{bar}[/green] {pct:>5.1f}% ({plays:,} plays, {album_count} albums)")


@app.command(name="types")
def metadata_types(
    ctx: typer.Context,
):
    """Analyze your listening by release type.

    Shows breakdown of albums vs EPs vs singles vs compilations, etc.

    Requires the MusicBrainz database - run 'metadata download' first.
    """
    from .. import musicbrainz_db

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    # Check for local database
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats:
        console.print("[yellow]No MusicBrainz database found.[/yellow]")
        console.print("Run [cyan]music-history metadata download[/cyan] first.")
        raise typer.Exit(1)

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)
        title = f"RELEASE TYPE ANALYSIS ({year})"
    else:
        title = "RELEASE TYPE ANALYSIS (All Time)"

    # Get unique albums
    df_albums = df[df["album"] != ""].copy()
    df_albums = df_albums[df_albums["artist"].notna()]

    # Count plays per album
    album_plays = df_albums.groupby(["artist", "album"]).size().reset_index(name="plays")

    # Look up release type for each album
    conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)
    type_plays = defaultdict(int)
    type_albums = defaultdict(set)
    matched = 0
    total_plays_matched = 0

    for _, row in album_plays.iterrows():
        artist = row["artist"]
        album = row["album"]
        plays = row["plays"]

        info = musicbrainz_db.lookup_release(artist, album, conn)
        if info:
            matched += 1
            total_plays_matched += plays
            release_type = info.release_type or "unknown"
            type_plays[release_type] += plays
            type_albums[release_type].add((artist, album))

    conn.close()

    console.print(f"\n[bold magenta]═══ {title} ═══[/bold magenta]")
    console.print(f"[dim]Matched {matched:,} of {len(album_plays):,} releases[/dim]\n")

    if not type_plays:
        console.print("[yellow]No release type data found.[/yellow]")
        return

    # Sort by play count
    sorted_types = sorted(type_plays.items(), key=lambda x: x[1], reverse=True)

    console.print("[bold cyan]💿 RELEASE TYPES[/bold cyan]\n")

    max_plays = sorted_types[0][1] if sorted_types else 1
    for release_type, plays in sorted_types:
        pct = plays / total_plays_matched * 100
        bar_width = int((plays / max_plays) * 25)
        bar = "█" * bar_width
        album_count = len(type_albums[release_type])
        # Capitalize nicely
        type_display = release_type.replace("-", " ").title()
        console.print(f"  {type_display:<20} [green]{bar}[/green] {pct:>5.1f}% ({plays:,} plays, {album_count} releases)")
