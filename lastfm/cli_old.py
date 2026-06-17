"""CLI for Last.fm listening history analysis."""

import typer
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from rich.console import Console
from rich.table import Table

from . import data

app = typer.Typer(help="Analyze your Last.fm listening history")
console = Console()

# Default CSV path - can be overridden
DEFAULT_CSV = Path(__file__).parent.parent / "recenttracks-biddulph-1767217094.csv"


def get_csv_path(csv: Optional[Path]) -> Path:
    """Get CSV path, using default if not specified."""
    return csv or DEFAULT_CSV


@app.command()
def stats(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Filter to specific year"),
):
    """Show overall listening statistics."""
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


@app.command()
def top(
    what: str = typer.Argument("artists", help="What to show: artists, albums, or tracks"),
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Filter to specific year"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of results"),
    unselected: bool = typer.Option(False, "--unselected", "-u", help="Only show artists not picked by critics (requires --year)"),
    new_album: bool = typer.Option(False, "--new-album", "-a", help="Only artists with an album first heard that year"),
):
    """Show top artists, albums, or tracks by play count."""
    df_full = data.load_scrobbles(get_csv_path(csv))

    # Find artists with "new" albums (first heard in the target year) before filtering
    new_album_artists = set()
    if new_album:
        if not year:
            console.print("[red]--new-album requires --year to be specified[/red]")
            raise typer.Exit(1)
        if what != "artists":
            console.print("[red]--new-album only works with 'artists'[/red]")
            raise typer.Exit(1)

        from . import crossref
        # Find first play of each album across all time
        df_with_albums = df_full[df_full["album"] != ""].copy()
        first_plays = df_with_albums.sort_values("timestamp").groupby(
            ["artist", "album"]
        ).first().reset_index()
        # Filter to albums first heard in the target year
        first_plays_year = first_plays[first_plays["year"] == year]
        # Get normalized artist names
        for artist in first_plays_year["artist"].unique():
            new_album_artists.add(crossref.normalize_for_matching(artist))

    df = data.filter_by_year(df_full, year) if year else df_full

    # Build set of critic-selected artists if filtering
    critics_artists = set()
    if unselected:
        if not year:
            console.print("[red]--unselected requires --year to be specified[/red]")
            raise typer.Exit(1)
        if what != "artists":
            console.print("[red]--unselected only works with 'artists'[/red]")
            raise typer.Exit(1)

        from . import crossref
        import json
        json_path = get_critics_path(year)
        try:
            with open(json_path) as f:
                raw_data = json.load(f)
            for lst in raw_data:
                for album in lst['albums']:
                    if album['artist']:
                        critics_artists.add(crossref.normalize_for_matching(album['artist']))
        except FileNotFoundError:
            console.print(f"[red]No critics data for {year}. Run 'music-history crawl --year {year}' first.[/red]")
            raise typer.Exit(1)

    if what == "artists":
        needs_filtering = unselected or new_album
        result = data.top_artists(df, limit if not needs_filtering else limit * 10)

        if needs_filtering:
            from . import crossref
            masks = []
            if unselected:
                masks.append(result['artist'].apply(
                    lambda x: crossref.normalize_for_matching(x) not in critics_artists
                ))
            if new_album:
                masks.append(result['artist'].apply(
                    lambda x: crossref.normalize_for_matching(x) in new_album_artists
                ))
            # Combine masks with AND
            combined_mask = masks[0]
            for m in masks[1:]:
                combined_mask = combined_mask & m
            result = result[combined_mask].head(limit)

            # Build title
            if unselected and new_album:
                title = f"Top {limit} Artists with New Albums NOT Picked by Critics ({year})"
            elif unselected:
                title = f"Top {limit} Artists NOT Picked by Critics ({year})"
            else:
                title = f"Top {limit} Artists with New Albums ({year})"
        else:
            result = result.head(limit)
            title = f"Top {limit} Artists" + (f" ({year})" if year else "")

        table = Table(title=title)
        table.add_column("Artist", style="cyan")
        table.add_column("Plays", justify="right", style="green")
        for _, row in result.iterrows():
            table.add_row(row["artist"], str(row["plays"]))

    elif what == "albums":
        result = data.top_albums(df, limit)
        table = Table(title=f"Top {limit} Albums" + (f" ({year})" if year else ""))
        table.add_column("Artist", style="cyan")
        table.add_column("Album", style="yellow")
        table.add_column("Plays", justify="right", style="green")
        for _, row in result.iterrows():
            table.add_row(row["artist"], row["album"], str(row["plays"]))

    elif what == "tracks":
        result = data.top_tracks(df, limit)
        table = Table(title=f"Top {limit} Tracks" + (f" ({year})" if year else ""))
        table.add_column("Artist", style="cyan")
        table.add_column("Track", style="yellow")
        table.add_column("Plays", justify="right", style="green")
        for _, row in result.iterrows():
            table.add_row(row["artist"], row["track"], str(row["plays"]))

    else:
        console.print(f"[red]Unknown type: {what}. Use artists, albums, or tracks.[/red]")
        raise typer.Exit(1)

    console.print(table)


@app.command()
def discovered(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to check discoveries"),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Show artists discovered (first played) in a given year."""
    df = data.load_scrobbles(get_csv_path(csv))
    result = data.artists_discovered_in_year(df, year)

    table = Table(title=f"Artists Discovered in {year}")
    table.add_column("Artist", style="cyan")
    table.add_column("First Played", style="yellow")
    table.add_column("First Track", style="dim")
    table.add_column(f"Plays in {year}", justify="right", style="green")

    for _, row in result.head(limit).iterrows():
        table.add_row(
            row["artist"],
            row["timestamp"].strftime("%Y-%m-%d"),
            row["track"][:40] + "..." if len(row["track"]) > 40 else row["track"],
            str(int(row["plays_in_year"])),
        )

    console.print(table)
    console.print(f"\nTotal new artists in {year}: {len(result)}")


@app.command()
def first_play(
    artist: str = typer.Argument(..., help="Artist name to look up"),
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
):
    """Show when you first played an artist."""
    df = data.load_scrobbles(get_csv_path(csv))

    # Case-insensitive search
    matches = df[df["artist"].str.lower() == artist.lower()]

    if matches.empty:
        # Try partial match
        partial = df[df["artist"].str.lower().str.contains(artist.lower(), regex=False)]
        if partial.empty:
            console.print(f"[red]No plays found for '{artist}'[/red]")
            raise typer.Exit(1)
        else:
            console.print(f"[yellow]No exact match. Did you mean one of these?[/yellow]")
            for a in partial["artist"].unique()[:10]:
                console.print(f"  - {a}")
            raise typer.Exit(1)

    first = matches.sort_values("timestamp").iloc[0]
    total_plays = len(matches)

    console.print(f"\n[bold]{first['artist']}[/bold]")
    console.print(f"First play: {first['timestamp']:%Y-%m-%d %H:%M} UTC")
    console.print(f"First track: {first['track']}")
    if first["album"]:
        console.print(f"First album: {first['album']}")
    console.print(f"Total plays: {total_plays:,}")


@app.command()
def plays(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Filter to specific year"),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Filter to last N days"),
    artist: Optional[str] = typer.Option(None, "--artist", "-a", help="Filter to specific artist"),
    limit: int = typer.Option(50, "--limit", "-n", help="Number of results"),
):
    """List recent plays with optional filters."""
    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)

    if days:
        cutoff = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - pd.Timedelta(days=days)
        df = df[df["timestamp"] >= cutoff]

    if artist:
        df = df[df["artist"].str.lower().str.contains(artist.lower(), regex=False)]

    # Sort by timestamp descending (most recent first)
    df = df.sort_values("timestamp", ascending=False).head(limit)

    table = Table(title=f"Recent Plays ({len(df)} shown)")
    table.add_column("Time", style="dim")
    table.add_column("Artist", style="cyan")
    table.add_column("Track", style="yellow")
    table.add_column("Album", style="dim")

    for _, row in df.iterrows():
        table.add_row(
            row["timestamp"].strftime("%Y-%m-%d %H:%M"),
            row["artist"],
            row["track"][:35] + "..." if len(row["track"]) > 35 else row["track"],
            (row["album"][:25] + "..." if len(row["album"]) > 25 else row["album"]) if row["album"] else "",
        )

    console.print(table)


