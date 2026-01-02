"""CLI for Last.fm listening history analysis - refactored with command groups."""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
import httpx
import webbrowser

from . import data, lastfm_api
from .commands import listen, critics, history, metadata, spotify, visualize, eval

app = typer.Typer(
    help="Analyze your Last.fm listening history.",
    no_args_is_help=True,
)
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


def get_critics_path(year: int) -> Path:
    """Get the default critics JSON path for a given year."""
    return Path.cwd() / f"critics-{year}.json"


# Global options callback
@app.callback()
def main(
    ctx: typer.Context,
    csv: Optional[Path] = typer.Option(None, "--csv", "-c",
        help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y",
        help="Filter to specific year"),
    verbose: bool = typer.Option(False, "--verbose", "-v",
        help="Verbose output"),
    familiarity: float = typer.Option(0.4, "--familiarity", "-f",
        help="Album familiarity threshold (0-1). Default 0.4 replaces old binary 5x5 rule."),
):
    """Analyze your Last.fm listening history."""
    ctx.ensure_object(dict)
    ctx.obj["csv"] = csv
    ctx.obj["year"] = year
    ctx.obj["verbose"] = verbose
    ctx.obj["familiarity"] = familiarity


# Register command groups
app.add_typer(listen.app, name="listen", help="Analyze your listening patterns")
app.add_typer(critics.app, name="critics", help="Cross-reference with music critics")
app.add_typer(history.app, name="history", help="Long-term taste evolution")
app.add_typer(metadata.app, name="metadata", help="MusicBrainz metadata enrichment")
app.add_typer(spotify.app, name="spotify", help="Spotify integration")
app.add_typer(visualize.app, name="visualize", help="Generate visual representations")
app.add_typer(eval.app, name="eval", help="Evaluate embedding and recommendation quality")


