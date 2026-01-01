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
            console.print(f"[red]No critics data for {year}. Run 'lastfm crawl --year {year}' first.[/red]")
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
            console.print("4. Run: [cyan]lastfm spotify-auth --client-id YOUR_ID --client-secret YOUR_SECRET[/cyan]")
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
        console.print("Run [cyan]lastfm spotify-auth[/cyan] first.")
        raise typer.Exit(1)

    # Load data (same as review command)
    df_full = data.load_scrobbles(get_csv_path(csv))
    df = data.filter_by_year(df_full, year)

    json_path = critics_json or get_critics_path(year)
    if not json_path.exists():
        console.print(f"[red]No critics data for {year}. Run 'lastfm crawl --year {year}' first.[/red]")
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


if __name__ == "__main__":
    app()
