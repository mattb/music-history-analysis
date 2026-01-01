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
):
    """Show top artists, albums, or tracks by play count."""
    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)

    if what == "artists":
        result = data.top_artists(df, limit)
        table = Table(title=f"Top {limit} Artists" + (f" ({year})" if year else ""))
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


@app.command()
def crawl(
    output: Path = typer.Option(
        Path("critics-2025.json"),
        "--output", "-o",
        help="Output JSON file path",
    ),
    delay: float = typer.Option(
        0.5,
        "--delay", "-d",
        help="Delay between requests in seconds",
    ),
):
    """Crawl yearendlists.com for 2025 album lists."""
    from . import crawler

    console.print("[bold]Crawling yearendlists.com for 2025 album lists...[/bold]\n")
    lists = asyncio.run(crawler.run_crawler(output, delay))

    # Summary
    total_albums = sum(len(lst.albums) for lst in lists)
    console.print(f"\n[bold green]Done![/bold green]")
    console.print(f"  Lists crawled: {len(lists)}")
    console.print(f"  Total album entries: {total_albums:,}")
    console.print(f"  Output: {output}")


DEFAULT_CRITICS_JSON = Path(__file__).parent.parent / "critics-2025.json"


@app.command()
def matched(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c", help="Path to Last.fm CSV export"),
    critics_json: Optional[Path] = typer.Option(None, "--critics", help="Path to critics JSON"),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Show critic-loved albums you've listened to in 2025."""
    from . import crossref

    df = data.load_scrobbles(get_csv_path(csv))
    critics_data = crossref.load_critics_data(critics_json or DEFAULT_CRITICS_JSON)
    results = crossref.match_with_history(critics_data, df, year=2025)

    console.print(f"\n[bold cyan]Albums You've Heard That Critics Love[/bold cyan]")
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
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
    known_artists: bool = typer.Option(False, "--known", "-k", help="Only show artists you've heard"),
):
    """Show highly-rated 2025 albums you haven't listened to."""
    from . import crossref

    df = data.load_scrobbles(get_csv_path(csv))
    critics_data = crossref.load_critics_data(critics_json or DEFAULT_CRITICS_JSON)
    results = crossref.match_with_history(critics_data, df, year=2025)

    unheard = results['unheard']
    if known_artists:
        unheard = [u for u in unheard if u['heard_artist']]

    title = "Unheard Albums From Artists You Know" if known_artists else "Highly-Rated Albums You Haven't Heard"
    console.print(f"\n[bold cyan]{title}[/bold cyan]\n")

    table = Table(show_header=True)
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Artist Plays", justify="right", style="dim")

    for i, u in enumerate(unheard[:limit], 1):
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
):
    """Show summary of overlap between your listening and critics' picks."""
    from . import crossref

    df = data.load_scrobbles(get_csv_path(csv))
    critics_data = crossref.load_critics_data(critics_json or DEFAULT_CRITICS_JSON)
    results = crossref.match_with_history(critics_data, df, year=2025)

    stats = results['stats']

    console.print("\n[bold magenta]═══ Critics vs Your 2025 Listening ═══[/bold magenta]\n")

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
    sort: str = typer.Option("overlap", "--sort", "-s", help="Sort by: overlap, albums, name"),
):
    """Show overview of critics and your overlap with each."""
    from . import crossref
    import json

    # Load critics data
    json_path = critics_json or DEFAULT_CRITICS_JSON
    with open(json_path) as f:
        raw_data = json.load(f)

    # Load your listening data for overlap calculation
    df = data.load_scrobbles(get_csv_path(csv))
    df_2025 = df[df['year'] == 2025]

    # Build set of your albums (normalized)
    your_albums = set()
    for _, row in df_2025.iterrows():
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

    console.print(f"\n[bold magenta]═══ Critics Overview ({len(critic_stats)} critics) ═══[/bold magenta]\n")

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
):
    """Show which critics listed a given artist."""
    import json
    from . import crossref

    json_path = critics_json or DEFAULT_CRITICS_JSON
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

    console.print(f"\n[bold cyan]{matches[0]['artist']}[/bold cyan] appears on [bold]{len(set(m['critic'] for m in matches))}[/bold] critics' lists\n")

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


if __name__ == "__main__":
    app()