# Root-level commands (most common operations)
@app.command()
def stats(
    ctx: typer.Context,
):
    """Show overall listening statistics."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)
        title = f"Listening Stats for {year}"
    else:
        title = "Overall Listening Stats"

    total_plays = len(df)
    unique_artists = df["artist"].nunique()
    unique_albums = df[df["album"] != ""]["album"].nunique()
    unique_tracks = df["track"].nunique()

    date_range = f"{df['timestamp'].min():%Y-%m-%d} to {df['timestamp'].max():%Y-%m-%d}"

    console.print(f"\n[bold]{title}[/bold]")
    console.print(f"Date range: {date_range}")
    console.print(f"Total plays: {total_plays:,}")
    console.print(f"Unique artists: {unique_artists:,}")
    console.print(f"Unique albums: {unique_albums:,}")
    console.print(f"Unique tracks: {unique_tracks:,}")


@app.command(name="fetch-api-key")
def fetch_api_key(
    api_key: Optional[str] = typer.Option(None, "--key", "-k", help="Last.fm API key"),
):
    """Set up Last.fm API key for downloading scrobbles.

    Get your API key from: https://www.last.fm/api/account/create
    """
    if api_key:
        lastfm_api.save_api_key(api_key)
        console.print("[green]✓ API key saved successfully![/green]")
        console.print(f"[dim]Stored in: {Path.home() / '.cache' / 'lastfm-analysis' / 'lastfm_api_key.txt'}[/dim]")
    else:
        stored_key = lastfm_api.get_api_key()
        if stored_key:
            console.print(f"[green]API key found:[/green] {stored_key[:8]}...")
            console.print(f"[dim]Stored in: {Path.home() / '.cache' / 'lastfm-analysis' / 'lastfm_api_key.txt'}[/dim]")
        else:
            console.print("[yellow]No API key found.[/yellow]")
            console.print("\nTo get an API key:")
            console.print("1. Go to [cyan]https://www.last.fm/api/account/create[/cyan]")
            console.print("2. Fill out the form (app name can be anything)")
            console.print("3. Run: [cyan]lastfm fetch-api-key --key YOUR_API_KEY[/cyan]")


@app.command(name="fetch")
def fetch_scrobbles(
    username: str = typer.Argument(..., help="Last.fm username"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Output CSV path"),
    api_key: Optional[str] = typer.Option(None, "--api-key", help="Last.fm API key (or use fetch-api-key)"),
    max_pages: Optional[int] = typer.Option(None, "--max-pages", help="Max pages to fetch (for testing, default: all)"),
    start_year: Optional[int] = typer.Option(None, "--start-year", help="Only fetch scrobbles from this year onwards"),
):
    """Download your complete Last.fm scrobble history via API.

    Downloads all scrobbles and saves to CSV in the same format as the
    external export website, compatible with all analysis commands.
    """
    import time

    # Get API key
    if not api_key:
        api_key = lastfm_api.get_api_key()

    if not api_key:
        console.print("[red]No API key found![/red]")
        console.print("Run [cyan]lastfm fetch-api-key --help[/cyan] to set one up.")
        raise typer.Exit(1)

    # Determine output path
    if not output:
        timestamp = int(time.time())
        output = Path.cwd() / f"recenttracks-{username}-{timestamp}.csv"

    # Fetch scrobbles
    api = lastfm_api.LastFMAPI(api_key)
    try:
        count = api.fetch_all_scrobbles(username, output, max_pages=max_pages, start_year=start_year)
        if count > 0:
            console.print(f"\n[green]✓ Success![/green] Ready to analyze:")
            console.print(f"  [cyan]lastfm --csv {output.name} stats[/cyan]")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            console.print("[red]Invalid API key![/red]")
            console.print("Run [cyan]lastfm fetch-api-key --help[/cyan] to update it.")
        elif e.response.status_code == 404:
            console.print(f"[red]User not found:[/red] {username}")
        elif e.response.status_code == 500:
            console.print("[red]Last.fm API Error (500 Internal Server Error)[/red]")
            try:
                error_data = e.response.json()
                if "message" in error_data:
                    console.print(f"Server message: {error_data['message']}")
            except:
                console.print(f"Response: {e.response.text[:200]}")
        else:
            console.print(f"[red]HTTP Error {e.response.status_code}:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@app.command()
def artist(
    ctx: typer.Context,
    artist_name: str = typer.Argument(..., help="Artist name to look up"),
):
    """Show comprehensive artist summary across all years."""
    from . import crossref
    from collections import defaultdict
    import json
    from rich.table import Table

    csv = ctx.obj.get("csv") if ctx.obj else None
    fam = ctx.obj.get("familiarity") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    # Case-insensitive search
    matches = df[df["artist"].str.lower() == artist_name.lower()]

    if matches.empty:
        # Try partial match
        partial = df[df["artist"].str.lower().str.contains(artist_name.lower(), regex=False)]
        if partial.empty:
            console.print(f"[red]No plays found for '{artist_name}'[/red]")
            raise typer.Exit(1)
        else:
            console.print(f"[yellow]No exact match. Did you mean one of these?[/yellow]")
            for a in partial["artist"].unique()[:10]:
                console.print(f"  - {a}")
            raise typer.Exit(1)

    canonical_name = matches["artist"].mode().iloc[0]
    norm_name = crossref.normalize_for_matching(canonical_name)

    console.print(f"\n[bold magenta]═══ {canonical_name} ═══[/bold magenta]\n")

    # --- Listening History ---
    console.print("[bold cyan]Your Listening History[/bold cyan]")

    first = matches.sort_values("timestamp").iloc[0]
    console.print(f"First play: {first['timestamp']:%Y-%m-%d} - \"{first['track']}\"")
    console.print(f"Total plays: {len(matches):,}")

    # Plays by year
    yearly_plays = matches.groupby("year").size().sort_index()
    years_with_plays = yearly_plays[yearly_plays > 0]

    if len(years_with_plays) > 1:
        console.print("\n[dim]Plays by year:[/dim]")
        max_plays = years_with_plays.max()
        max_bar_width = 30

        for yr, plays in years_with_plays.items():
            bar_width = int((plays / max_plays) * max_bar_width)
            bar = "█" * bar_width
            console.print(f"  [dim]{yr}[/dim] [green]{bar}[/green] {plays}")

    # Top albums
    albums_df = matches[matches["album"] != ""]
    if not albums_df.empty:
        top_albums = albums_df.groupby("album").size().sort_values(ascending=False).head(5)
        console.print("\n[dim]Top albums:[/dim]")
        for album, plays in top_albums.items():
            console.print(f"  {album}: {plays} plays")

    # --- Critics Data ---
    console.print(f"\n[bold yellow]Critics' Selections[/bold yellow]")

    # Check all years from 2011-2025
    critics_by_year = {}
    for year in range(2011, 2026):
        json_path = get_critics_path(year)
        if not json_path.exists():
            continue

        try:
            with open(json_path) as f:
                raw_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        # Find matches for this artist
        year_matches = []
        for lst in raw_data:
            critic = lst['critic']
            for album in lst['albums']:
                if album['artist']:
                    if crossref.normalize_for_matching(album['artist']) == norm_name:
                        year_matches.append({
                            'critic': critic,
                            'album': album['title'],
                            'rank': album['rank'],
                        })

        if year_matches:
            # Group by album
            albums = defaultdict(list)
            for m in year_matches:
                albums[m['album']].append(m['critic'])
            critics_by_year[year] = {
                'total_critics': len(set(m['critic'] for m in year_matches)),
                'albums': {album: len(critics) for album, critics in albums.items()},
            }

    if not critics_by_year:
        console.print("[dim]Not found on any critics' lists (2011-2025)[/dim]")
    else:
        total_years = len(critics_by_year)
        total_critics = sum(y['total_critics'] for y in critics_by_year.values())
        console.print(f"Appears on critics' lists in {total_years} year(s), {total_critics} total list appearances\n")

        table = Table(show_header=True)
        table.add_column("Year", style="dim", justify="right")
        table.add_column("Album", style="yellow")
        table.add_column("Critics", justify="right", style="green")

        for year in sorted(critics_by_year.keys()):
            year_data = critics_by_year[year]
            first_row = True
            for album, count in sorted(year_data['albums'].items(), key=lambda x: -x[1]):
                table.add_row(
                    str(year) if first_row else "",
                    album,
                    str(count),
                )
                first_row = False

        console.print(table)

    # --- Comparison ---
    if critics_by_year:
        console.print(f"\n[bold green]Your Taste vs Critics[/bold green]")

        # Check which critic-selected albums you've listened to
        listened_albums = data.get_listened_albums(matches, min_familiarity=fam)
        # Extract just the album names for this artist
        your_albums = set(
            crossref.normalize_for_matching(album)
            for artist, album in listened_albums
            if artist == canonical_name
        )

        heard = []
        unheard = []
        for year, year_data in critics_by_year.items():
            for album, count in year_data['albums'].items():
                norm_album = crossref.normalize_for_matching(album)
                if norm_album in your_albums:
                    heard.append((year, album, count))
                else:
                    unheard.append((year, album, count))

        if heard:
            console.print(f"[green]✓[/green] You've heard {len(heard)} critic-selected album(s)")
        if unheard:
            console.print(f"[yellow]○[/yellow] {len(unheard)} critic-selected album(s) you haven't played:")
            for year, album, count in sorted(unheard, key=lambda x: (-x[2], -x[0])):
                console.print(f"    {year}: {album} ({count} critics)")

    # --- Similar Artists (User Embeddings) ---
    console.print(f"\n[bold blue]Similar in Your Library[/bold blue]")
    console.print("[dim]Based on your co-listening patterns[/dim]\n")

    try:
        from . import embeddings as emb_module

        csv_path = get_csv_path(csv)
        user_emb = emb_module.build_embeddings_from_csv(csv_path, min_plays=5)

        try:
            user_similar = user_emb.find_similar(canonical_name, top_n=5)

            if user_similar:
                for similar_artist, similarity in user_similar:
                    # Get play count for this artist
                    similar_plays = len(df[df["artist"] == similar_artist])
                    pct = similarity * 100
                    console.print(f"  {similar_artist} ({pct:.0f}%) - {similar_plays:,} plays")
            else:
                console.print("[dim]No similar artists found in your library[/dim]")
        except ValueError:
            console.print("[dim]Artist not in embeddings (needs more plays)[/dim]")
    except Exception as e:
        console.print(f"[dim]Could not load user embeddings: {e}[/dim]")

    # --- Similar Artists (Critics Embeddings) ---
    console.print(f"\n[bold yellow]Critics Also Group With[/bold yellow]")
    console.print("[dim]Artists critics list together (critical consensus)[/dim]\n")

    try:
        from . import embeddings as emb_module

        critics_emb = emb_module.get_or_build_critics_embeddings()

        try:
            critics_similar = critics_emb.find_similar(canonical_name, top_n=5)

            if critics_similar:
                for similar_artist_norm, similarity in critics_similar:
                    # Check if you've played this artist
                    similar_df = df[df["artist"].apply(lambda x: crossref.normalize_for_matching(x) == similar_artist_norm)]
                    if not similar_df.empty:
                        display_name = similar_df["artist"].mode().iloc[0]
                        plays = len(similar_df)
                        pct = similarity * 100
                        console.print(f"  {display_name} ({pct:.0f}%) - {plays:,} plays")
                    else:
                        # Artist you haven't played - mark it!
                        pct = similarity * 100
                        console.print(f"  {similar_artist_norm} ({pct:.0f}%) - [red]0 plays[/red] [yellow]← explore![/yellow]")
            else:
                console.print("[dim]Artist not found in critics data[/dim]")
        except ValueError:
            console.print("[dim]Artist not in critics embeddings[/dim]")
    except Exception as e:
        console.print(f"[dim]Could not load critics embeddings: {e}[/dim]")


@app.command()
def overview(
    ctx: typer.Context,
    html: Optional[Path] = typer.Option(None, "--html", help="Export to HTML file"),
):
    """Generate comprehensive all-time listening overview."""
    from . import crossref
    from collections import defaultdict
    import json
    from rich.table import Table

    csv = ctx.obj.get("csv") if ctx.obj else None
    fam = ctx.obj.get("familiarity") if ctx.obj else None

    df_full = data.load_scrobbles(get_csv_path(csv))

    if df_full.empty:
        console.print("[red]No listening data found[/red]")
        raise typer.Exit(1)

    # ============ GATHER ALL DATA ============

    # Basic all-time stats
    total_plays = len(df_full)
    unique_artists = df_full["artist"].nunique()
    unique_albums = df_full[df_full["album"] != ""]["album"].nunique()
    unique_tracks = df_full["track"].nunique()

    first_scrobble = df_full["timestamp"].min()
    last_scrobble = df_full["timestamp"].max()
    years_of_data = last_scrobble.year - first_scrobble.year + 1

    # Plays by year
    plays_by_year = df_full.groupby("year").size().to_dict()
    all_years = sorted(plays_by_year.keys())
    peak_year = max(plays_by_year.items(), key=lambda x: x[1])
    avg_plays_per_year = total_plays / years_of_data if years_of_data > 0 else 0

    # Top artists all-time with yearly breakdown
    top_artists_all = data.top_artists(df_full, 25)
    artist_contexts = []
    for _, row in top_artists_all.iterrows():
        artist_name = row["artist"]
        artist_plays = row["plays"]
        artist_df = df_full[df_full["artist"] == artist_name]

        first_play = artist_df["timestamp"].min()
        years_active = artist_df["year"].nunique()
        yearly_plays = artist_df.groupby("year").size().to_dict()

        # Create sparkline data for all years
        sparkline_data = [yearly_plays.get(y, 0) for y in all_years]

        artist_contexts.append({
            "name": artist_name,
            "plays": artist_plays,
            "first_year": first_play.year,
            "years_active": years_active,
            "sparkline_data": sparkline_data,
        })

    # Top albums all-time
    top_albums_all = data.top_albums(df_full, 25)
    album_contexts = []
    for _, row in top_albums_all.iterrows():
        artist_name = row["artist"]
        album_name = row["album"]
        plays = row["plays"]

        # When did you first hear this album?
        album_df = df_full[(df_full["artist"] == artist_name) & (df_full["album"] == album_name)]
        first_play = album_df["timestamp"].min()
        years_active = album_df["year"].nunique()

        album_contexts.append({
            "artist": artist_name,
            "album": album_name,
            "plays": plays,
            "first_year": first_play.year,
            "years_active": years_active,
        })

    # Discovery and abandonment patterns
    discoveries_by_year = {}
    for year in all_years:
        discovered = data.artists_discovered_in_year(df_full, year)
        discoveries_by_year[year] = len(discovered)

    # Consistency: artists played every year
    artist_year_counts = df_full.groupby("artist")["year"].nunique()
    consistent_artists = artist_year_counts[artist_year_counts >= min(10, years_of_data - 1)].sort_values(ascending=False)

    # Peak obsessions: artist + their peak year
    peak_obsessions = []
    for artist_name in df_full["artist"].unique()[:100]:  # Sample top artists
        artist_df = df_full[df_full["artist"] == artist_name]
        yearly = artist_df.groupby("year").size()
        if len(yearly) > 0:
            peak_year_artist = yearly.idxmax()
            peak_plays = yearly.max()
            total_plays = len(artist_df)
            if peak_plays >= 50:  # Significant obsession
                peak_obsessions.append({
                    "artist": artist_name,
                    "year": peak_year_artist,
                    "plays": peak_plays,
                    "total": total_plays,
                    "concentration": peak_plays / total_plays * 100,
                })
    peak_obsessions = sorted(peak_obsessions, key=lambda x: -x["plays"])[:15]

    # ============ CRITICS DATA (ALL YEARS) ============
    critics_all_time_stats = None
    top_aligned_critics = []
    most_acclaimed_albums = []

    # Load all available critics years
    all_critics_data = []
    for check_year in range(2011, 2026):
        json_path = get_critics_path(check_year)
        if json_path.exists():
            try:
                with open(json_path) as f:
                    year_data = json.load(f)
                    for lst in year_data:
                        lst["year"] = check_year  # Tag with year
                    all_critics_data.extend(year_data)
            except:
                pass

    if all_critics_data:
        # Build set of your albums
        listened_albums = data.get_listened_albums(df_full, min_familiarity=fam)
        your_albums = set()
        your_albums_with_plays = {}

        # Normalize listened albums for critic matching
        for artist, album in listened_albums:
            key = (crossref.normalize_for_matching(artist),
                   crossref.normalize_for_matching(album))
            your_albums.add(key)

        # Count plays for albums we've listened to
        df_with_albums = df_full[df_full["album"] != ""]
        for _, row in df_with_albums.iterrows():
            key = (crossref.normalize_for_matching(row["artist"]),
                   crossref.normalize_for_matching(row["album"]))
            if key in your_albums:  # Only count plays for albums we've properly listened to
                if key not in your_albums_with_plays:
                    your_albums_with_plays[key] = 0
                your_albums_with_plays[key] += 1

        # Calculate per-critic overlap
        critic_scores = defaultdict(lambda: {"overlap": 0, "total": 0})
        total_critic_albums = 0
        total_matched = 0

        for lst in all_critics_data:
            critic = lst["critic"]
            for album in lst["albums"]:
                if album["artist"] and album["title"]:
                    key = (crossref.normalize_for_matching(album["artist"]),
                           crossref.normalize_for_matching(album["title"]))
                    critic_scores[critic]["total"] += 1
                    total_critic_albums += 1
                    if key in your_albums:
                        critic_scores[critic]["overlap"] += 1
                        total_matched += 1

        # Calculate percentages
        for critic, scores in critic_scores.items():
            scores["pct"] = (scores["overlap"] / scores["total"] * 100) if scores["total"] > 0 else 0

        critics_all_time_stats = {
            "total_albums": total_critic_albums,
            "matched": total_matched,
            "overlap_pct": (total_matched / total_critic_albums * 100) if total_critic_albums > 0 else 0,
        }

        # Top aligned critics
        top_aligned_critics = sorted(
            [{"name": k, **v} for k, v in critic_scores.items()],
            key=lambda x: -x["overlap"]
        )[:10]

        # Most critically-acclaimed albums you've heard
        album_critic_counts = defaultdict(lambda: {"artist": "", "album": "", "critics": 0, "plays": 0})
        for lst in all_critics_data:
            for album in lst["albums"]:
                if album["artist"] and album["title"]:
                    key = (crossref.normalize_for_matching(album["artist"]),
                           crossref.normalize_for_matching(album["title"]))
                    if key in your_albums:
                        album_critic_counts[key]["artist"] = album["artist"]
                        album_critic_counts[key]["album"] = album["title"]
                        album_critic_counts[key]["critics"] += 1
                        album_critic_counts[key]["plays"] = your_albums_with_plays.get(key, 0)

        most_acclaimed_albums = sorted(
            album_critic_counts.values(),
            key=lambda x: -x["critics"]
        )[:15]

    # ============ MUSICBRAINZ METADATA (ALL-TIME) ============
    from . import musicbrainz_db
    import sqlite3 as sqlite3_overview

    mb_available = False
    genre_breakdown_all = []
    decade_breakdown = defaultdict(int)

    db_stats = musicbrainz_db.get_database_stats()
    if db_stats and db_stats.get("has_full_schema"):
        try:
            conn = sqlite3_overview.connect(musicbrainz_db.MUSICBRAINZ_DB)

            df_albums = df_full[df_full["album"] != ""].copy()
            df_albums = df_albums[df_albums["artist"].notna()]
            album_plays = df_albums.groupby(["artist", "album"]).size().reset_index(name="plays")

            genre_plays = defaultdict(int)
            albums_matched = 0

            for _, row in album_plays.iterrows():
                info = musicbrainz_db.lookup_release(row["artist"], row["album"], conn)
                if info:
                    albums_matched += 1

                    # Genres
                    if info.genres:
                        for g in info.genres:
                            genre_plays[g] += row["plays"]

                    # Decades
                    if info.year:
                        decade = (info.year // 10) * 10
                        decade_breakdown[decade] += row["plays"]

            conn.close()

            if albums_matched > 0:
                mb_available = True

                # Genre breakdown
                sorted_genres = sorted(genre_plays.items(), key=lambda x: -x[1])
                total_genre_plays = sum(g[1] for g in sorted_genres)
                genre_breakdown_all = [
                    {"name": g, "plays": p, "pct": p / total_genre_plays * 100}
                    for g, p in sorted_genres[:15]
                ]

        except Exception as e:
            mb_available = False

    # ============ BRIDGE ARTISTS ============
    # Find artists that connect different regions of your taste
    # Uses similarity variance - artists whose similar artists are diverse
    from . import embeddings as emb_module

    bridge_artists = []
    try:
        csv_path = get_csv_path(csv)
        user_emb = emb_module.build_embeddings_from_csv(csv_path, min_plays=5)

        if user_emb.embeddings is not None and len(user_emb.embeddings) >= 50:
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity

            # For each artist, measure the diversity of their similar artists
            # Bridge artists have similar artists that are NOT similar to each other
            artist_diversity = []

            # Get top artists by play count
            artist_plays_lookup = {}
            for artist_name in user_emb.artist_to_idx.keys():
                plays = len(df_full[df_full["artist"] == artist_name])
                artist_plays_lookup[artist_name] = plays

            top_by_plays = sorted(artist_plays_lookup.items(), key=lambda x: -x[1])[:200]

            for artist_name, plays in top_by_plays:
                try:
                    similar = user_emb.find_similar(artist_name, top_n=8)

                    if len(similar) >= 4:
                        # Get embeddings of similar artists
                        similar_embeddings = []
                        for sim_artist, _ in similar:
                            if sim_artist in user_emb.artist_to_idx:
                                idx = user_emb.artist_to_idx[sim_artist]
                                similar_embeddings.append(user_emb.embeddings[idx])

                        if len(similar_embeddings) >= 4:
                            # Calculate pairwise similarity between similar artists
                            sim_matrix = cosine_similarity(similar_embeddings)
                            # Get off-diagonal elements (similarity between pairs)
                            off_diag = sim_matrix[np.triu_indices_from(sim_matrix, k=1)]

                            # Low average similarity = diverse neighborhood = bridge artist
                            avg_internal_sim = np.mean(off_diag)

                            # Bridge artists have low internal similarity (diverse neighbors)
                            if avg_internal_sim < 0.6:  # Neighbors are not too similar to each other
                                artist_diversity.append({
                                    "artist": artist_name,
                                    "diversity": 1 - avg_internal_sim,  # Higher = more diverse
                                    "plays": plays,
                                })
                except ValueError:
                    continue

            # Sort by diversity
            bridge_artists = sorted(artist_diversity, key=lambda x: (-x["diversity"], -x["plays"]))[:10]
    except Exception:
        pass  # Skip if embeddings not available

    # ============ CONSOLE OUTPUT ============
    if not html:
        from datetime import datetime

        console.print(f"\n[bold magenta]{'═' * 50}[/bold magenta]")
        console.print(f"[bold magenta]  YOUR LISTENING OVERVIEW[/bold magenta]")
        console.print(f"[bold magenta]{'═' * 50}[/bold magenta]\n")

        # The Big Picture
        console.print("[bold cyan]📊 THE BIG PICTURE[/bold cyan]\n")
        console.print(f"  [bold]{total_plays:,}[/bold] total scrobbles")
        console.print(f"  [dim]from[/dim] {first_scrobble:%B %d, %Y} [dim]to[/dim] {last_scrobble:%B %d, %Y}")
        console.print(f"  [bold]{years_of_data}[/bold] years of listening")
        console.print(f"  [dim]across[/dim] [bold]{unique_artists:,}[/bold] artists, [bold]{unique_albums:,}[/bold] albums, [bold]{unique_tracks:,}[/bold] tracks")
        console.print(f"  [dim]Peak year:[/dim] [bold]{peak_year[0]}[/bold] ({peak_year[1]:,} plays)")
        console.print(f"  [dim]Average:[/dim] {avg_plays_per_year:,.0f} plays/year")
        console.print()

        # Listening over time (sparkline)
        console.print("[dim]Listening intensity:[/dim]")
        max_plays = max(plays_by_year.values())
        blocks = " ▁▂▃▄▅▆▇█"
        sparkline = "".join(blocks[min(8, int(plays_by_year.get(y, 0) / max_plays * 8))] for y in all_years)
        console.print(f"  {all_years[0]} {sparkline} {all_years[-1]}")
        console.print()

        # All-time favorites
        console.print("[bold cyan]🎸 YOUR ALL-TIME FAVORITES[/bold cyan]")
        console.print("[dim]  Top artists across your entire listening history[/dim]\n")

        for i, ctx in enumerate(artist_contexts[:20], 1):
            # Create sparkline for this artist
            max_artist_plays = max(ctx["sparkline_data"]) if ctx["sparkline_data"] else 1
            sparkline = "".join(
                blocks[min(8, int(p / max_artist_plays * 8))] if max_artist_plays > 0 else " "
                for p in ctx["sparkline_data"]
            )

            console.print(f"  {i:2}. [bold]{ctx['name']}[/bold] — {ctx['plays']:,} plays")
            console.print(f"      [dim]{all_years[0]} {sparkline} {all_years[-1]}[/dim]")
            console.print(f"      [dim]Fan since {ctx['first_year']} · Active {ctx['years_active']} years[/dim]")

        console.print()

        # Discovery patterns
        console.print("[bold cyan]🔍 YOUR MUSICAL JOURNEY[/bold cyan]\n")
        console.print(f"  You've discovered [bold]{unique_artists:,}[/bold] artists over {years_of_data} years")
        console.print(f"  [dim]Average:[/dim] {unique_artists / years_of_data:.0f} new artists/year\n")

        console.print("[dim]Discovery rate over time:[/dim]")
        max_discoveries = max(discoveries_by_year.values()) if discoveries_by_year else 1
        disc_sparkline = "".join(
            blocks[min(8, int(discoveries_by_year.get(y, 0) / max_discoveries * 8))]
            for y in all_years
        )
        console.print(f"  {all_years[0]} {disc_sparkline} {all_years[-1]}")
        console.print()

        # Top albums
        console.print("[bold cyan]💿 YOUR ALL-TIME ALBUMS[/bold cyan]")
        console.print("[dim]  The records you've lived with[/dim]\n")

        table = Table(show_header=True, box=None)
        table.add_column("#", style="dim", width=3)
        table.add_column("Album", style="yellow")
        table.add_column("Artist", style="cyan")
        table.add_column("Plays", justify="right", style="green")
        table.add_column("Years", justify="right", style="dim")

        for i, ctx in enumerate(album_contexts[:15], 1):
            table.add_row(
                str(i),
                ctx["album"][:35] + "..." if len(ctx["album"]) > 35 else ctx["album"],
                ctx["artist"][:25] + "..." if len(ctx["artist"]) > 25 else ctx["artist"],
                str(ctx["plays"]),
                str(ctx["years_active"]),
            )

        console.print(table)
        console.print()

        # Consistency
        if len(consistent_artists) > 0:
            console.print("[bold cyan]❤️  LONGTIME LOYALTY[/bold cyan]")
            console.print(f"[dim]  Artists you've played for {min(10, years_of_data - 1)}+ years[/dim]\n")

            for artist, year_count in consistent_artists.head(15).items():
                artist_total = df_full[df_full["artist"] == artist].shape[0]
                console.print(f"  [bold]{artist}[/bold] — {artist_total:,} plays across {int(year_count)} years")
            console.print()

        # Peak obsessions
        if peak_obsessions:
            console.print("[bold cyan]🔥 PEAK OBSESSIONS[/bold cyan]")
            console.print("[dim]  Artists and the years you couldn't stop playing them[/dim]\n")

            for obs in peak_obsessions[:10]:
                console.print(f"  [bold]{obs['artist']}[/bold] — {obs['year']}")
                console.print(f"  [dim]{obs['plays']} plays that year ({obs['concentration']:.0f}% of all {obs['artist']} plays)[/dim]")
            console.print()

        # Bridge artists
        if bridge_artists:
            console.print("[bold cyan]🌉 BRIDGE ARTISTS[/bold cyan]")
            console.print("[dim]  Artists whose similar artists are diverse (connecting different tastes)[/dim]\n")

            for ba in bridge_artists[:8]:
                diversity_pct = ba['diversity'] * 100
                console.print(f"  [bold]{ba['artist']}[/bold] — {diversity_pct:.0f}% neighborhood diversity ({ba['plays']:,} plays)")
            console.print()

        # Critics section
        if critics_all_time_stats:
            console.print("[bold cyan]🏆 YOU & THE CRITICS (ALL-TIME)[/bold cyan]")
            console.print(f"[dim]  Your alignment across {len(all_critics_data)} critic lists (2011-2025)[/dim]\n")

            console.print(f"  Overall alignment: [bold]{critics_all_time_stats['overlap_pct']:.1f}%[/bold]")
            console.print(f"  [dim]{critics_all_time_stats['matched']:,} of {critics_all_time_stats['total_albums']:,} albums[/dim]\n")

            if top_aligned_critics:
                console.print("  [bold]Critics who share your taste:[/bold]")
                for c in top_aligned_critics[:8]:
                    if c["overlap"] > 0:
                        console.print(f"    {c['name']}: {c['overlap']}/{c['total']} ({c['pct']:.0f}%)")
                console.print()

            if most_acclaimed_albums:
                console.print("  [bold]Your most critically-acclaimed albums:[/bold]\n")

                table = Table(show_header=False, box=None, padding=(0, 2))
                table.add_column("Album")
                table.add_column("Plays", justify="right", style="green")
                table.add_column("Critics", justify="right", style="yellow")

                for a in most_acclaimed_albums[:12]:
                    table.add_row(
                        f"{a['artist']} — {a['album']}"[:50],
                        str(a["plays"]),
                        f"{a['critics']} lists",
                    )

                console.print(table)
                console.print()

        # MusicBrainz metadata
        if mb_available:
            if genre_breakdown_all:
                console.print("[bold cyan]🎸 YOUR SOUND (ALL-TIME)[/bold cyan]")
                console.print("[dim]  Top genres across your entire listening history[/dim]\n")

                for g in genre_breakdown_all[:10]:
                    console.print(f"  {g['name']:<25} {g['pct']:>5.1f}%")
                console.print()

            if decade_breakdown:
                console.print("[bold cyan]📅 MUSIC BY DECADE[/bold cyan]")
                console.print("[dim]  When the music you listen to was released[/dim]\n")

                sorted_decades = sorted(decade_breakdown.items())
                total_decade_plays = sum(decade_breakdown.values())

                for decade, plays in sorted_decades:
                    pct = plays / total_decade_plays * 100 if total_decade_plays > 0 else 0
                    decade_label = f"{decade}s" if decade >= 1950 else "Pre-1950"
                    bar_width = int(pct / 2)
                    bar = "█" * bar_width
                    console.print(f"  {decade_label:<10} [green]{bar}[/green] {pct:>5.1f}%")
                console.print()

        console.print(f"\n[dim]{'─' * 50}[/dim]")
        console.print(f"[dim]Generated {datetime.now():%Y-%m-%d %H:%M}[/dim]\n")

    else:
        # HTML output
        html_content = generate_overview_html(
            total_plays=total_plays,
            first_scrobble=first_scrobble,
            last_scrobble=last_scrobble,
            years_of_data=years_of_data,
            unique_artists=unique_artists,
            unique_albums=unique_albums,
            unique_tracks=unique_tracks,
            peak_year=peak_year,
            avg_plays_per_year=avg_plays_per_year,
            plays_by_year=plays_by_year,
            all_years=all_years,
            artist_contexts=artist_contexts,
            album_contexts=album_contexts,
            discoveries_by_year=discoveries_by_year,
            consistent_artists=consistent_artists,
            peak_obsessions=peak_obsessions,
            critics_all_time_stats=critics_all_time_stats,
            top_aligned_critics=top_aligned_critics,
            most_acclaimed_albums=most_acclaimed_albums,
            mb_available=mb_available,
            genre_breakdown_all=genre_breakdown_all,
            decade_breakdown=decade_breakdown,
        )
        html.write_text(html_content)
        console.print(f"[green]Generated HTML overview: {html}[/green]")

        # Open in browser
        absolute_path = html.resolve()
        webbrowser.open(f"file://{absolute_path}")
        console.print(f"[dim]Opening in browser...[/dim]")


@app.command()
def review(
    ctx: typer.Context,
    html: Optional[Path] = typer.Option(None, "--html", help="Export to HTML file"),
):
    """Generate comprehensive year-in-review."""
    from . import crossref
    from collections import defaultdict
    import json
    from rich.table import Table

    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025
    fam = ctx.obj.get("familiarity") if ctx.obj else None

    df_full = data.load_scrobbles(get_csv_path(csv))
    df = data.filter_by_year(df_full, year)

    if df.empty:
        console.print(f"[red]No listening data for {year}[/red]")
        raise typer.Exit(1)

    # ============ GATHER ALL DATA ============

    # Basic stats
    total_plays = len(df)
    unique_artists = df["artist"].nunique()
    unique_albums = df[df["album"] != ""]["album"].nunique()
    unique_tracks = df["track"].nunique()

    # Previous year comparison
    df_prev = data.filter_by_year(df_full, year - 1)
    prev_plays = len(df_prev) if not df_prev.empty else None

    # Top artists
    top_artists_df = data.top_artists(df, 15)

    # For each top artist, get historical context
    artist_contexts = []
    for _, row in top_artists_df.iterrows():
        artist_name = row["artist"]
        artist_plays = row["plays"]
        artist_df = df_full[df_full["artist"] == artist_name]
        first_play = artist_df["timestamp"].min()
        first_year = first_play.year
        total_all_time = len(artist_df)

        # Plays by year for this artist
        yearly = artist_df.groupby("year").size()

        # Is this their peak year?
        peak_year = yearly.idxmax()
        is_peak = (peak_year == year)

        # Is this a new discovery?
        is_new = (first_year == year)

        artist_contexts.append({
            "name": artist_name,
            "plays": artist_plays,
            "first_year": first_year,
            "total_all_time": total_all_time,
            "is_peak": is_peak,
            "is_new": is_new,
            "yearly": yearly.to_dict(),
        })

    # Top albums
    top_albums_df = data.top_albums(df, 15)
    album_contexts = []
    for _, row in top_albums_df.iterrows():
        artist_name = row["artist"]
        album_name = row["album"]
        plays = row["plays"]

        # When did you first hear this album?
        album_df = df_full[(df_full["artist"] == artist_name) & (df_full["album"] == album_name)]
        first_play = album_df["timestamp"].min()

        album_contexts.append({
            "artist": artist_name,
            "album": album_name,
            "plays": plays,
            "first_play": first_play,
            "discovered_this_year": first_play.year == year,
        })

    # New discoveries
    discovered = data.artists_discovered_in_year(df_full, year)
    new_artists_count = len(discovered)
    top_discoveries = []
    for _, row in discovered.head(10).iterrows():
        top_discoveries.append({
            "name": row["artist"],
            "plays": int(row["plays_in_year"]),
            "first_track": row["track"],
            "first_date": row["timestamp"],
        })

    # ============ CRITICS DATA ============
    critics_available = False
    critics_data = None
    matched_albums = []
    unheard_weighted = []
    overlooked_gems = []
    your_overlap_pct = 0
    critic_overlap_stats = []

    json_path = get_critics_path(year)
    if json_path.exists():
        try:
            critics_data = crossref.load_critics_data(json_path)
            with open(json_path) as f:
                raw_critics = json.load(f)
            critics_available = True

            # Build your albums set
            listened_albums = data.get_listened_albums(df, min_familiarity=fam)
            your_albums = set()
            for artist, album in listened_albums:
                key = (crossref.normalize_for_matching(artist),
                       crossref.normalize_for_matching(album))
                your_albums.add(key)

            # Match with critics
            results = crossref.match_with_history(critics_data, df_full, year=year, min_familiarity=fam)
            your_overlap_pct = (results["stats"]["matched_count"] /
                               results["stats"]["total_critics_albums"] * 100)

            # Matched albums (critics picks you've heard)
            for m in results["matched"][:10]:
                matched_albums.append({
                    "artist": m.artist,
                    "album": m.album,
                    "critics_count": m.critics_count,
                    "your_plays": m.your_plays,
                })

            # Calculate critic overlap scores
            critic_scores = {}
            for lst in raw_critics:
                critic = lst["critic"]
                total = len(lst["albums"])
                overlap = 0
                for album in lst["albums"]:
                    if album["artist"] and album["title"]:
                        key = (crossref.normalize_for_matching(album["artist"]),
                               crossref.normalize_for_matching(album["title"]))
                        if key in your_albums:
                            overlap += 1
                critic_scores[critic] = {
                    "overlap": overlap,
                    "total": total,
                    "pct": (overlap / total * 100) if total > 0 else 0,
                }

            # Top aligned critics
            critic_overlap_stats = sorted(
                [{"name": k, **v} for k, v in critic_scores.items()],
                key=lambda x: -x["overlap"]
            )[:10]

            # Weighted unheard recommendations
            album_critics_map = {}
            for lst in raw_critics:
                critic = lst["critic"]
                for album in lst["albums"]:
                    if album["artist"] and album["title"]:
                        key = (crossref.normalize_for_matching(album["artist"]),
                               crossref.normalize_for_matching(album["title"]))
                        if key not in your_albums:
                            if key not in album_critics_map:
                                album_critics_map[key] = {
                                    "artist": album["artist"],
                                    "album": album["title"],
                                    "critics": [],
                                }
                            album_critics_map[key]["critics"].append(critic)

            for key, album_data in album_critics_map.items():
                score = sum(critic_scores[c]["pct"] / 100 for c in album_data["critics"])
                album_data["score"] = score
                album_data["critics_count"] = len(album_data["critics"])

            unheard_weighted = sorted(
                album_critics_map.values(),
                key=lambda x: -x["score"]
            )[:10]

            # Overlooked gems - your top artists with new albums not picked by critics
            critics_artists = set()
            for lst in raw_critics:
                for album in lst["albums"]:
                    if album["artist"]:
                        critics_artists.add(crossref.normalize_for_matching(album["artist"]))

            # Find albums first heard this year
            df_with_albums_full = df_full[df_full["album"] != ""].copy()
            first_plays = df_with_albums_full.sort_values("timestamp").groupby(
                ["artist", "album"]
            ).first().reset_index()
            new_albums_this_year = first_plays[first_plays["year"] == year]

            # Filter to only albums actually released in the review year (using MusicBrainz)
            from . import musicbrainz_db
            import sqlite3 as sqlite3_gems

            db_stats = musicbrainz_db.get_database_stats()
            if db_stats and db_stats.get("has_full_schema"):
                conn_mb = sqlite3_gems.connect(musicbrainz_db.MUSICBRAINZ_DB)
                albums_released_this_year = []

                for _, row in new_albums_this_year.iterrows():
                    info = musicbrainz_db.lookup_release(row["artist"], row["album"], conn_mb)
                    if info and info.year == year:
                        albums_released_this_year.append({
                            "artist": row["artist"],
                            "album": row["album"],
                            "norm_artist": crossref.normalize_for_matching(row["artist"])
                        })

                conn_mb.close()

                # Build set of artists with actual new releases this year
                new_album_artists = set(a["norm_artist"] for a in albums_released_this_year)

                # Top artists with new albums not in critics lists
                for ctx in artist_contexts:
                    norm_name = crossref.normalize_for_matching(ctx["name"])
                    if norm_name not in critics_artists and norm_name in new_album_artists:
                        # Get the albums released this year
                        artist_albums = [a["album"] for a in albums_released_this_year if a["norm_artist"] == norm_name]
                        overlooked_gems.append({
                            "artist": ctx["name"],
                            "plays": ctx["plays"],
                            "albums": artist_albums[:2],  # Top 2 new albums
                        })
            else:
                # Fallback: use first-heard albums without release year filtering
                new_album_artists = set()
                for _, row in new_albums_this_year.iterrows():
                    new_album_artists.add(crossref.normalize_for_matching(row["artist"]))

                for ctx in artist_contexts:
                    norm_name = crossref.normalize_for_matching(ctx["name"])
                    if norm_name not in critics_artists and norm_name in new_album_artists:
                        artist_new = new_albums_this_year[
                            new_albums_this_year["artist"].apply(
                                lambda x: crossref.normalize_for_matching(x) == norm_name
                            )
                        ]
                        albums = artist_new["album"].tolist()
                        overlooked_gems.append({
                            "artist": ctx["name"],
                            "plays": ctx["plays"],
                            "albums": albums[:2],
                        })

            overlooked_gems = overlooked_gems[:10]

        except Exception as e:
            critics_available = False

    # ============ MUSICBRAINZ METADATA ============
    from . import musicbrainz_db
    import sqlite3

    mb_available = False
    genre_breakdown = []
    genre_all_time = []  # For comparison
    label_breakdown = []
    country_breakdown = []
    release_type_breakdown = []
    new_vs_catalog = {"new_pct": 0, "catalog_pct": 0, "avg_lag": 0}

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
            type_plays = defaultdict(int)
            new_releases = 0
            catalog_releases = 0
            discovery_lags = []
            albums_matched = 0

            for _, row in album_plays.iterrows():
                artist = row["artist"]
                album_name = row["album"]
                plays = row["plays"]

                info = musicbrainz_db.lookup_release(artist, album_name, conn)
                if info:
                    albums_matched += 1

                    # Genres
                    if info.genres:
                        for g in info.genres:
                            genre_plays[g] += plays

                    # Labels
                    if info.labels:
                        for l in info.labels:
                            label_plays[l] += plays

                    # Country
                    if info.country:
                        country_plays[info.country] += plays

                    # Release type
                    release_type = info.release_type or "unknown"
                    type_plays[release_type] += plays

                    # New vs catalog
                    if info.year == year:
                        new_releases += plays
                    else:
                        catalog_releases += plays
                        discovery_lags.append(year - info.year)

            conn.close()

            if albums_matched > 0:
                mb_available = True
                total_matched_plays = new_releases + catalog_releases

                # Genre breakdown (top 10)
                sorted_genres = sorted(genre_plays.items(), key=lambda x: -x[1])
                total_genre_plays = sum(g[1] for g in sorted_genres)
                genre_breakdown = [
                    {"name": g, "plays": p, "pct": p / total_genre_plays * 100}
                    for g, p in sorted_genres[:10]
                ]

                # Get all-time genres for comparison
                df_all_albums = df_full[df_full["album"] != ""].copy()
                df_all_albums = df_all_albums[df_all_albums["artist"].notna()]
                all_album_plays = df_all_albums.groupby(["artist", "album"]).size().reset_index(name="plays")
                genre_all_plays = defaultdict(int)

                conn = sqlite3.connect(musicbrainz_db.MUSICBRAINZ_DB)
                for _, row in all_album_plays.head(2000).iterrows():  # Sample for speed
                    info = musicbrainz_db.lookup_release(row["artist"], row["album"], conn)
                    if info and info.genres:
                        for g in info.genres:
                            genre_all_plays[g] += row["plays"]
                conn.close()

                sorted_all_genres = sorted(genre_all_plays.items(), key=lambda x: -x[1])
                total_all_genre = sum(g[1] for g in sorted_all_genres)
                genre_all_time = {
                    g: p / total_all_genre * 100
                    for g, p in sorted_all_genres[:20]
                }

                # Label breakdown (top 10)
                sorted_labels = sorted(label_plays.items(), key=lambda x: -x[1])
                total_label_plays = sum(l[1] for l in sorted_labels)
                label_breakdown = [
                    {"name": l, "plays": p, "pct": p / total_label_plays * 100 if total_label_plays > 0 else 0}
                    for l, p in sorted_labels[:10]
                ]

                # Country breakdown
                country_names = {
                    "US": "United States", "GB": "United Kingdom", "JP": "Japan",
                    "DE": "Germany", "FR": "France", "CA": "Canada", "AU": "Australia",
                    "SE": "Sweden", "NL": "Netherlands", "XW": "Worldwide", "XE": "Europe",
                }
                sorted_countries = sorted(country_plays.items(), key=lambda x: -x[1])
                total_country_plays = sum(c[1] for c in sorted_countries)
                country_breakdown = [
                    {"code": c, "name": country_names.get(c, c), "plays": p,
                     "pct": p / total_country_plays * 100 if total_country_plays > 0 else 0}
                    for c, p in sorted_countries[:8]
                ]

                # Release type breakdown
                sorted_types = sorted(type_plays.items(), key=lambda x: -x[1])
                total_type_plays = sum(t[1] for t in sorted_types)
                release_type_breakdown = [
                    {"type": t.replace("-", " ").title(), "plays": p,
                     "pct": p / total_type_plays * 100 if total_type_plays > 0 else 0}
                    for t, p in sorted_types
                ]

                # New vs catalog
                if total_matched_plays > 0:
                    new_vs_catalog = {
                        "new_pct": new_releases / total_matched_plays * 100,
                        "catalog_pct": catalog_releases / total_matched_plays * 100,
                        "avg_lag": sum(discovery_lags) / len(discovery_lags) if discovery_lags else 0,
                        "new_plays": new_releases,
                        "catalog_plays": catalog_releases,
                    }

        except Exception as e:
            mb_available = False

    # ============ CONSOLE OUTPUT ============
    if not html:
        from datetime import datetime

        console.print(f"\n[bold magenta]{'═' * 50}[/bold magenta]")
        console.print(f"[bold magenta]  YOUR {year} IN MUSIC[/bold magenta]")
        console.print(f"[bold magenta]{'═' * 50}[/bold magenta]\n")

        # The Big Picture
        console.print("[bold cyan]📊 THE BIG PICTURE[/bold cyan]\n")
        console.print(f"  You played [bold]{total_plays:,}[/bold] tracks this year")
        console.print(f"  [dim]across[/dim] [bold]{unique_artists:,}[/bold] artists, [bold]{unique_albums:,}[/bold] albums, [bold]{unique_tracks:,}[/bold] unique tracks")
        if prev_plays:
            diff = total_plays - prev_plays
            pct = abs(diff) / prev_plays * 100
            direction = "up" if diff > 0 else "down"
            color = "green" if diff > 0 else "red"
            console.print(f"  [{color}]{direction} {pct:.0f}% from {year-1}[/{color}]")
        console.print()

        # Your Obsessions
        console.print("[bold cyan]🎸 YOUR OBSESSIONS[/bold cyan]")
        console.print("[dim]  The artists you couldn't stop playing[/dim]\n")

        max_plays = artist_contexts[0]["plays"] if artist_contexts else 1
        for i, ctx in enumerate(artist_contexts[:10], 1):
            bar_width = int((ctx["plays"] / max_plays) * 25)
            bar = "█" * bar_width

            badge = ""
            if ctx["is_new"]:
                badge = " [yellow]★ NEW[/yellow]"
            elif ctx["is_peak"]:
                badge = " [green]↑ PEAK[/green]"

            console.print(f"  {i:2}. [bold]{ctx['name']}[/bold]{badge}")
            console.print(f"      [green]{bar}[/green] {ctx['plays']} plays")
            if not ctx["is_new"]:
                console.print(f"      [dim]Fan since {ctx['first_year']} · {ctx['total_all_time']:,} all-time plays[/dim]")
            console.print()

        # The Albums You Lived With
        console.print("[bold cyan]💿 THE ALBUMS YOU LIVED WITH[/bold cyan]\n")

        table = Table(show_header=True, box=None)
        table.add_column("#", style="dim", width=3)
        table.add_column("Album", style="yellow")
        table.add_column("Artist", style="cyan")
        table.add_column("Plays", justify="right", style="green")
        table.add_column("", style="dim")

        for i, ctx in enumerate(album_contexts[:10], 1):
            note = "★ Discovered this year" if ctx["discovered_this_year"] else f"First heard {ctx['first_play']:%b %Y}"
            table.add_row(
                str(i),
                ctx["album"][:30] + "..." if len(ctx["album"]) > 30 else ctx["album"],
                ctx["artist"][:20] + "..." if len(ctx["artist"]) > 20 else ctx["artist"],
                str(ctx["plays"]),
                note,
            )

        console.print(table)
        console.print()

        # New Discoveries
        console.print("[bold cyan]🔍 NEW DISCOVERIES[/bold cyan]")
        console.print(f"[dim]  You discovered [bold]{new_artists_count}[/bold] new artists this year[/dim]\n")

        console.print("  [bold]The ones that stuck:[/bold]\n")
        for i, disc in enumerate(top_discoveries[:7], 1):
            console.print(f"  {i}. [bold]{disc['name']}[/bold] — {disc['plays']} plays")
            console.print(f"     [dim]First heard \"{disc['first_track'][:40]}\" on {disc['first_date']:%b %d}[/dim]")
        console.print()

        # Critics Section
        if critics_available:
            console.print("[bold cyan]🏆 YOU & THE CRITICS[/bold cyan]")
            console.print(f"[dim]  Your taste aligned with critics on [bold]{your_overlap_pct:.1f}%[/bold] of their picks[/dim]\n")

            if critic_overlap_stats:
                console.print("  [bold]Critics who share your taste:[/bold]")
                for c in critic_overlap_stats[:5]:
                    if c["overlap"] > 0:
                        console.print(f"    {c['name']}: {c['overlap']}/{c['total']} albums ({c['pct']:.0f}%)")
                console.print()

            if matched_albums:
                console.print("  [bold]Your favorites that critics also loved:[/bold]\n")
                table = Table(show_header=False, box=None, padding=(0, 2))
                table.add_column("Album")
                table.add_column("Your Plays", justify="right", style="green")
                table.add_column("Critics", justify="right", style="yellow")

                for m in matched_albums[:7]:
                    table.add_row(
                        f"{m['artist']} — {m['album']}"[:45],
                        str(m["your_plays"]),
                        f"{m['critics_count']} critics",
                    )
                console.print(table)
                console.print()

            # Hidden Gems
            if overlooked_gems:
                console.print("[bold cyan]💎 YOUR HIDDEN GEMS[/bold cyan]")
                console.print("[dim]  Artists you championed that critics missed[/dim]\n")

                for gem in overlooked_gems[:7]:
                    albums_str = ", ".join(gem["albums"][:2])
                    console.print(f"  [bold]{gem['artist']}[/bold] — {gem['plays']} plays")
                    if albums_str:
                        console.print(f"  [dim]{albums_str}[/dim]")
                console.print()

            # Recommendations
            if unheard_weighted:
                console.print("[bold cyan]🎧 WHAT YOU MIGHT BE MISSING[/bold cyan]")
                console.print("[dim]  Recommended by critics who share your taste[/dim]\n")

                table = Table(show_header=True, box=None)
                table.add_column("Album", style="yellow")
                table.add_column("Artist", style="cyan")
                table.add_column("Score", justify="right", style="magenta")

                for rec in unheard_weighted[:10]:
                    table.add_row(
                        rec["album"][:30],
                        rec["artist"][:25],
                        f"{rec['score']:.2f}",
                    )
                console.print(table)

        # MusicBrainz metadata sections
        if mb_available:
            console.print()

            # Genre breakdown
            if genre_breakdown:
                console.print("[bold cyan]🎸 YOUR SOUND[/bold cyan]")
                console.print("[dim]  The genres that defined your year[/dim]\n")

                max_genre_pct = genre_breakdown[0]["pct"] if genre_breakdown else 1
                for g in genre_breakdown[:7]:
                    bar_width = int((g["pct"] / max_genre_pct) * 20)
                    bar = "█" * bar_width

                    # Compare to all-time
                    all_time_pct = genre_all_time.get(g["name"], 0)
                    diff = g["pct"] - all_time_pct
                    trend = ""
                    if diff > 5:
                        trend = f" [green]↑ +{diff:.0f}% vs all-time[/green]"
                    elif diff < -5:
                        trend = f" [red]↓ {diff:.0f}% vs all-time[/red]"

                    console.print(f"  {g['name']:<18} [green]{bar}[/green] {g['pct']:>5.1f}%{trend}")
                console.print()

            # New vs Catalog
            if new_vs_catalog.get("new_pct", 0) > 0 or new_vs_catalog.get("catalog_pct", 0) > 0:
                console.print("[bold cyan]📅 NEW VS CATALOG[/bold cyan]")
                console.print("[dim]  How current is your listening?[/dim]\n")

                new_bar = "█" * int(new_vs_catalog["new_pct"] / 5)
                cat_bar = "░" * int(new_vs_catalog["catalog_pct"] / 5)
                console.print(f"  [green]{new_bar}[/green][dim]{cat_bar}[/dim]")
                console.print(f"  [green]{new_vs_catalog['new_pct']:.0f}% new releases[/green] · [dim]{new_vs_catalog['catalog_pct']:.0f}% catalog[/dim]")
                if new_vs_catalog.get("avg_lag", 0) > 0:
                    console.print(f"  [dim]Average discovery lag: {new_vs_catalog['avg_lag']:.1f} years[/dim]")
                console.print()

            # Top Labels
            if label_breakdown:
                console.print("[bold cyan]🏷️  YOUR LABELS[/bold cyan]")
                console.print("[dim]  The record labels you gravitate toward[/dim]\n")

                for l in label_breakdown[:5]:
                    console.print(f"  {l['name'][:30]:<30} {l['pct']:>5.1f}%")
                console.print()

            # Countries
            if country_breakdown:
                console.print("[bold cyan]🌍 WHERE YOUR MUSIC COMES FROM[/bold cyan]\n")
                for c in country_breakdown[:5]:
                    console.print(f"  {c['name']:<20} {c['pct']:>5.1f}%")
                console.print()

            # Release Types
            if release_type_breakdown:
                console.print("[bold cyan]💿 WHAT YOU LISTEN TO[/bold cyan]\n")
                for t in release_type_breakdown[:4]:
                    console.print(f"  {t['type']:<20} {t['pct']:>5.1f}%")
                console.print()

        console.print(f"\n[dim]{'─' * 50}[/dim]")
        console.print(f"[dim]Generated {datetime.now():%Y-%m-%d %H:%M}[/dim]\n")

    # ============ HTML OUTPUT ============
    else:
        html_content = generate_review_html(
            year=year,
            total_plays=total_plays,
            unique_artists=unique_artists,
            unique_albums=unique_albums,
            unique_tracks=unique_tracks,
            prev_plays=prev_plays,
            artist_contexts=artist_contexts,
            album_contexts=album_contexts,
            new_artists_count=new_artists_count,
            top_discoveries=top_discoveries,
            critics_available=critics_available,
            your_overlap_pct=your_overlap_pct,
            critic_overlap_stats=critic_overlap_stats,
            matched_albums=matched_albums,
            overlooked_gems=overlooked_gems,
            unheard_weighted=unheard_weighted,
            # MusicBrainz metadata
            mb_available=mb_available,
            genre_breakdown=genre_breakdown,
            genre_all_time=genre_all_time,
            label_breakdown=label_breakdown,
            country_breakdown=country_breakdown,
            release_type_breakdown=release_type_breakdown,
            new_vs_catalog=new_vs_catalog,
        )
        html.write_text(html_content)
        console.print(f"[green]Generated HTML report: {html}[/green]")

        # Open in browser
        absolute_path = html.resolve()
        webbrowser.open(f"file://{absolute_path}")
        console.print(f"[dim]Opening in browser...[/dim]")


def generate_review_html(
    year: int,
    total_plays: int,
    unique_artists: int,
    unique_albums: int,
    unique_tracks: int,
    prev_plays: int | None,
    artist_contexts: list,
    album_contexts: list,
    new_artists_count: int,
    top_discoveries: list,
    critics_available: bool,
    your_overlap_pct: float,
    critic_overlap_stats: list,
    matched_albums: list,
    overlooked_gems: list,
    unheard_weighted: list,
    # MusicBrainz metadata
    mb_available: bool = False,
    genre_breakdown: list = None,
    genre_all_time: dict = None,
    label_breakdown: list = None,
    country_breakdown: list = None,
    release_type_breakdown: list = None,
    new_vs_catalog: dict = None,
) -> str:
    """Generate HTML report content."""
    from datetime import datetime

    max_artist_plays = artist_contexts[0]["plays"] if artist_contexts else 1
    max_album_plays = album_contexts[0]["plays"] if album_contexts else 1

    # Build artist rows
    artist_rows = ""
    for i, ctx in enumerate(artist_contexts[:10], 1):
        pct = ctx["plays"] / max_artist_plays * 100
        badge = ""
        if ctx["is_new"]:
            badge = '<span class="badge new">★ NEW</span>'
        elif ctx["is_peak"]:
            badge = '<span class="badge peak">↑ PEAK YEAR</span>'

        context = f"Fan since {ctx['first_year']}" if not ctx["is_new"] else "Discovered this year"

        artist_rows += f"""
        <div class="artist-row">
            <div class="rank">{i}</div>
            <div class="artist-info">
                <div class="artist-name">{ctx['name']} {badge}</div>
                <div class="bar-container">
                    <div class="bar" style="width: {pct}%"></div>
                </div>
                <div class="artist-meta">{ctx['plays']:,} plays · {context}</div>
            </div>
        </div>"""

    # Build album rows
    album_rows = ""
    for i, ctx in enumerate(album_contexts[:10], 1):
        pct = ctx["plays"] / max_album_plays * 100
        note = "★ Discovered this year" if ctx["discovered_this_year"] else f"First heard {ctx['first_play']:%b %Y}"
        album_rows += f"""
        <div class="album-row">
            <div class="rank">{i}</div>
            <div class="album-info">
                <div class="album-name">{ctx['album']}</div>
                <div class="album-artist">{ctx['artist']}</div>
                <div class="bar-container">
                    <div class="bar album-bar" style="width: {pct}%"></div>
                </div>
                <div class="album-meta">{ctx['plays']} plays · {note}</div>
            </div>
        </div>"""

    # Build discoveries
    discovery_rows = ""
    for i, disc in enumerate(top_discoveries[:7], 1):
        discovery_rows += f"""
        <div class="discovery-row">
            <div class="rank">{i}</div>
            <div class="discovery-info">
                <div class="discovery-name">{disc['name']}</div>
                <div class="discovery-meta">{disc['plays']} plays · First heard "{disc['first_track'][:50]}" on {disc['first_date']:%b %d}</div>
            </div>
        </div>"""

    # Year comparison
    year_comparison = ""
    if prev_plays:
        diff = total_plays - prev_plays
        pct = abs(diff) / prev_plays * 100
        direction = "↑" if diff > 0 else "↓"
        color = "#4ade80" if diff > 0 else "#f87171"
        year_comparison = f'<div class="stat-change" style="color: {color}">{direction} {pct:.0f}% from {year-1}</div>'

    # Critics section
    critics_section = ""
    if critics_available:
        # Aligned critics
        aligned_critics = ""
        for c in critic_overlap_stats[:5]:
            if c["overlap"] > 0:
                aligned_critics += f'<div class="critic-row">{c["name"]}: {c["overlap"]}/{c["total"]} ({c["pct"]:.0f}%)</div>'

        # Matched albums
        matched_rows = ""
        for m in matched_albums[:7]:
            matched_rows += f"""
            <div class="matched-row">
                <div class="matched-album">{m['artist']} — {m['album']}</div>
                <div class="matched-stats">{m['your_plays']} plays · {m['critics_count']} critics</div>
            </div>"""

        # Hidden gems
        gems_rows = ""
        for gem in overlooked_gems[:7]:
            albums = ", ".join(gem["albums"][:2]) if gem["albums"] else ""
            gems_rows += f"""
            <div class="gem-row">
                <div class="gem-artist">{gem['artist']}</div>
                <div class="gem-meta">{gem['plays']} plays{' · ' + albums if albums else ''}</div>
            </div>"""

        # Recommendations
        rec_rows = ""
        for rec in unheard_weighted[:10]:
            rec_rows += f"""
            <div class="rec-row">
                <div class="rec-album">{rec['album']}</div>
                <div class="rec-artist">{rec['artist']}</div>
                <div class="rec-score">{rec['score']:.2f}</div>
            </div>"""

        critics_section = f"""
        <section class="critics-section">
            <h2>🏆 You & The Critics</h2>
            <p class="section-intro">Your taste aligned with critics on <strong>{your_overlap_pct:.1f}%</strong> of their picks</p>

            <h3>Critics Who Share Your Taste</h3>
            <div class="critics-list">{aligned_critics}</div>

            <h3>Your Favorites That Critics Also Loved</h3>
            <div class="matched-list">{matched_rows}</div>
        </section>

        <section class="gems-section">
            <h2>💎 Your Hidden Gems</h2>
            <p class="section-intro">Artists you championed that critics missed</p>
            <div class="gems-list">{gems_rows}</div>
        </section>

        <section class="recs-section">
            <h2>🎧 What You Might Be Missing</h2>
            <p class="section-intro">Recommended by critics who share your taste</p>
            <div class="recs-list">{rec_rows}</div>
        </section>
        """

    # Build MusicBrainz metadata section
    mb_section = ""
    if mb_available and genre_breakdown:
        # Genre bars with comparison
        genre_bars = ""
        max_genre_pct = genre_breakdown[0]["pct"] if genre_breakdown else 1
        for g in (genre_breakdown or [])[:8]:
            width = (g["pct"] / max_genre_pct) * 100
            all_time_pct = (genre_all_time or {}).get(g["name"], 0)
            diff = g["pct"] - all_time_pct
            trend = ""
            if diff > 5:
                trend = f'<span class="trend up">↑ +{diff:.0f}%</span>'
            elif diff < -5:
                trend = f'<span class="trend down">↓ {diff:.0f}%</span>'
            genre_bars += f'''
            <div class="genre-row">
                <div class="genre-name">{g["name"]}</div>
                <div class="genre-bar-container">
                    <div class="genre-bar" style="width: {width}%"></div>
                </div>
                <div class="genre-pct">{g["pct"]:.1f}% {trend}</div>
            </div>'''

        # New vs catalog donut (CSS only)
        new_pct = (new_vs_catalog or {}).get("new_pct", 0)
        catalog_pct = (new_vs_catalog or {}).get("catalog_pct", 0)
        avg_lag = (new_vs_catalog or {}).get("avg_lag", 0)

        # Labels list
        labels_html = ""
        for l in (label_breakdown or [])[:6]:
            labels_html += f'<div class="label-item">{l["name"][:25]} <span>{l["pct"]:.1f}%</span></div>'

        # Countries list
        countries_html = ""
        for c in (country_breakdown or [])[:5]:
            countries_html += f'<div class="country-item">{c["name"]} <span>{c["pct"]:.1f}%</span></div>'

        # Release types
        types_html = ""
        for t in (release_type_breakdown or [])[:4]:
            types_html += f'<div class="type-item">{t["type"]} <span>{t["pct"]:.1f}%</span></div>'

        mb_section = f"""
        <section class="metadata-section">
            <h2>🎸 Your Sound</h2>
            <p class="section-intro">The genres that defined your year</p>
            <div class="genre-list">{genre_bars}</div>
        </section>

        <section class="freshness-section">
            <h2>📅 New vs Catalog</h2>
            <p class="section-intro">How current is your listening?</p>
            <div class="freshness-grid">
                <div class="donut-container">
                    <div class="donut" style="--new: {new_pct}; --catalog: {catalog_pct}">
                        <div class="donut-center">
                            <div class="donut-value">{new_pct:.0f}%</div>
                            <div class="donut-label">new</div>
                        </div>
                    </div>
                </div>
                <div class="freshness-stats">
                    <div class="freshness-stat">
                        <span class="freshness-value new">{new_pct:.0f}%</span>
                        <span class="freshness-label">New Releases ({year})</span>
                    </div>
                    <div class="freshness-stat">
                        <span class="freshness-value catalog">{catalog_pct:.0f}%</span>
                        <span class="freshness-label">Catalog (older)</span>
                    </div>
                    <div class="freshness-stat">
                        <span class="freshness-value">{avg_lag:.1f} yrs</span>
                        <span class="freshness-label">Avg Discovery Lag</span>
                    </div>
                </div>
            </div>
        </section>

        <section class="labels-countries">
            <div class="lc-grid">
                <div class="lc-column">
                    <h3>🏷️ Your Labels</h3>
                    <div class="labels-list">{labels_html}</div>
                </div>
                <div class="lc-column">
                    <h3>🌍 Release Countries</h3>
                    <div class="countries-list">{countries_html}</div>
                </div>
                <div class="lc-column">
                    <h3>💿 Release Types</h3>
                    <div class="types-list">{types_html}</div>
                </div>
            </div>
        </section>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My {year} in Music</title>
    <style>
        :root {{
            --bg: #0f0f0f;
            --card-bg: #1a1a1a;
            --text: #e5e5e5;
            --text-dim: #737373;
            --accent: #22d3ee;
            --accent2: #a855f7;
            --green: #4ade80;
            --yellow: #facc15;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2rem;
            max-width: 900px;
            margin: 0 auto;
        }}

        h1 {{
            font-size: 3rem;
            font-weight: 800;
            text-align: center;
            margin-bottom: 0.5rem;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .subtitle {{
            text-align: center;
            color: var(--text-dim);
            margin-bottom: 3rem;
        }}

        section {{
            background: var(--card-bg);
            border-radius: 1rem;
            padding: 2rem;
            margin-bottom: 2rem;
        }}

        h2 {{
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
            color: var(--accent);
        }}

        h3 {{
            font-size: 1.1rem;
            margin: 1.5rem 0 1rem;
            color: var(--text-dim);
        }}

        .section-intro {{
            color: var(--text-dim);
            margin-bottom: 1.5rem;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem;
            margin-bottom: 1rem;
        }}

        .stat {{
            text-align: center;
            padding: 1rem;
            background: var(--bg);
            border-radius: 0.5rem;
        }}

        .stat-value {{
            font-size: 2rem;
            font-weight: 700;
            color: var(--accent);
        }}

        .stat-label {{
            color: var(--text-dim);
            font-size: 0.9rem;
        }}

        .stat-change {{
            text-align: center;
            font-weight: 600;
            margin-top: 0.5rem;
        }}

        .artist-row, .album-row, .discovery-row {{
            display: flex;
            gap: 1rem;
            padding: 0.75rem 0;
            border-bottom: 1px solid #2a2a2a;
        }}

        .rank {{
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--text-dim);
            width: 2rem;
            text-align: right;
        }}

        .artist-info, .album-info, .discovery-info {{
            flex: 1;
        }}

        .artist-name, .discovery-name {{
            font-weight: 600;
            font-size: 1.1rem;
        }}

        .album-name {{
            font-weight: 600;
            color: var(--yellow);
        }}

        .album-artist {{
            color: var(--text-dim);
        }}

        .bar-container {{
            height: 8px;
            background: #2a2a2a;
            border-radius: 4px;
            margin: 0.5rem 0;
            overflow: hidden;
        }}

        .bar {{
            height: 100%;
            background: linear-gradient(90deg, var(--accent), var(--accent2));
            border-radius: 4px;
            transition: width 0.3s ease;
        }}

        .album-bar {{
            background: linear-gradient(90deg, var(--yellow), #f97316);
        }}

        .artist-meta, .album-meta, .discovery-meta {{
            font-size: 0.85rem;
            color: var(--text-dim);
        }}

        .badge {{
            display: inline-block;
            font-size: 0.7rem;
            padding: 0.15rem 0.5rem;
            border-radius: 1rem;
            font-weight: 600;
            margin-left: 0.5rem;
            vertical-align: middle;
        }}

        .badge.new {{
            background: var(--yellow);
            color: #000;
        }}

        .badge.peak {{
            background: var(--green);
            color: #000;
        }}

        .critic-row, .matched-row, .gem-row, .rec-row {{
            padding: 0.5rem 0;
            border-bottom: 1px solid #2a2a2a;
        }}

        .matched-album, .gem-artist, .rec-album {{
            font-weight: 500;
        }}

        .matched-stats, .gem-meta, .rec-artist {{
            font-size: 0.85rem;
            color: var(--text-dim);
        }}

        .rec-row {{
            display: grid;
            grid-template-columns: 1fr 1fr auto;
            gap: 1rem;
            align-items: center;
        }}

        .rec-album {{
            font-weight: 500;
        }}

        .rec-artist {{
            color: var(--text-dim);
            font-size: 0.85rem;
        }}

        .rec-score {{
            color: var(--accent2);
            font-weight: 600;
            text-align: right;
            min-width: 3rem;
        }}

        /* MusicBrainz metadata styles */
        .genre-row {{
            display: grid;
            grid-template-columns: 120px 1fr 100px;
            gap: 1rem;
            align-items: center;
            padding: 0.5rem 0;
        }}

        .genre-name {{
            font-weight: 500;
        }}

        .genre-bar-container {{
            height: 8px;
            background: #2a2a2a;
            border-radius: 4px;
            overflow: hidden;
        }}

        .genre-bar {{
            height: 100%;
            background: linear-gradient(90deg, var(--accent), var(--accent2));
            border-radius: 4px;
        }}

        .genre-pct {{
            text-align: right;
            font-size: 0.9rem;
        }}

        .trend {{
            font-size: 0.75rem;
            margin-left: 0.25rem;
        }}

        .trend.up {{ color: var(--green); }}
        .trend.down {{ color: #f87171; }}

        .freshness-grid {{
            display: grid;
            grid-template-columns: auto 1fr;
            gap: 2rem;
            align-items: center;
        }}

        .donut-container {{
            width: 150px;
            height: 150px;
        }}

        .donut {{
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: conic-gradient(
                var(--green) 0% calc(var(--new) * 1%),
                #3a3a3a calc(var(--new) * 1%) 100%
            );
            display: flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }}

        .donut-center {{
            width: 100px;
            height: 100px;
            background: var(--card-bg);
            border-radius: 50%;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }}

        .donut-value {{
            font-size: 1.75rem;
            font-weight: 700;
            color: var(--green);
        }}

        .donut-label {{
            font-size: 0.85rem;
            color: var(--text-dim);
        }}

        .freshness-stats {{
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }}

        .freshness-stat {{
            display: flex;
            flex-direction: column;
        }}

        .freshness-value {{
            font-size: 1.25rem;
            font-weight: 600;
        }}

        .freshness-value.new {{ color: var(--green); }}
        .freshness-value.catalog {{ color: var(--text-dim); }}

        .freshness-label {{
            font-size: 0.85rem;
            color: var(--text-dim);
        }}

        .lc-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 2rem;
        }}

        .lc-column h3 {{
            margin-bottom: 1rem;
            font-size: 1rem;
        }}

        .label-item, .country-item, .type-item {{
            display: flex;
            justify-content: space-between;
            padding: 0.4rem 0;
            border-bottom: 1px solid #2a2a2a;
        }}

        .label-item span, .country-item span, .type-item span {{
            color: var(--text-dim);
        }}

        @media (max-width: 600px) {{
            .freshness-grid {{
                grid-template-columns: 1fr;
                justify-items: center;
            }}
            .lc-grid {{
                grid-template-columns: 1fr;
            }}
            .genre-row {{
                grid-template-columns: 1fr;
                gap: 0.25rem;
            }}
            .genre-pct {{
                text-align: left;
            }}
        }}

        footer {{
            text-align: center;
            color: var(--text-dim);
            font-size: 0.85rem;
            padding: 2rem 0;
        }}
    </style>
</head>
<body>
    <h1>My {year} in Music</h1>
    <p class="subtitle">A year of listening, discovering, and obsessing</p>

    <section>
        <h2>📊 The Big Picture</h2>
        <div class="stats-grid">
            <div class="stat">
                <div class="stat-value">{total_plays:,}</div>
                <div class="stat-label">tracks played</div>
            </div>
            <div class="stat">
                <div class="stat-value">{unique_artists:,}</div>
                <div class="stat-label">artists</div>
            </div>
            <div class="stat">
                <div class="stat-value">{unique_albums:,}</div>
                <div class="stat-label">albums</div>
            </div>
            <div class="stat">
                <div class="stat-value">{new_artists_count:,}</div>
                <div class="stat-label">new discoveries</div>
            </div>
        </div>
        {year_comparison}
    </section>

    <section>
        <h2>🎸 Your Obsessions</h2>
        <p class="section-intro">The artists you couldn't stop playing</p>
        <div class="artists-list">{artist_rows}</div>
    </section>

    <section>
        <h2>💿 The Albums You Lived With</h2>
        <p class="section-intro">The records that defined your year</p>
        <div class="albums-list">{album_rows}</div>
    </section>

    <section>
        <h2>🔍 New Discoveries</h2>
        <p class="section-intro">You discovered <strong>{new_artists_count}</strong> new artists this year. These are the ones that stuck.</p>
        <div class="discoveries-list">{discovery_rows}</div>
    </section>

    {mb_section}

    {critics_section}

    <footer>
        Generated {datetime.now():%B %d, %Y} · Data from Last.fm
    </footer>
</body>
</html>"""

    return html


def generate_overview_html(
    total_plays: int,
    first_scrobble,
    last_scrobble,
    years_of_data: int,
    unique_artists: int,
    unique_albums: int,
    unique_tracks: int,
    peak_year: tuple,
    avg_plays_per_year: float,
    plays_by_year: dict,
    all_years: list,
    artist_contexts: list,
    album_contexts: list,
    discoveries_by_year: dict,
    consistent_artists,
    peak_obsessions: list,
    critics_all_time_stats: dict | None,
    top_aligned_critics: list,
    most_acclaimed_albums: list,
    mb_available: bool,
    genre_breakdown_all: list,
    decade_breakdown: dict,
) -> str:
    """Generate HTML overview content."""
    from datetime import datetime

    # Build artist rows with sparklines
    artist_rows = ""
    blocks = " ▁▂▃▄▅▆▇█"
    for i, ctx in enumerate(artist_contexts[:25], 1):
        max_artist_plays = max(ctx["sparkline_data"]) if ctx["sparkline_data"] else 1
        sparkline = "".join(
            blocks[min(8, int(p / max_artist_plays * 8))] if max_artist_plays > 0 else " "
            for p in ctx["sparkline_data"]
        )

        artist_rows += f"""
        <div class="artist-row">
            <div class="rank">{i}</div>
            <div class="artist-info">
                <div class="artist-name">{ctx['name']}</div>
                <div class="artist-stats">{ctx['plays']:,} plays · Fan since {ctx['first_year']} · {ctx['years_active']} years active</div>
                <div class="sparkline">{all_years[0]} {sparkline} {all_years[-1]}</div>
            </div>
        </div>"""

    # Build album table
    album_rows = ""
    for i, ctx in enumerate(album_contexts[:20], 1):
        album_rows += f"""
        <tr>
            <td class="rank">{i}</td>
            <td class="album-name">{ctx['album']}</td>
            <td class="artist-name">{ctx['artist']}</td>
            <td class="plays">{ctx['plays']:,}</td>
            <td class="years">{ctx['years_active']}</td>
        </tr>"""

    # Critics section
    critics_html = ""
    if critics_all_time_stats:
        aligned_critics_html = ""
        for c in top_aligned_critics[:10]:
            if c["overlap"] > 0:
                aligned_critics_html += f'<div class="critic-row">{c["name"]}: {c["overlap"]}/{c["total"]} ({c["pct"]:.0f}%)</div>'

        acclaimed_albums_html = ""
        for a in most_acclaimed_albums[:15]:
            acclaimed_albums_html += f"""
            <div class="album-row">
                <div class="album-info">
                    <div class="album-title">{a['artist']} — {a['album']}</div>
                    <div class="album-stats">{a['plays']} plays · {a['critics']} critic lists</div>
                </div>
            </div>"""

        critics_html = f"""
        <section class="critics-section">
            <h2>🏆 You & The Critics (All-Time)</h2>
            <p class="section-intro">Your alignment across all years (2011-2025)</p>
            <div class="stat-big">
                <div class="stat-value">{critics_all_time_stats['overlap_pct']:.1f}%</div>
                <div class="stat-label">Overall alignment ({critics_all_time_stats['matched']:,} of {critics_all_time_stats['total_albums']:,} albums)</div>
            </div>

            <h3>Critics Who Share Your Taste</h3>
            <div class="critics-list">{aligned_critics_html}</div>

            <h3>Your Most Critically-Acclaimed Albums</h3>
            <div class="albums-list">{acclaimed_albums_html}</div>
        </section>"""

    # Genre section
    genre_html = ""
    if mb_available and genre_breakdown_all:
        genre_rows_html = ""
        max_genre_pct = genre_breakdown_all[0]["pct"] if genre_breakdown_all else 1
        for g in genre_breakdown_all[:12]:
            width = (g["pct"] / max_genre_pct) * 100
            genre_rows_html += f"""
            <div class="genre-row">
                <div class="genre-name">{g['name']}</div>
                <div class="genre-bar-container">
                    <div class="genre-bar" style="width: {width}%"></div>
                </div>
                <div class="genre-pct">{g['pct']:.1f}%</div>
            </div>"""

        genre_html = f"""
        <section class="genre-section">
            <h2>🎸 Your Sound (All-Time)</h2>
            <p class="section-intro">Top genres across your entire listening history</p>
            <div class="genre-list">{genre_rows_html}</div>
        </section>"""

    # Decade section
    decade_html = ""
    if decade_breakdown:
        sorted_decades = sorted(decade_breakdown.items())
        total_decade_plays = sum(decade_breakdown.values())
        decade_rows_html = ""

        for decade, plays in sorted_decades:
            pct = plays / total_decade_plays * 100 if total_decade_plays > 0 else 0
            decade_label = f"{decade}s" if decade >= 1950 else "Pre-1950"
            decade_rows_html += f"""
            <div class="decade-row">
                <div class="decade-label">{decade_label}</div>
                <div class="decade-bar-container">
                    <div class="decade-bar" style="width: {pct}%"></div>
                </div>
                <div class="decade-pct">{pct:.1f}%</div>
            </div>"""

        decade_html = f"""
        <section class="decade-section">
            <h2>📅 Music By Decade</h2>
            <p class="section-intro">When the music you listen to was released</p>
            <div class="decade-list">{decade_rows_html}</div>
        </section>"""

    # Peak obsessions
    peak_html = ""
    if peak_obsessions:
        peak_rows_html = ""
        for obs in peak_obsessions[:12]:
            peak_rows_html += f"""
            <div class="peak-row">
                <div class="peak-artist">{obs['artist']}</div>
                <div class="peak-year">{obs['year']}</div>
                <div class="peak-stats">{obs['plays']} plays ({obs['concentration']:.0f}% of total)</div>
            </div>"""

        peak_html = f"""
        <section class="peak-section">
            <h2>🔥 Peak Obsessions</h2>
            <p class="section-intro">Artists and the years you couldn't stop playing them</p>
            <div class="peak-list">{peak_rows_html}</div>
        </section>"""

    # Timeline sparkline
    max_plays_year = max(plays_by_year.values()) if plays_by_year else 1
    timeline_html = "".join(
        f'<div class="year-bar" style="height: {plays_by_year.get(y, 0) / max_plays_year * 100}%" title="{y}: {plays_by_year.get(y, 0):,} plays"></div>'
        for y in all_years
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>My All-Time Listening Overview</title>
    <style>
        :root {{
            --bg: #0f0f0f;
            --card-bg: #1a1a1a;
            --text: #e5e5e5;
            --text-dim: #737373;
            --accent: #22d3ee;
            --accent2: #a855f7;
            --green: #4ade80;
            --yellow: #facc15;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            padding: 2rem;
            max-width: 1000px;
            margin: 0 auto;
        }}

        h1 {{
            font-size: 3rem;
            font-weight: 800;
            text-align: center;
            margin-bottom: 0.5rem;
            background: linear-gradient(135deg, var(--accent), var(--accent2));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .subtitle {{
            text-align: center;
            color: var(--text-dim);
            margin-bottom: 3rem;
        }}

        section {{
            background: var(--card-bg);
            border-radius: 1rem;
            padding: 2rem;
            margin-bottom: 2rem;
        }}

        h2 {{
            font-size: 1.5rem;
            margin-bottom: 0.5rem;
            color: var(--accent);
        }}

        h3 {{
            font-size: 1.1rem;
            margin: 1.5rem 0 1rem;
            color: var(--text-dim);
        }}

        .section-intro {{
            color: var(--text-dim);
            margin-bottom: 1.5rem;
        }}

        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
            gap: 1rem;
            margin-bottom: 1.5rem;
        }}

        .stat {{
            text-align: center;
            padding: 1rem;
            background: var(--bg);
            border-radius: 0.5rem;
        }}

        .stat-value {{
            font-size: 2rem;
            font-weight: 700;
            color: var(--accent);
        }}

        .stat-label {{
            color: var(--text-dim);
            font-size: 0.9rem;
        }}

        .stat-big {{
            text-align: center;
            padding: 2rem;
            background: var(--bg);
            border-radius: 0.75rem;
            margin-bottom: 2rem;
        }}

        .stat-big .stat-value {{
            font-size: 3rem;
        }}

        .timeline {{
            display: flex;
            align-items: flex-end;
            height: 60px;
            gap: 2px;
            margin: 1rem 0;
            padding: 0.5rem;
            background: var(--bg);
            border-radius: 0.5rem;
        }}

        .year-bar {{
            flex: 1;
            background: linear-gradient(180deg, var(--accent), var(--accent2));
            border-radius: 2px 2px 0 0;
            min-height: 2px;
            transition: opacity 0.2s;
        }}

        .year-bar:hover {{
            opacity: 0.7;
        }}

        .artist-row {{
            display: flex;
            gap: 1rem;
            padding: 1rem 0;
            border-bottom: 1px solid #2a2a2a;
        }}

        .rank {{
            font-size: 1.25rem;
            font-weight: 700;
            color: var(--text-dim);
            width: 2.5rem;
            text-align: right;
            flex-shrink: 0;
        }}

        .artist-info {{
            flex: 1;
        }}

        .artist-name {{
            font-weight: 600;
            font-size: 1.1rem;
            margin-bottom: 0.25rem;
        }}

        .artist-stats {{
            font-size: 0.85rem;
            color: var(--text-dim);
            margin-bottom: 0.25rem;
        }}

        .sparkline {{
            font-family: monospace;
            font-size: 0.75rem;
            color: var(--accent);
            letter-spacing: 1px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
        }}

        th {{
            text-align: left;
            padding: 0.5rem;
            border-bottom: 2px solid #2a2a2a;
            color: var(--text-dim);
        }}

        td {{
            padding: 0.75rem 0.5rem;
            border-bottom: 1px solid #2a2a2a;
        }}

        td.rank {{
            width: 3rem;
        }}

        td.album-name {{
            color: var(--yellow);
            font-weight: 500;
        }}

        td.plays, td.years {{
            text-align: right;
            color: var(--green);
        }}

        .critic-row, .album-row, .peak-row {{
            padding: 0.75rem 0;
            border-bottom: 1px solid #2a2a2a;
        }}

        .genre-row, .decade-row {{
            display: grid;
            grid-template-columns: 150px 1fr 80px;
            gap: 1rem;
            align-items: center;
            padding: 0.5rem 0;
        }}

        .genre-name, .decade-label {{
            font-weight: 500;
        }}

        .genre-bar-container, .decade-bar-container {{
            height: 8px;
            background: #2a2a2a;
            border-radius: 4px;
            overflow: hidden;
        }}

        .genre-bar, .decade-bar {{
            height: 100%;
            background: linear-gradient(90deg, var(--accent), var(--accent2));
            border-radius: 4px;
        }}

        .genre-pct, .decade-pct {{
            text-align: right;
            color: var(--text-dim);
        }}

        .peak-artist {{
            font-weight: 600;
            margin-bottom: 0.25rem;
        }}

        .peak-year {{
            color: var(--accent);
            font-size: 0.9rem;
            margin-bottom: 0.25rem;
        }}

        .peak-stats {{
            font-size: 0.85rem;
            color: var(--text-dim);
        }}

        .album-info {{
            padding: 0.5rem 0;
        }}

        .album-title {{
            font-weight: 500;
            margin-bottom: 0.25rem;
        }}

        .album-stats {{
            font-size: 0.85rem;
            color: var(--text-dim);
        }}

        footer {{
            text-align: center;
            color: var(--text-dim);
            font-size: 0.85rem;
            padding: 2rem 0;
        }}

        @media (max-width: 600px) {{
            .genre-row, .decade-row {{
                grid-template-columns: 1fr;
                gap: 0.25rem;
            }}

            .genre-pct, .decade-pct {{
                text-align: left;
            }}
        }}
    </style>
</head>
<body>
    <h1>My Listening Overview</h1>
    <p class="subtitle">{first_scrobble:%B %Y} – {last_scrobble:%B %Y} · {years_of_data} years of listening</p>

    <section>
        <h2>📊 The Big Picture</h2>
        <div class="stats-grid">
            <div class="stat">
                <div class="stat-value">{total_plays:,}</div>
                <div class="stat-label">total plays</div>
            </div>
            <div class="stat">
                <div class="stat-value">{unique_artists:,}</div>
                <div class="stat-label">artists</div>
            </div>
            <div class="stat">
                <div class="stat-value">{unique_albums:,}</div>
                <div class="stat-label">albums</div>
            </div>
            <div class="stat">
                <div class="stat-value">{peak_year[0]}</div>
                <div class="stat-label">peak year</div>
            </div>
        </div>
        <p class="section-intro">Listening intensity over time:</p>
        <div class="timeline">{timeline_html}</div>
    </section>

    <section>
        <h2>🎸 Your All-Time Favorites</h2>
        <p class="section-intro">Top artists across your entire listening history</p>
        <div class="artists-list">{artist_rows}</div>
    </section>

    <section>
        <h2>💿 Your All-Time Albums</h2>
        <p class="section-intro">The records you've lived with</p>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Album</th>
                    <th>Artist</th>
                    <th style="text-align: right">Plays</th>
                    <th style="text-align: right">Years</th>
                </tr>
            </thead>
            <tbody>{album_rows}</tbody>
        </table>
    </section>

    {peak_html}
    {critics_html}
    {genre_html}
    {decade_html}

    <footer>
        Generated {datetime.now():%B %d, %Y} · Data from Last.fm
    </footer>
</body>
</html>"""

    return html