@app.command("2025")
def year_2025(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
):
    """Special analysis for 2025 - new discoveries and top listens."""
    df = data.load_scrobbles(get_csv_path(csv))

    # Get 2025 plays
    df_2025 = data.filter_by_year(df, 2025)

    console.print("\n[bold magenta]═══ 2025 Listening Summary ═══[/bold magenta]\n")

    # Basic stats
    console.print(f"[bold]Total plays in 2025:[/bold] {len(df_2025):,}")
    console.print(f"[bold]Unique artists:[/bold] {df_2025['artist'].nunique():,}")
    console.print(f"[bold]Unique albums:[/bold] {df_2025[df_2025['album'] != '']['album'].nunique():,}")

    # New discoveries
    discovered = data.artists_discovered_in_year(df, 2025)
    console.print(f"[bold]New artists discovered:[/bold] {len(discovered):,}")

    console.print("\n[bold cyan]Top 10 New Discoveries of 2025:[/bold cyan]")
    table = Table(show_header=True)
    table.add_column("Artist", style="cyan")
    table.add_column("First Track", style="dim")
    table.add_column("Plays", justify="right", style="green")

    for _, row in discovered.head(10).iterrows():
        table.add_row(
            row["artist"],
            row["track"][:40] + "..." if len(row["track"]) > 40 else row["track"],
            str(int(row["plays_in_year"])),
        )
    console.print(table)

    console.print("\n[bold yellow]Top 10 Artists Overall in 2025:[/bold yellow]")
    top_artists = data.top_artists(df_2025, 10)
    table2 = Table(show_header=True)
    table2.add_column("Artist", style="cyan")
    table2.add_column("Plays", justify="right", style="green")

    for _, row in top_artists.iterrows():
        table2.add_row(row["artist"], str(row["plays"]))
    console.print(table2)


# Need pandas import for Timedelta
import pandas as pd
import asyncio


def get_critics_path(year: int) -> Path:
    """Get the default critics JSON path for a given year."""
    return Path(__file__).parent.parent / f"critics-{year}.json"


@app.command()
def crawl(
    year: int = typer.Option(
        2025,
        "--year", "-y",
        help="Year to crawl (2011-2025)",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output", "-o",
        help="Output JSON file path (default: critics-{year}.json)",
    ),
    delay: float = typer.Option(
        0.5,
        "--delay", "-d",
        help="Delay between requests in seconds",
    ),
):
    """Crawl yearendlists.com for album lists."""
    from . import crawler

    if year < 2011 or year > 2025:
        console.print("[red]Year must be between 2011 and 2025[/red]")
        raise typer.Exit(1)

    output_path = output or get_critics_path(year)

    console.print(f"[bold]Crawling yearendlists.com for {year} album lists...[/bold]\n")
    lists = asyncio.run(crawler.run_crawler(output_path, year=year, delay=delay))

    # Summary
    total_albums = sum(len(lst.albums) for lst in lists)
    console.print(f"\n[bold green]Done![/bold green]")
    console.print(f"  Lists crawled: {len(lists)}")
    console.print(f"  Total album entries: {total_albums:,}")
    console.print(f"  Output: {output_path}")


