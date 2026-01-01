"""Critics commands - cross-reference with music critics' year-end lists."""

import typer
from pathlib import Path
from typing import Optional
import json
import asyncio
from collections import defaultdict
import pandas as pd
from rich.console import Console
from rich.table import Table

from .. import data, crossref

app = typer.Typer(help="Cross-reference with music critics")
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
    return Path(__file__).parent.parent.parent / f"critics-{year}.json"


@app.command(name="fetch")
def critics_fetch(
    ctx: typer.Context,
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
    from .. import crawler

    # Override year from context if provided
    if ctx.obj and ctx.obj.get("year"):
        year = ctx.obj.get("year")

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


@app.command(name="matched")
def critics_matched(
    ctx: typer.Context,
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Show critic-loved albums you've listened to (across all years or filter by --year)."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    # Determine which years to search
    if year is not None:
        years_to_search = [year]
    else:
        years_to_search = []
        for y in range(2011, 2026):
            if get_critics_path(y).exists():
                years_to_search.append(y)

    # Build your albums set (albums properly listened to: 5+ tracks, 5+ plays each)
    listened_albums = data.get_albums_listened_to(df)
    your_albums_set = set()
    for artist, album in listened_albums:
        key = (crossref.normalize_for_matching(artist),
               crossref.normalize_for_matching(album))
        your_albums_set.add(key)

    # Count plays for albums we've properly listened to
    your_albums = {}  # (norm_artist, norm_album) -> plays
    df_with_albums = df[df["album"] != ""]
    for _, row in df_with_albums.iterrows():
        artist = row.get("artist", "")
        album = row.get("album", "")
        if pd.notna(artist) and pd.notna(album) and artist and album:
            key = (crossref.normalize_for_matching(artist),
                   crossref.normalize_for_matching(album))
            if key in your_albums_set:  # Only count plays for albums we've properly listened to
                your_albums[key] = your_albums.get(key, 0) + 1

    # Find matches across all years
    all_matches = {}  # (norm_artist, norm_album) -> {artist, album, critics_count, your_plays, years}
    total_critics_albums = 0

    for y in years_to_search:
        try:
            critics_data = crossref.load_critics_data(get_critics_path(y))
            results = crossref.match_with_history(critics_data, df, year=y)
            total_critics_albums += results['stats']['total_critics_albums']

            for m in results['matched']:
                key = (crossref.normalize_for_matching(m.artist),
                       crossref.normalize_for_matching(m.album))
                if key not in all_matches:
                    all_matches[key] = {
                        'artist': m.artist,
                        'album': m.album,
                        'critics_count': 0,
                        'your_plays': your_albums.get(key, 0),
                        'years': [],
                    }
                all_matches[key]['critics_count'] += m.critics_count
                all_matches[key]['years'].append(y)
        except (IOError, json.JSONDecodeError):
            continue

    matched_list = sorted(all_matches.values(), key=lambda x: -x['critics_count'])

    if year is not None:
        console.print(f"\n[bold cyan]Albums You've Heard That Critics Love ({year})[/bold cyan]")
    else:
        console.print(f"\n[bold cyan]Albums You've Heard That Critics Love (All Years)[/bold cyan]")
    console.print(f"Matched {len(matched_list)} albums from critics' lists\n")

    table = Table(show_header=True)
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Your Plays", justify="right", style="magenta")
    if year is None:
        table.add_column("Years", style="dim")

    for i, m in enumerate(matched_list[:limit], 1):
        years_str = ", ".join(map(str, sorted(m['years']))) if year is None else None
        if year is None:
            table.add_row(
                str(i),
                m['artist'],
                m['album'][:35] + "..." if len(m['album']) > 35 else m['album'],
                str(m['critics_count']),
                str(m['your_plays']),
                years_str,
            )
        else:
            table.add_row(
                str(i),
                m['artist'],
                m['album'][:35] + "..." if len(m['album']) > 35 else m['album'],
                str(m['critics_count']),
                str(m['your_plays']),
            )

    console.print(table)


@app.command(name="unheard")
def critics_unheard(
    ctx: typer.Context,
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
    known_artists: bool = typer.Option(False, "--known", "-k", help="Only show artists you've heard"),
    weighted: bool = typer.Option(False, "--weighted", "-w", help="Weight by critic overlap with your taste"),
):
    """Show highly-rated albums you haven't listened to (across all years or filter by --year)."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    # Determine which years to search
    if year is not None:
        years_to_search = [year]
    else:
        years_to_search = []
        for y in range(2011, 2026):
            if get_critics_path(y).exists():
                years_to_search.append(y)

    # Build set of your albums (albums properly listened to: 5+ tracks, 5+ plays each)
    listened_albums = data.get_albums_listened_to(df)
    your_albums = set()
    for artist, album in listened_albums:
        key = (crossref.normalize_for_matching(artist),
               crossref.normalize_for_matching(album))
        your_albums.add(key)

    # Build artist plays (all plays, not just listened albums)
    your_artists = {}  # norm_artist -> plays
    for _, row in df.iterrows():
        artist = row.get("artist", "")
        if pd.notna(artist) and artist:
            artist_norm = crossref.normalize_for_matching(artist)
            your_artists[artist_norm] = your_artists.get(artist_norm, 0) + 1

    # Aggregate unheard albums across all years
    all_unheard = {}  # (norm_artist, norm_album) -> {artist, album, critics_count, years, artist_plays}

    for y in years_to_search:
        try:
            critics_data = crossref.load_critics_data(get_critics_path(y))
            results = crossref.match_with_history(critics_data, df, year=y)

            for u in results['unheard']:
                key = (crossref.normalize_for_matching(u['artist']),
                       crossref.normalize_for_matching(u['album']))
                if key not in all_unheard:
                    all_unheard[key] = {
                        'artist': u['artist'],
                        'album': u['album'],
                        'critics_count': 0,
                        'years': [],
                        'artist_plays': your_artists.get(crossref.normalize_for_matching(u['artist']), 0),
                        'heard_artist': u['heard_artist'],
                    }
                all_unheard[key]['critics_count'] += u['critics_count']
                all_unheard[key]['years'].append(y)
        except (IOError, json.JSONDecodeError):
            continue

    unheard_list = list(all_unheard.values())
    if known_artists:
        unheard_list = [u for u in unheard_list if u['heard_artist']]

    # Sort by critics count (or weighted score if requested)
    if weighted:
        # Calculate weighted score across all years
        # For simplicity, weight by total critics_count (already aggregated)
        for u in unheard_list:
            u['weighted_score'] = u['critics_count']
        unheard_list = sorted(unheard_list, key=lambda x: -x['weighted_score'])
    else:
        unheard_list = sorted(unheard_list, key=lambda x: -x['critics_count'])

    # Display results
    if year is not None:
        title = f"Unheard Albums From Artists You Know ({year})" if known_artists else f"Highly-Rated Albums You Haven't Heard ({year})"
    else:
        title = f"Unheard Albums From Artists You Know (All Years)" if known_artists else f"Highly-Rated Albums You Haven't Heard (All Years)"

    console.print(f"\n[bold cyan]{title}[/bold cyan]\n")

    table = Table(show_header=True)
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Artist", style="cyan")
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Artist Plays", justify="right", style="dim")
    if year is None:
        table.add_column("Years", style="dim")

    for i, u in enumerate(unheard_list[:limit], 1):
        years_str = ", ".join(map(str, sorted(u['years']))) if year is None else None
        if year is None:
            table.add_row(
                str(i),
                u['artist'],
                u['album'][:35] + "..." if len(u['album']) > 35 else u['album'],
                str(u['critics_count']),
                str(u['artist_plays']) if u['artist_plays'] else "-",
                years_str,
            )
        else:
            table.add_row(
                str(i),
                u['artist'],
                u['album'][:35] + "..." if len(u['album']) > 35 else u['album'],
                str(u['critics_count']),
                str(u['artist_plays']) if u['artist_plays'] else "-",
            )

    console.print(table)


@app.command(name="overlap")
def critics_overlap(
    ctx: typer.Context,
):
    """Show summary of overlap between your listening and critics' picks."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025

    df = data.load_scrobbles(get_csv_path(csv))
    critics_data = crossref.load_critics_data(get_critics_path(year))
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


@app.command(name="list")
def critics_list(
    ctx: typer.Context,
    sort: str = typer.Option("overlap", "--sort", "-s", help="Sort by: overlap, albums, name"),
):
    """Show overview of critics and your overlap with each."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025

    # Load critics data
    json_path = get_critics_path(year)
    with open(json_path) as f:
        raw_data = json.load(f)

    # Load your listening data for overlap calculation
    df = data.load_scrobbles(get_csv_path(csv))
    df_year = df[df['year'] == year]

    # Build set of your albums (albums properly listened to: 5+ tracks, 5+ plays each)
    listened_albums = data.get_albums_listened_to(df_year)
    your_albums = set()
    for artist, album in listened_albums:
        key = (crossref.normalize_for_matching(artist),
               crossref.normalize_for_matching(album))
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


@app.command(name="who-listed")
def critics_who_listed(
    ctx: typer.Context,
    artist: str = typer.Argument(..., help="Artist name to search for"),
):
    """Show which critics listed a given artist across all years (or filter by --year)."""
    from pathlib import Path

    # Get global options from context
    year = ctx.obj.get("year") if ctx.obj else None

    # Determine which years to search
    if year is not None:
        years_to_search = [year]
    else:
        # Search all available years (2011-2025)
        years_to_search = []
        for y in range(2011, 2026):
            json_path = get_critics_path(y)
            if json_path.exists():
                years_to_search.append(y)

    if not years_to_search:
        console.print("[red]No critics data found[/red]")
        raise typer.Exit(1)

    # Normalize search term
    search_norm = crossref.normalize_for_matching(artist)

    # Find all matches across all years
    all_matches = []
    for y in years_to_search:
        json_path = get_critics_path(y)
        try:
            with open(json_path) as f:
                raw_data = json.load(f)

            for lst in raw_data:
                critic = lst['critic']
                for album in lst['albums']:
                    if album['artist']:
                        artist_norm = crossref.normalize_for_matching(album['artist'])
                        if search_norm in artist_norm or artist_norm in search_norm:
                            all_matches.append({
                                'year': y,
                                'critic': critic,
                                'artist': album['artist'],
                                'album': album['title'],
                                'rank': album['rank'],
                            })
        except (json.JSONDecodeError, IOError):
            continue

    if not all_matches:
        # Try partial match across all years
        partial = []
        for y in years_to_search:
            json_path = get_critics_path(y)
            try:
                with open(json_path) as f:
                    raw_data = json.load(f)
                for lst in raw_data:
                    for album in lst['albums']:
                        if album['artist'] and artist.lower() in album['artist'].lower():
                            partial.append(album['artist'])
            except (json.JSONDecodeError, IOError):
                continue

        if partial:
            console.print(f"[yellow]No exact match for '{artist}'. Did you mean:[/yellow]")
            for a in sorted(set(partial))[:10]:
                console.print(f"  - {a}")
        else:
            console.print(f"[red]No critics listed '{artist}'[/red]")
        raise typer.Exit(1)

    # Get canonical artist name
    canonical_artist = all_matches[0]['artist']

    # Summary
    total_critics = len(set(m['critic'] for m in all_matches))
    years_appeared = sorted(set(m['year'] for m in all_matches))

    if year is not None:
        console.print(f"\n[bold cyan]{canonical_artist}[/bold cyan] appears on [bold]{total_critics}[/bold] critics' {year} lists\n")
    else:
        console.print(f"\n[bold cyan]{canonical_artist}[/bold cyan] appears on [bold]{total_critics}[/bold] critics' lists across [bold]{len(years_appeared)}[/bold] years ({min(years_appeared)}-{max(years_appeared)})\n")

    # Group by year and album
    by_year_album = defaultdict(list)
    for m in all_matches:
        by_year_album[(m['year'], m['artist'], m['album'])].append((m['critic'], m['rank']))

    # Build table
    table = Table(show_header=True)
    if year is None:
        table.add_column("Year", style="dim", justify="right")
    table.add_column("Album", style="yellow")
    table.add_column("Critics", justify="right", style="green")
    table.add_column("Listed By", no_wrap=False)

    for (yr, artist_name, album), critics_list in sorted(by_year_album.items(), key=lambda x: (-x[0][0], -len(x[1]))):
        critics_str = ", ".join(sorted(set(c[0] for c in critics_list))[:20])  # Limit for readability
        if len(critics_list) > 20:
            critics_str += f", ... +{len(critics_list) - 20} more"

        if year is None:
            table.add_row(
                str(yr),
                album,
                str(len(critics_list)),
                critics_str,
            )
        else:
            table.add_row(
                album,
                str(len(critics_list)),
                critics_str,
            )

    console.print(table)


@app.command(name="blind-spots")
def critics_blind_spots(
    ctx: typer.Context,
    min_critics: int = typer.Option(20, "--min-critics", "-m", help="Minimum critics to be considered a blind spot"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of results"),
):
    """Find highly-acclaimed albums you've never explored.

    Shows albums that many critics loved but you've never played -
    your biggest gaps in critical consensus.
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    # Build set of all albums you've properly listened to (5+ tracks, 5+ plays each)
    listened_albums = data.get_albums_listened_to(df)
    your_albums = set()
    your_artists = set()
    for artist, album in listened_albums:
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


@app.command(name="accuracy")
def critics_accuracy(
    ctx: typer.Context,
    year: int = typer.Option(2020, "--year", "-y", help="Year of critic recommendations to check"),
):
    """Check if you ended up loving albums critics recommended years ago.

    Looks at critic picks from a past year and checks how much you've
    played them in subsequent years.
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

    # Override year from context if provided
    if ctx.obj and ctx.obj.get("year"):
        year = ctx.obj.get("year")

    df = data.load_scrobbles(get_csv_path(csv))

    json_path = get_critics_path(year)
    if not json_path.exists():
        console.print(f"[red]No critics data for {year}. Run 'lastfm critics fetch --year {year}' first.[/red]")
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


@app.command(name="tracker")
def critics_tracker(
    ctx: typer.Context,
    reference_year: int = typer.Option(2023, "--ref-year", "-r", help="Year to find your aligned critics"),
    target_year: int = typer.Option(2025, "--target-year", "-t", help="Year to get their new picks"),
    min_overlap: int = typer.Option(3, "--min-overlap", "-m", help="Minimum albums overlap to consider a critic aligned"),
):
    """Find critics who predicted your past favorites and see what they pick now.

    Uses a reference year to find critics whose picks matched your taste,
    then shows what those same critics are recommending for the target year.
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    # Load both years
    ref_path = get_critics_path(reference_year)
    target_path = get_critics_path(target_year)

    if not ref_path.exists():
        console.print(f"[red]No critics data for {reference_year}. Run 'lastfm critics fetch --year {reference_year}' first.[/red]")
        raise typer.Exit(1)
    if not target_path.exists():
        console.print(f"[red]No critics data for {target_year}. Run 'lastfm critics fetch --year {target_year}' first.[/red]")
        raise typer.Exit(1)

    with open(ref_path) as f:
        ref_data = json.load(f)
    with open(target_path) as f:
        target_data = json.load(f)

    # Build your albums set (albums properly listened to: 5+ tracks, 5+ plays each)
    listened_albums = data.get_albums_listened_to(df)
    your_albums = set()
    for artist, album in listened_albums:
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