@app.command()
def matched(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to analyze"),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Show critic-loved albums you've listened to."""
    from . import crossref

    df = data.load_scrobbles(get_csv_path(csv))
    critics_data = crossref.load_critics_data(critics_json or get_critics_path(year))
    results = crossref.match_with_history(critics_data, df, year=year)

    console.print(f"\n[bold cyan]Albums You've Heard That Critics Love ({year})[/bold cyan]")
    console.print(f"Matched {len(results['matched'])} of {results['stats']['total_critics_albums']} critic-listed albums\n")

    table = Table(show_header=True)
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Your Plays", justify="right", style="magenta")

    for i, m in enumerate(results['matched'][:limit], 1):
        table.add_row(
            str(i),
            m.artist,
            m.album[:35] + "..." if len(m.album) > 35 else m.album,
            str(m.critics_count),
            str(m.your_plays),
        )

    console.print(table)


@app.command()
def unheard(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to analyze"),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
    known_artists: bool = typer.Option(False, "--known", "-k", help="Only show artists you've heard"),
    weighted: bool = typer.Option(False, "--weighted", "-w", help="Weight by critic overlap with your taste"),
):
    """Show highly-rated albums you haven't listened to."""
    from . import crossref
    import json

    df = data.load_scrobbles(get_csv_path(csv))
    json_path = critics_json or get_critics_path(year)
    critics_data = crossref.load_critics_data(json_path)
    results = crossref.match_with_history(critics_data, df, year=year)

    unheard_list = results['unheard']
    if known_artists:
        unheard_list = [u for u in unheard_list if u['heard_artist']]

    if weighted:
        # Calculate overlap score per critic
        with open(json_path) as f:
            raw_data = json.load(f)

        df_year = df[df['year'] == year]
        your_albums = set()
        for _, row in df_year.iterrows():
            if row['album']:
                key = (crossref.normalize_for_matching(row['artist']),
                       crossref.normalize_for_matching(row['album']))
                your_albums.add(key)

        critic_scores = {}
        for lst in raw_data:
            critic = lst['critic']
            total = len(lst['albums'])
            overlap_count = 0
            for album in lst['albums']:
                if album['artist'] and album['title']:
                    key = (crossref.normalize_for_matching(album['artist']),
                           crossref.normalize_for_matching(album['title']))
                    if key in your_albums:
                        overlap_count += 1
            # Score is overlap percentage (0-1)
            critic_scores[critic] = overlap_count / total if total > 0 else 0

        # Build album -> critics mapping for unheard albums
        album_critics = {}
        for lst in raw_data:
            critic = lst['critic']
            for album in lst['albums']:
                if album['artist'] and album['title']:
                    key = (crossref.normalize_for_matching(album['artist']),
                           crossref.normalize_for_matching(album['title']))
                    if key not in your_albums:
                        if key not in album_critics:
                            album_critics[key] = {'artist': album['artist'], 'album': album['title'], 'critics': []}
                        album_critics[key]['critics'].append(critic)

        # Calculate weighted score for each unheard album
        for u in unheard_list:
            key = (crossref.normalize_for_matching(u['artist']),
                   crossref.normalize_for_matching(u['album']))
            if key in album_critics:
                critics = album_critics[key]['critics']
                # Weighted score = sum of overlap scores from critics who listed it
                u['weighted_score'] = sum(critic_scores.get(c, 0) for c in critics)
            else:
                u['weighted_score'] = 0

        # Sort by weighted score
        unheard_list = sorted(unheard_list, key=lambda x: -x['weighted_score'])

        title = f"Albums Recommended By Critics Who Share Your Taste ({year})"
        console.print(f"\n[bold cyan]{title}[/bold cyan]")
        console.print("[dim]Weighted by overlap with each critic's list[/dim]\n")

        table = Table(show_header=True)
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("Artist", style="cyan")
        table.add_column("Album", style="yellow")
        table.add_column("Score", justify="right", style="magenta")
        table.add_column("Critics", justify="right", style="green")

        for i, u in enumerate(unheard_list[:limit], 1):
            table.add_row(
                str(i),
                u['artist'],
                u['album'][:35] + "..." if len(u['album']) > 35 else u['album'],
                f"{u['weighted_score']:.2f}",
                str(u['critics_count']),
            )
    else:
        title = f"Unheard Albums From Artists You Know ({year})" if known_artists else f"Highly-Rated Albums You Haven't Heard ({year})"
        console.print(f"\n[bold cyan]{title}[/bold cyan]\n")

        table = Table(show_header=True)
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("Artist", style="cyan")
        table.add_column("Album", style="yellow")
        table.add_column("Critics", justify="right", style="green")
        table.add_column("Artist Plays", justify="right", style="dim")

        for i, u in enumerate(unheard_list[:limit], 1):
            table.add_row(
                str(i),
                u['artist'],
                u['album'][:35] + "..." if len(u['album']) > 35 else u['album'],
                str(u['critics_count']),
                str(u['artist_plays']) if u['artist_plays'] else "-",
            )

    console.print(table)


@app.command()
def overlap(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to analyze"),
):
    """Show summary of overlap between your listening and critics' picks."""
    from . import crossref

    df = data.load_scrobbles(get_csv_path(csv))
    critics_data = crossref.load_critics_data(critics_json or get_critics_path(year))
    results = crossref.match_with_history(critics_data, df, year=year)

    stats = results['stats']

    console.print(f"\n[bold magenta]═══ Critics vs Your {year} Listening ═══[/bold magenta]\n")

    console.print(f"[bold]Critics' albums:[/bold] {stats['total_critics_albums']}")
    console.print(f"[bold]Albums you've heard:[/bold] {stats['matched_count']} ({100*stats['matched_count']/stats['total_critics_albums']:.1f}%)")
    console.print(f"[bold]Your artists in critics' lists:[/bold] {stats['your_artists_in_critics']}")

    # Top matched
    console.print("\n[bold cyan]Your Most-Played Critic Favorites:[/bold cyan]")
    table = Table(show_header=True)
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Your Plays", justify="right", style="magenta")

    for m in sorted(results['matched'], key=lambda x: -x.your_plays)[:10]:
        table.add_row(m.artist, m.album, str(m.critics_count), str(m.your_plays))
    console.print(table)

    # Your artists that critics love
    console.print("\n[bold yellow]Your Top Artists With Critic-Listed Albums:[/bold yellow]")
    table2 = Table(show_header=True)
    table2.add_column("Artist", style="cyan")
    table2.add_column("Your Plays", justify="right", style="magenta")
    table2.add_column("Critic Album", style="yellow")
    table2.add_column("Lists", justify="right", style="green")

    for artist_data in results['your_top_artists'][:15]:
        # Show the highest-rated album for this artist
        best_album = max(artist_data['critic_albums'], key=lambda x: x[2])
        table2.add_row(
            artist_data['artist'],
            str(artist_data['your_plays']),
            best_album[1][:30] + "..." if len(best_album[1]) > 30 else best_album[1],
            str(best_album[2]),
        )
    console.print(table2)


@app.command()
def critics(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to analyze"),
    sort: str = typer.Option("overlap", "--sort", "-s", help="Sort by: overlap, albums, name"),
):
    """Show overview of critics and your overlap with each."""
    from . import crossref
    import json

    # Load critics data
    json_path = critics_json or get_critics_path(year)
    with open(json_path) as f:
        raw_data = json.load(f)

    # Load your listening data for overlap calculation
    df = data.load_scrobbles(get_csv_path(csv))
    df_year = df[df['year'] == year]

    # Build set of your albums (normalized)
    your_albums = set()
    for _, row in df_year.iterrows():
        if row['album']:
            key = (crossref.normalize_for_matching(row['artist']),
                   crossref.normalize_for_matching(row['album']))
            your_albums.add(key)

    # Calculate stats per critic
    critic_stats = []
    for lst in raw_data:
        critic = lst['critic']
        albums = lst['albums']
        total = len(albums)

        # Count overlap
        overlap_count = 0
        for album in albums:
            if album['artist'] and album['title']:
                key = (crossref.normalize_for_matching(album['artist']),
                       crossref.normalize_for_matching(album['title']))
                if key in your_albums:
                    overlap_count += 1

        overlap_pct = (overlap_count / total * 100) if total > 0 else 0
        critic_stats.append({
            'critic': critic,
            'albums': total,
            'overlap': overlap_count,
            'overlap_pct': overlap_pct,
            'url': lst['url'],
        })

    # Sort
    if sort == "overlap":
        critic_stats.sort(key=lambda x: (-x['overlap'], -x['overlap_pct']))
    elif sort == "albums":
        critic_stats.sort(key=lambda x: -x['albums'])
    else:  # name
        critic_stats.sort(key=lambda x: x['critic'].lower())

    console.print(f"\n[bold magenta]═══ {year} Critics Overview ({len(critic_stats)} critics) ═══[/bold magenta]\n")

    # Summary stats
    total_albums = sum(c['albums'] for c in critic_stats)
    avg_albums = total_albums / len(critic_stats)
    critics_with_overlap = sum(1 for c in critic_stats if c['overlap'] > 0)

    console.print(f"[bold]Total lists:[/bold] {len(critic_stats)}")
    console.print(f"[bold]Total album entries:[/bold] {total_albums:,}")
    console.print(f"[bold]Avg albums per list:[/bold] {avg_albums:.1f}")
    console.print(f"[bold]Critics with overlap:[/bold] {critics_with_overlap} ({100*critics_with_overlap/len(critic_stats):.0f}%)\n")

    table = Table(show_header=True)
    table.add_column("Critic", style="cyan")
    table.add_column("Albums", justify="right")
    table.add_column("Overlap", justify="right", style="green")
    table.add_column("%", justify="right", style="dim")

    for c in critic_stats:
        overlap_str = str(c['overlap']) if c['overlap'] > 0 else "-"
        pct_str = f"{c['overlap_pct']:.0f}%" if c['overlap'] > 0 else "-"
        table.add_row(
            c['critic'],
            str(c['albums']),
            overlap_str,
            pct_str,
        )

    console.print(table)


@app.command()
def artist_lists(
    artist: str = typer.Argument(..., help="Artist name to search for"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to search"),
):
    """Show which critics listed a given artist."""
    import json
    from . import crossref

    json_path = critics_json or get_critics_path(year)
    with open(json_path) as f:
        raw_data = json.load(f)

    # Normalize search term
    search_norm = crossref.normalize_for_matching(artist)

    # Find all matches
    matches = []
    for lst in raw_data:
        critic = lst['critic']
        for album in lst['albums']:
            if album['artist']:
                artist_norm = crossref.normalize_for_matching(album['artist'])
                if search_norm in artist_norm or artist_norm in search_norm:
                    matches.append({
                        'critic': critic,
                        'artist': album['artist'],
                        'album': album['title'],
                        'rank': album['rank'],
                    })

    if not matches:
        # Try partial match
        partial = []
        for lst in raw_data:
            for album in lst['albums']:
                if album['artist'] and artist.lower() in album['artist'].lower():
                    partial.append(album['artist'])

        if partial:
            console.print(f"[yellow]No exact match for '{artist}'. Did you mean:[/yellow]")
            for a in sorted(set(partial))[:10]:
                console.print(f"  - {a}")
        else:
            console.print(f"[red]No critics listed '{artist}'[/red]")
        raise typer.Exit(1)

    # Group by album
    from collections import defaultdict
    albums = defaultdict(list)
    for m in matches:
        albums[(m['artist'], m['album'])].append((m['critic'], m['rank']))

    console.print(f"\n[bold cyan]{matches[0]['artist']}[/bold cyan] appears on [bold]{len(set(m['critic'] for m in matches))}[/bold] critics' {year} lists\n")

    table = Table(show_header=True)
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Listed By", no_wrap=False)

    for (artist_name, album), critics_list in sorted(albums.items(), key=lambda x: -len(x[1])):
        critics_str = ", ".join(sorted(set(c[0] for c in critics_list)))
        table.add_row(
            album,
            str(len(critics_list)),
            critics_str,
        )

    console.print(table)


@app.command()
def artist(
    artist_name: str = typer.Argument(..., help="Artist name to look up"),
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
):
    """Show comprehensive artist summary across all years."""
    from . import crossref
    from collections import defaultdict
    import json

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
        your_albums = set(crossref.normalize_for_matching(a) for a in albums_df["album"].unique())

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


@app.command()
def review(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to review"),
    html: Optional[Path] = typer.Option(None, "--html", help="Generate HTML report to this path"),
):
    """Generate a comprehensive year-in-review of your listening."""
    from . import crossref
    from collections import defaultdict
    import json

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

    json_path = critics_json or get_critics_path(year)
    if json_path.exists():
        try:
            critics_data = crossref.load_critics_data(json_path)
            with open(json_path) as f:
                raw_critics = json.load(f)
            critics_available = True

            # Build your albums set
            df_with_albums = df[df["album"] != ""]
            your_albums = set()
            for _, row in df_with_albums.iterrows():
                key = (crossref.normalize_for_matching(row["artist"]),
                       crossref.normalize_for_matching(row["album"]))
                your_albums.add(key)

            # Match with critics
            results = crossref.match_with_history(critics_data, df_full, year=year)
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

            new_album_artists = set()
            for _, row in new_albums_this_year.iterrows():
                new_album_artists.add(crossref.normalize_for_matching(row["artist"]))

            # Top artists with new albums not in critics lists
            for ctx in artist_contexts:
                norm_name = crossref.normalize_for_matching(ctx["name"])
                if norm_name not in critics_artists and norm_name in new_album_artists:
                    # Get the new album(s)
                    artist_new = new_albums_this_year[
                        new_albums_this_year["artist"].apply(
                            lambda x: crossref.normalize_for_matching(x) == norm_name
                        )
                    ]
                    albums = artist_new["album"].tolist()
                    overlooked_gems.append({
                        "artist": ctx["name"],
                        "plays": ctx["plays"],
                        "albums": albums[:2],  # Top 2 new albums
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


@app.command()
def spotify_auth(
    client_id: str = typer.Option(None, "--client-id", help="Spotify Client ID"),
    client_secret: str = typer.Option(None, "--client-secret", help="Spotify Client Secret"),
):
    """Set up Spotify API credentials for playlist creation.

    To get credentials:
    1. Go to https://developer.spotify.com/dashboard
    2. Create an app
    3. Add http://localhost:8888/callback to Redirect URIs
    4. Copy your Client ID and Client Secret
    """
    from . import spotify

    if client_id and client_secret:
        creds = spotify.SpotifyCredentials(
            client_id=client_id,
            client_secret=client_secret,
        )
        spotify.save_credentials(creds)
        console.print("[green]Credentials saved![/green]")
    else:
        # Check if we have stored credentials
        creds = spotify.get_credentials()
        if creds:
            console.print(f"[green]Credentials found[/green] (Client ID: {creds.client_id[:8]}...)")
        else:
            console.print("[yellow]No credentials found.[/yellow]")
            console.print("\nTo set up Spotify:")
            console.print("1. Go to https://developer.spotify.com/dashboard")
            console.print("2. Create an app")
            console.print("3. Add [cyan]http://localhost:8888/callback[/cyan] to Redirect URIs")
            console.print("4. Run: [cyan]music-history spotify-auth --client-id YOUR_ID --client-secret YOUR_SECRET[/cyan]")
            return

    # Test authentication
    console.print("\n[dim]Testing authentication...[/dim]")
    try:
        sp = spotify.get_spotify_client()
        if sp:
            user = sp.current_user()
            console.print(f"[green]Authenticated as:[/green] {user['display_name']} ({user['id']})")
        else:
            console.print("[red]Failed to authenticate[/red]")
    except Exception as e:
        console.print(f"[red]Authentication error:[/red] {e}")


@app.command()
def spotify_playlist(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    year: int = typer.Option(2025, "--year", "-y", help="Year to analyze"),
    playlist_type: str = typer.Option(
        "both",
        "--type", "-t",
        help="Playlist type: 'matched' (critics you agreed with), 'missing' (recommendations), or 'both'",
    ),
):
    """Create Spotify playlists from your year-in-review data.

    Creates playlists of:
    - 'matched': Albums you loved that critics also loved
    - 'missing': Recommended albums you haven't heard yet
    - 'both': Both playlists
    """
    from . import spotify, crossref
    import json

    # Check for Spotify credentials
    sp = spotify.get_spotify_client()
    if not sp:
        console.print("[red]Spotify not configured.[/red]")
        console.print("Run [cyan]music-history spotify-auth[/cyan] first.")
        raise typer.Exit(1)

    # Load data (same as review command)
    df_full = data.load_scrobbles(get_csv_path(csv))
    df = data.filter_by_year(df_full, year)

    json_path = critics_json or get_critics_path(year)
    if not json_path.exists():
        console.print(f"[red]No critics data for {year}. Run 'music-history crawl --year {year}' first.[/red]")
        raise typer.Exit(1)

    critics_data = crossref.load_critics_data(json_path)
    with open(json_path) as f:
        raw_critics = json.load(f)

    # Build your albums set
    df_with_albums = df[df["album"] != ""]
    your_albums = set()
    for _, row in df_with_albums.iterrows():
        key = (crossref.normalize_for_matching(row["artist"]),
               crossref.normalize_for_matching(row["album"]))
        your_albums.add(key)

    # Get matched albums (your favorites that critics loved)
    results = crossref.match_with_history(critics_data, df_full, year=year)
    matched_albums = [
        {"artist": m.artist, "album": m.album}
        for m in results["matched"][:20]  # Top 20
    ]

    # Get recommended albums (weighted by critic overlap)
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
        critic_scores[critic] = (overlap / total * 100) if total > 0 else 0

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
        album_data["score"] = sum(critic_scores[c] / 100 for c in album_data["critics"])

    missing_albums = sorted(
        [{"artist": a["artist"], "album": a["album"]} for a in album_critics_map.values()],
        key=lambda x: -album_critics_map.get(
            (crossref.normalize_for_matching(x["artist"]),
             crossref.normalize_for_matching(x["album"])),
            {"score": 0}
        ).get("score", 0)
    )[:20]  # Top 20

    # Create playlists
    if playlist_type in ("matched", "both") and matched_albums:
        console.print(f"\n[bold cyan]Creating 'Critics Approved' playlist...[/bold cyan]")
        console.print(f"[dim]Albums you loved that critics also loved ({year})[/dim]\n")

        url, tracks, found = spotify.create_playlist_from_albums(
            sp,
            matched_albums,
            f"Critics Approved {year}",
            f"Albums I loved in {year} that critics also loved. Generated from Last.fm + yearendlists.com data.",
        )

        if url:
            console.print(f"[green]Created playlist:[/green] {url}")
            console.print(f"[dim]{tracks} tracks from {found}/{len(matched_albums)} albums found[/dim]")
        else:
            console.print("[yellow]Could not find any albums on Spotify[/yellow]")

    if playlist_type in ("missing", "both") and missing_albums:
        console.print(f"\n[bold cyan]Creating 'Critics Recommend' playlist...[/bold cyan]")
        console.print(f"[dim]Albums recommended by critics who share your taste ({year})[/dim]\n")

        url, tracks, found = spotify.create_playlist_from_albums(
            sp,
            missing_albums,
            f"Critics Recommend {year}",
            f"Albums I haven't heard, recommended by critics who share my taste. Generated from Last.fm + yearendlists.com data.",
        )

        if url:
            console.print(f"[green]Created playlist:[/green] {url}")
            console.print(f"[dim]{tracks} tracks from {found}/{len(missing_albums)} albums found[/dim]")
        else:
            console.print("[yellow]Could not find any albums on Spotify[/yellow]")


@app.command()
def blind_spots(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    min_critics: int = typer.Option(20, "--min-critics", "-m", help="Minimum critics to be considered a blind spot"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of results"),
):
    """Find highly-acclaimed albums you've never explored.

    Shows albums that many critics loved but you've never played -
    your biggest gaps in critical consensus.
    """
    from . import crossref
    import json

    df = data.load_scrobbles(get_csv_path(csv))

    # Build set of all albums you've ever played (normalized)
    your_albums = set()
    your_artists = set()
    df_with_albums = df[df["album"] != ""]
    for _, row in df_with_albums.iterrows():
        artist = row["artist"] if pd.notna(row["artist"]) else ""
        album = row["album"] if pd.notna(row["album"]) else ""
        if artist and album:
            your_albums.add((
                crossref.normalize_for_matching(artist),
                crossref.normalize_for_matching(album)
            ))
            your_artists.add(crossref.normalize_for_matching(artist))

    # Aggregate across all available years
    all_blind_spots = {}  # (norm_artist, norm_album) -> {artist, album, total_critics, years}

    for year in range(2011, 2026):
        json_path = get_critics_path(year)
        if not json_path.exists():
            continue

        try:
            with open(json_path) as f:
                raw_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        # Count critics per album for this year
        album_critics = {}
        for lst in raw_data:
            for album in lst["albums"]:
                if album["artist"] and album["title"]:
                    key = (
                        crossref.normalize_for_matching(album["artist"]),
                        crossref.normalize_for_matching(album["title"])
                    )
                    if key not in album_critics:
                        album_critics[key] = {
                            "artist": album["artist"],
                            "album": album["title"],
                            "critics": set(),
                        }
                    album_critics[key]["critics"].add(lst["critic"])

        # Add to all_blind_spots if you haven't heard it
        for key, info in album_critics.items():
            if key not in your_albums:
                critic_count = len(info["critics"])
                if key not in all_blind_spots:
                    all_blind_spots[key] = {
                        "artist": info["artist"],
                        "album": info["album"],
                        "total_critics": 0,
                        "years": [],
                        "heard_artist": key[0] in your_artists,
                    }
                all_blind_spots[key]["total_critics"] += critic_count
                all_blind_spots[key]["years"].append((year, critic_count))

    # Filter and sort
    blind_spots = [
        v for v in all_blind_spots.values()
        if v["total_critics"] >= min_critics
    ]
    blind_spots.sort(key=lambda x: -x["total_critics"])

    console.print(f"\n[bold magenta]═══ YOUR CRITICAL BLIND SPOTS ═══[/bold magenta]")
    console.print(f"[dim]Highly-acclaimed albums you've never played ({min_critics}+ critic picks)[/dim]\n")

    if not blind_spots:
        console.print("[green]No major blind spots found! You're well-aligned with critics.[/green]")
        return

    table = Table(show_header=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Years", style="dim")
    table.add_column("", style="dim")

    for i, spot in enumerate(blind_spots[:limit], 1):
        years_str = ", ".join(str(y) for y, _ in sorted(spot["years"], key=lambda x: -x[1])[:3])
        known = "★" if spot["heard_artist"] else ""
        table.add_row(
            str(i),
            spot["artist"][:25],
            spot["album"][:30],
            str(spot["total_critics"]),
            years_str,
            known,
        )

    console.print(table)
    console.print(f"\n[dim]★ = You've heard other music by this artist[/dim]")
    console.print(f"[dim]Showing albums with {min_critics}+ total critic selections across all years[/dim]")


@app.command()
def loyalty(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    min_years: int = typer.Option(5, "--min-years", "-m", help="Minimum years to be considered loyal"),
):
    """Show your artist loyalty patterns over time.

    Identifies:
    - Long-term favorites (artists you've played for 5+ years)
    - Abandoned artists (used to play, stopped completely)
    - Rediscoveries (returned after a gap)
    """
    from . import crossref

    df = data.load_scrobbles(get_csv_path(csv))

    # Filter out NaN artists
    df = df[df["artist"].notna()]

    # Get year range
    min_year = df["year"].min()
    max_year = df["year"].max()
    current_year = max_year

    # Build artist stats
    artist_stats = {}
    for artist in df["artist"].unique():
        artist_df = df[df["artist"] == artist]
        years_active = sorted(artist_df["year"].unique())
        plays_by_year = artist_df.groupby("year").size().to_dict()
        total_plays = len(artist_df)
        first_year = min(years_active)
        last_year = max(years_active)
        span = last_year - first_year + 1

        artist_stats[artist] = {
            "years_active": years_active,
            "plays_by_year": plays_by_year,
            "total_plays": total_plays,
            "first_year": first_year,
            "last_year": last_year,
            "span": span,
            "num_years": len(years_active),
        }

    # Categorize artists
    long_term = []  # 5+ years of plays
    abandoned = []  # Played significantly, then stopped for 2+ years
    rediscovered = []  # Gap of 2+ years, then returned

    for artist, stats in artist_stats.items():
        years = stats["years_active"]
        num_years = stats["num_years"]
        last_year = stats["last_year"]
        total_plays = stats["total_plays"]

        # Long-term: played in 5+ different years
        if num_years >= min_years:
            long_term.append({
                "artist": artist,
                "num_years": num_years,
                "span": stats["span"],
                "first_year": stats["first_year"],
                "total_plays": total_plays,
                "plays_by_year": stats["plays_by_year"],
            })

        # Check for gaps
        if len(years) >= 2:
            gaps = []
            for i in range(len(years) - 1):
                gap = years[i + 1] - years[i]
                if gap >= 3:  # 3+ year gap
                    gaps.append((years[i], years[i + 1], gap))

            if gaps:
                last_gap = gaps[-1]
                # Rediscovered: had a gap but came back
                if last_year >= current_year - 1:  # Active recently
                    rediscovered.append({
                        "artist": artist,
                        "gap_start": last_gap[0],
                        "gap_end": last_gap[1],
                        "gap_years": last_gap[2],
                        "total_plays": total_plays,
                        "first_year": stats["first_year"],
                    })
                # Abandoned: significant plays but stopped
                elif total_plays >= 20 and last_year <= current_year - 2:
                    # Check they had real engagement (not just 1-2 plays)
                    peak_plays = max(stats["plays_by_year"].values())
                    if peak_plays >= 10:
                        abandoned.append({
                            "artist": artist,
                            "last_year": last_year,
                            "peak_year": max(stats["plays_by_year"], key=stats["plays_by_year"].get),
                            "peak_plays": peak_plays,
                            "total_plays": total_plays,
                        })

    # Also find abandoned without gaps (just stopped)
    for artist, stats in artist_stats.items():
        if artist in [a["artist"] for a in abandoned]:
            continue
        if stats["total_plays"] >= 30 and stats["last_year"] <= current_year - 3:
            peak_plays = max(stats["plays_by_year"].values())
            if peak_plays >= 15:
                abandoned.append({
                    "artist": artist,
                    "last_year": stats["last_year"],
                    "peak_year": max(stats["plays_by_year"], key=stats["plays_by_year"].get),
                    "peak_plays": peak_plays,
                    "total_plays": stats["total_plays"],
                })

    # Sort
    long_term.sort(key=lambda x: (-x["num_years"], -x["total_plays"]))
    abandoned.sort(key=lambda x: (-x["peak_plays"], -x["total_plays"]))
    rediscovered.sort(key=lambda x: (-x["gap_years"], -x["total_plays"]))

    console.print(f"\n[bold magenta]═══ ARTIST LOYALTY REPORT ═══[/bold magenta]")
    console.print(f"[dim]Your listening history: {min_year}-{max_year}[/dim]\n")

    # Long-term favorites
    console.print(f"[bold cyan]🎸 LONG-TERM FAVORITES[/bold cyan]")
    console.print(f"[dim]Artists you've played for {min_years}+ years[/dim]\n")

    if long_term:
        for i, a in enumerate(long_term[:15], 1):
            # Mini sparkline of activity
            years_range = range(a["first_year"], max_year + 1)
            sparkline = ""
            for y in years_range:
                plays = a["plays_by_year"].get(y, 0)
                if plays == 0:
                    sparkline += "·"
                elif plays < 10:
                    sparkline += "▁"
                elif plays < 30:
                    sparkline += "▃"
                elif plays < 60:
                    sparkline += "▅"
                else:
                    sparkline += "█"

            console.print(f"  {i:2}. [bold]{a['artist']}[/bold]")
            console.print(f"      {a['num_years']} years · {a['total_plays']:,} plays · Since {a['first_year']}")
            console.print(f"      [green]{sparkline}[/green] [dim]{a['first_year']}-{max_year}[/dim]")
    else:
        console.print("  [dim]No artists with {min_years}+ years of plays yet[/dim]")

    # Rediscoveries
    console.print(f"\n[bold yellow]🔄 REDISCOVERIES[/bold yellow]")
    console.print(f"[dim]Artists you returned to after 3+ years away[/dim]\n")

    if rediscovered:
        for i, a in enumerate(rediscovered[:10], 1):
            console.print(f"  {i:2}. [bold]{a['artist']}[/bold]")
            console.print(f"      [dim]Gap: {a['gap_start']}-{a['gap_end']} ({a['gap_years']} years) · First heard {a['first_year']}[/dim]")
    else:
        console.print("  [dim]No major rediscoveries found[/dim]")

    # Abandoned
    console.print(f"\n[bold red]👋 ABANDONED[/bold red]")
    console.print(f"[dim]Artists you used to love but haven't played in years[/dim]\n")

    if abandoned:
        for i, a in enumerate(abandoned[:10], 1):
            years_gone = current_year - a["last_year"]
            console.print(f"  {i:2}. [bold]{a['artist']}[/bold]")
            console.print(f"      [dim]Peak: {a['peak_plays']} plays in {a['peak_year']} · Last played {a['last_year']} ({years_gone} years ago)[/dim]")
    else:
        console.print("  [dim]No abandoned artists found[/dim]")


@app.command()
def evolution(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
):
    """Show how your taste has evolved over time.

    Detects 'musical eras' - periods where certain artists dominated,
    and shows when key artists became staples in your listening.
    """
    df = data.load_scrobbles(get_csv_path(csv))

    min_year = df["year"].min()
    max_year = df["year"].max()

    console.print(f"\n[bold magenta]═══ TASTE EVOLUTION ═══[/bold magenta]")
    console.print(f"[dim]How your listening has changed: {min_year}-{max_year}[/dim]\n")

    # For each year, find the dominant artists
    console.print("[bold cyan]📅 YEAR BY YEAR: WHO DOMINATED[/bold cyan]\n")

    yearly_data = []
    for year in range(min_year, max_year + 1):
        year_df = df[df["year"] == year]
        if year_df.empty:
            continue

        total_plays = len(year_df)
        top_artists = year_df.groupby("artist").size().sort_values(ascending=False)

        # Top 3 artists and their share
        top3 = []
        for artist, plays in top_artists.head(3).items():
            share = plays / total_plays * 100
            top3.append((artist, plays, share))

        # Concentration: what % do top 10 artists represent?
        top10_plays = top_artists.head(10).sum()
        concentration = top10_plays / total_plays * 100

        yearly_data.append({
            "year": year,
            "total_plays": total_plays,
            "top3": top3,
            "concentration": concentration,
            "unique_artists": len(top_artists),
        })

    for yd in yearly_data:
        year = yd["year"]
        top3_str = ", ".join(f"{a[0]} ({a[2]:.0f}%)" for a in yd["top3"])
        bar_width = min(30, yd["total_plays"] // 200)
        bar = "█" * bar_width

        console.print(f"  [bold]{year}[/bold] [green]{bar}[/green] {yd['total_plays']:,} plays")
        console.print(f"       [dim]{top3_str}[/dim]")

    # Detect "eras" - periods of similar listening
    console.print(f"\n[bold cyan]🎭 MUSICAL ERAS[/bold cyan]")
    console.print("[dim]Detecting shifts in your dominant artists[/dim]\n")

    # Group years into eras based on top artist overlap
    eras = []
    current_era = None

    for i, yd in enumerate(yearly_data):
        top_artists = set(a[0] for a in yd["top3"])

        if current_era is None:
            current_era = {
                "start": yd["year"],
                "end": yd["year"],
                "artists": top_artists,
                "defining_artists": list(top_artists),
            }
        else:
            # Check overlap with current era
            overlap = len(top_artists & current_era["artists"])
            if overlap >= 1:  # At least 1 shared top artist
                current_era["end"] = yd["year"]
                current_era["artists"] |= top_artists
            else:
                # New era
                eras.append(current_era)
                current_era = {
                    "start": yd["year"],
                    "end": yd["year"],
                    "artists": top_artists,
                    "defining_artists": list(top_artists),
                }

    if current_era:
        eras.append(current_era)

    for i, era in enumerate(eras, 1):
        span = f"{era['start']}" if era["start"] == era["end"] else f"{era['start']}-{era['end']}"
        defining = ", ".join(era["defining_artists"][:4])
        console.print(f"  [bold]Era {i}: {span}[/bold]")
        console.print(f"  [dim]Defined by: {defining}[/dim]\n")

    # When did key artists enter your life?
    console.print(f"[bold cyan]🌟 WHEN KEY ARTISTS ENTERED YOUR LIFE[/bold cyan]\n")

    # Find artists with most total plays
    artist_totals = df.groupby("artist").agg({
        "timestamp": "min",
        "track": "count"
    }).rename(columns={"track": "plays"})
    artist_totals["first_year"] = artist_totals["timestamp"].dt.year
    top_artists = artist_totals.nlargest(20, "plays")

    # Group by discovery year
    by_discovery = {}
    for artist, row in top_artists.iterrows():
        year = row["first_year"]
        if year not in by_discovery:
            by_discovery[year] = []
        by_discovery[year].append((artist, row["plays"]))

    for year in sorted(by_discovery.keys()):
        artists = by_discovery[year]
        artists_str = ", ".join(f"{a[0]} ({a[1]:,})" for a in sorted(artists, key=lambda x: -x[1])[:3])
        console.print(f"  [bold]{year}[/bold]: {artists_str}")


@app.command()
def critic_accuracy(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: int = typer.Option(2020, "--year", "-y", help="Year of critic recommendations to check"),
):
    """Check if you ended up loving albums critics recommended years ago.

    Looks at critic picks from a past year and checks how much you've
    played them in subsequent years.
    """
    from . import crossref
    import json

    df = data.load_scrobbles(get_csv_path(csv))

    json_path = get_critics_path(year)
    if not json_path.exists():
        console.print(f"[red]No critics data for {year}. Run 'music-history crawl --year {year}' first.[/red]")
        raise typer.Exit(1)

    with open(json_path) as f:
        raw_data = json.load(f)

    # Get all critic-recommended albums for that year
    critic_albums = {}
    for lst in raw_data:
        for album in lst["albums"]:
            if album["artist"] and album["title"]:
                key = (
                    crossref.normalize_for_matching(album["artist"]),
                    crossref.normalize_for_matching(album["title"])
                )
                if key not in critic_albums:
                    critic_albums[key] = {
                        "artist": album["artist"],
                        "album": album["title"],
                        "critics": set(),
                    }
                critic_albums[key]["critics"].add(lst["critic"])

    # Check your plays of these albums AFTER the recommendation year
    df_after = df[df["year"] > year]
    your_plays = {}

    for _, row in df_after.iterrows():
        if row["album"]:
            key = (
                crossref.normalize_for_matching(row["artist"]),
                crossref.normalize_for_matching(row["album"])
            )
            if key in critic_albums:
                if key not in your_plays:
                    your_plays[key] = {"plays": 0, "years": set()}
                your_plays[key]["plays"] += 1
                your_plays[key]["years"].add(row["year"])

    # Calculate results
    total_recommended = len(critic_albums)
    you_played = len(your_plays)
    you_loved = len([k for k, v in your_plays.items() if v["plays"] >= 10])

    console.print(f"\n[bold magenta]═══ CRITIC PREDICTION ACCURACY ({year}) ═══[/bold magenta]")
    console.print(f"[dim]Did you end up loving what critics recommended in {year}?[/dim]\n")

    console.print(f"  Critics recommended: [bold]{total_recommended}[/bold] albums")
    console.print(f"  You've since played: [bold]{you_played}[/bold] ({100*you_played/total_recommended:.1f}%)")
    console.print(f"  You've loved (10+ plays): [bold]{you_loved}[/bold] ({100*you_loved/total_recommended:.1f}%)")

    # Top albums you ended up loving
    if your_plays:
        console.print(f"\n[bold cyan]Albums from {year} you ended up loving:[/bold cyan]\n")

        loved_albums = [
            {
                "artist": critic_albums[k]["artist"],
                "album": critic_albums[k]["album"],
                "plays": v["plays"],
                "critics": len(critic_albums[k]["critics"]),
                "years_played": sorted(v["years"]),
            }
            for k, v in your_plays.items()
        ]
        loved_albums.sort(key=lambda x: -x["plays"])

        table = Table(show_header=True, box=None)
        table.add_column("Album", style="yellow")
        table.add_column("Artist", style="cyan")
        table.add_column("Your Plays", justify="right", style="green")
        table.add_column("Critics", justify="right", style="dim")
        table.add_column("Played In", style="dim")

        for a in loved_albums[:15]:
            years_str = ", ".join(str(y) for y in a["years_played"][-3:])
            table.add_row(
                a["album"][:25],
                a["artist"][:20],
                str(a["plays"]),
                str(a["critics"]),
                years_str,
            )

        console.print(table)

    # What did critics love that you missed?
    high_consensus = [
        (k, v) for k, v in critic_albums.items()
        if len(v["critics"]) >= 20 and k not in your_plays
    ]
    high_consensus.sort(key=lambda x: -len(x[1]["critics"]))

    if high_consensus:
        console.print(f"\n[bold yellow]High-consensus picks you still haven't tried:[/bold yellow]\n")
        for key, album in high_consensus[:7]:
            console.print(f"  {album['artist']} — {album['album']} ({len(album['critics'])} critics)")


@app.command()
def critic_tracker(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    reference_year: int = typer.Option(2023, "--ref-year", "-r", help="Year to find your aligned critics"),
    target_year: int = typer.Option(2025, "--target-year", "-t", help="Year to get their new picks"),
    min_overlap: int = typer.Option(3, "--min-overlap", "-m", help="Minimum albums overlap to consider a critic aligned"),
):
    """Find critics who predicted your past favorites and see what they pick now.

    Uses a reference year to find critics whose picks matched your taste,
    then shows what those same critics are recommending for the target year.
    """
    from . import crossref
    import json

    df = data.load_scrobbles(get_csv_path(csv))

    # Load both years
    ref_path = get_critics_path(reference_year)
    target_path = get_critics_path(target_year)

    if not ref_path.exists():
        console.print(f"[red]No critics data for {reference_year}. Run 'music-history crawl --year {reference_year}' first.[/red]")
        raise typer.Exit(1)
    if not target_path.exists():
        console.print(f"[red]No critics data for {target_year}. Run 'music-history crawl --year {target_year}' first.[/red]")
        raise typer.Exit(1)

    with open(ref_path) as f:
        ref_data = json.load(f)
    with open(target_path) as f:
        target_data = json.load(f)

    # Build your albums set (all time, for checking what you've heard)
    your_albums = set()
    df_with_albums = df[df["album"] != ""]
    for _, row in df_with_albums.iterrows():
        artist = row["artist"] if pd.notna(row["artist"]) else ""
        album = row["album"] if pd.notna(row["album"]) else ""
        if artist and album:
            your_albums.add((
                crossref.normalize_for_matching(artist),
                crossref.normalize_for_matching(album)
            ))

    # Find critics with overlap in reference year
    critic_overlap = {}
    for lst in ref_data:
        critic = lst["critic"]
        overlap = 0
        matched_albums = []
        for album in lst["albums"]:
            if album["artist"] and album["title"]:
                key = (
                    crossref.normalize_for_matching(album["artist"]),
                    crossref.normalize_for_matching(album["title"])
                )
                if key in your_albums:
                    overlap += 1
                    matched_albums.append(f"{album['artist']} — {album['title']}")

        if overlap >= min_overlap:
            critic_overlap[critic] = {
                "overlap": overlap,
                "total": len(lst["albums"]),
                "matched": matched_albums,
            }

    if not critic_overlap:
        console.print(f"[yellow]No critics found with {min_overlap}+ album overlap in {reference_year}[/yellow]")
        console.print(f"[dim]Try lowering --min-overlap or using a different reference year[/dim]")
        raise typer.Exit(1)

    # Get target year picks from these aligned critics
    aligned_critics = set(critic_overlap.keys())
    target_picks = {}  # album_key -> {album info, critics who picked it}

    for lst in target_data:
        critic = lst["critic"]
        if critic in aligned_critics:
            for album in lst["albums"]:
                if album["artist"] and album["title"]:
                    key = (
                        crossref.normalize_for_matching(album["artist"]),
                        crossref.normalize_for_matching(album["title"])
                    )
                    if key not in target_picks:
                        target_picks[key] = {
                            "artist": album["artist"],
                            "album": album["title"],
                            "critics": [],
                            "you_heard": key in your_albums,
                        }
                    target_picks[key]["critics"].append({
                        "name": critic,
                        "overlap": critic_overlap[critic]["overlap"],
                    })

    # Score picks by sum of critic overlap scores
    for key, pick in target_picks.items():
        pick["score"] = sum(c["overlap"] for c in pick["critics"])
        pick["critic_count"] = len(pick["critics"])

    # Sort by score
    sorted_picks = sorted(target_picks.values(), key=lambda x: -x["score"])

    console.print(f"\n[bold magenta]═══ CRITIC TRACKER ═══[/bold magenta]")
    console.print(f"[dim]Critics who matched your taste in {reference_year} → What they pick for {target_year}[/dim]\n")

    console.print(f"[bold cyan]Your Aligned Critics ({reference_year}):[/bold cyan]")
    console.print(f"[dim]Critics with {min_overlap}+ albums you've also heard[/dim]\n")

    for critic, info in sorted(critic_overlap.items(), key=lambda x: -x[1]["overlap"])[:10]:
        console.print(f"  [bold]{critic}[/bold]: {info['overlap']}/{info['total']} overlap")

    # What do they recommend for target year that you haven't heard?
    unheard = [p for p in sorted_picks if not p["you_heard"]]
    heard = [p for p in sorted_picks if p["you_heard"]]

    console.print(f"\n[bold cyan]Their {target_year} Picks You Haven't Heard:[/bold cyan]\n")

    if unheard:
        table = Table(show_header=True, box=None)
        table.add_column("Album", style="yellow")
        table.add_column("Artist", style="cyan")
        table.add_column("Score", justify="right", style="magenta")
        table.add_column("Aligned Critics", justify="right", style="green")

        for p in unheard[:15]:
            table.add_row(
                p["album"][:28],
                p["artist"][:22],
                str(p["score"]),
                str(p["critic_count"]),
            )

        console.print(table)
    else:
        console.print("  [green]You've heard everything they recommend![/green]")

    if heard:
        console.print(f"\n[bold green]Their {target_year} Picks You Already Know:[/bold green]")
        console.print(f"[dim]Validation - you and these critics agree![/dim]\n")

        for p in heard[:5]:
            console.print(f"  {p['artist']} — {p['album']} ({p['critic_count']} aligned critics)")


@app.command()
def download_musicbrainz(
    force: bool = typer.Option(False, "--force", "-f", help="Force re-download even if cached"),
):
    """Download MusicBrainz database for rich music metadata lookups.

    Downloads the full MusicBrainz release dump (~2-3GB compressed) and builds
    a local SQLite database with release years, genres, labels, countries, and more.

    The dump file is cached locally, so re-running only re-processes (no re-download).
    Use --force to bypass the cache and re-download.

    The database is stored at ~/.cache/music-history-analysis/musicbrainz_releases.db
    """
    from . import musicbrainz_db

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


@app.command()
def enrich_releases(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    limit: int = typer.Option(500, "--limit", "-n", help="Max albums to look up via API"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Only enrich albums from this year"),
):
    """Fetch release years for albums.

    Uses local MusicBrainz database if available (instant), otherwise falls
    back to API (rate-limited to 1 req/sec).

    Run 'download-musicbrainz' first for best performance.
    """
    from . import release_years
    from . import musicbrainz_db
    import sqlite3

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
        console.print("[yellow]No local database. Run 'download-musicbrainz' for faster lookups.[/yellow]")

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


@app.command()
def catalog(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Analyze specific year"),
):
    """Analyze your new vs catalog listening patterns.

    Shows:
    - What % of your listening is new releases vs older catalog
    - Decade breakdown (are you a 70s person? 90s? 2020s?)
    - Average discovery lag (how long after release do you find albums?)

    Run 'enrich-releases' first to populate release year data.
    """
    from . import release_years
    from collections import defaultdict

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
        console.print("Run [cyan]music-history enrich-releases[/cyan] first to fetch release years.")
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
        console.print("[yellow]No release year data matched. Run 'enrich-releases' with more albums.[/yellow]")
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

    for play_year in sorted(by_play_year.keys())[-10:]:  # Last 10 years
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


@app.command()
def genres(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Analyze specific year"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of genres to show"),
):
    """Analyze your listening by genre.

    Shows which genres dominate your listening, how they've evolved over time,
    and identifies genre blind spots.

    Requires the MusicBrainz database - run 'download-musicbrainz' first.
    """
    from . import musicbrainz_db
    from collections import defaultdict
    import sqlite3

    # Check for local database
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats:
        console.print("[yellow]No MusicBrainz database found.[/yellow]")
        console.print("Run [cyan]music-history download-musicbrainz[/cyan] first.")
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
        console.print("[yellow]No genre data found. Try running 'download-musicbrainz' first.[/yellow]")
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


@app.command()
def labels(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Analyze specific year"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of labels to show"),
):
    """Analyze your listening by record label.

    Discover which labels' releases dominate your listening.

    Requires the MusicBrainz database - run 'download-musicbrainz' first.
    """
    from . import musicbrainz_db
    from collections import defaultdict
    import sqlite3

    # Check for local database
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats:
        console.print("[yellow]No MusicBrainz database found.[/yellow]")
        console.print("Run [cyan]music-history download-musicbrainz[/cyan] first.")
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
        console.print("[yellow]No label data found. Try running 'download-musicbrainz' first.[/yellow]")
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


@app.command()
def countries(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Analyze specific year"),
    limit: int = typer.Option(15, "--limit", "-n", help="Number of countries to show"),
):
    """Analyze your listening by release country.

    See which countries' releases dominate your listening.

    Requires the MusicBrainz database - run 'download-musicbrainz' first.
    """
    from . import musicbrainz_db
    from collections import defaultdict
    import sqlite3

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
        console.print("Run [cyan]music-history download-musicbrainz[/cyan] first.")
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
        console.print("[yellow]No country data found. Try running 'download-musicbrainz' first.[/yellow]")
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


@app.command()
def release_types(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    year: Optional[int] = typer.Option(None, "--year", "-y", help="Analyze specific year"),
):
    """Analyze your listening by release type.

    Shows breakdown of albums vs EPs vs singles vs compilations, etc.

    Requires the MusicBrainz database - run 'download-musicbrainz' first.
    """
    from . import musicbrainz_db
    from collections import defaultdict
    import sqlite3

    # Check for local database
    db_stats = musicbrainz_db.get_database_stats()
    if not db_stats:
        console.print("[yellow]No MusicBrainz database found.[/yellow]")
        console.print("Run [cyan]music-history download-musicbrainz[/cyan] first.")
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

    # Summary insight
    album_pct = type_plays.get("album", 0) / total_plays_matched * 100 if total_plays_matched else 0
    non_album = total_plays_matched - type_plays.get("album", 0)
    non_album_pct = non_album / total_plays_matched * 100 if total_plays_matched else 0

    console.print(f"\n[dim]You listen to {album_pct:.0f}% studio albums, {non_album_pct:.0f}% other formats.[/dim]")


if __name__ == "__main__":
    app()
