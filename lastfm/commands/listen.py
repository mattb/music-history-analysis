"""Listen commands - basic listening analysis."""

import typer
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
from rich.console import Console
from rich.table import Table

from .. import data

app = typer.Typer(help="Analyze your listening patterns")
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


@app.command(name="top")
def listen_top(
    ctx: typer.Context,
    what: str = typer.Argument("artists", help="What to show: artists, albums, or tracks"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of results"),
    unselected: bool = typer.Option(False, "--unselected", "-u", help="Only show artists not picked by critics (requires --year)"),
    new_album: bool = typer.Option(False, "--new-album", "-a", help="Only artists with an album first heard that year"),
):
    """Show top artists, albums, or tracks by play count."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

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

        from .. import crossref
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

        from .. import crossref
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
            console.print(f"[red]No critics data for {year}. Run 'lastfm critics fetch --year {year}' first.[/red]")
            raise typer.Exit(1)

    if what == "artists":
        needs_filtering = unselected or new_album
        result = data.top_artists(df, limit if not needs_filtering else limit * 10)

        if needs_filtering:
            from .. import crossref
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


@app.command(name="discovered")
def listen_discovered(
    ctx: typer.Context,
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Show artists discovered (first played) in a given year."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025

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


@app.command(name="abandoned")
def listen_abandoned(
    ctx: typer.Context,
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Show artists abandoned (last played) in a given year."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025

    df = data.load_scrobbles(get_csv_path(csv))
    result = data.artists_abandoned_in_year(df, year)

    table = Table(title=f"Artists Abandoned in {year}")
    table.add_column("Artist", style="cyan")
    table.add_column("Last Played", style="yellow")
    table.add_column("Last Track", style="dim")
    table.add_column("Total Plays", justify="right", style="red")
    table.add_column(f"Plays in {year}", justify="right", style="dim")

    for _, row in result.head(limit).iterrows():
        table.add_row(
            row["artist"],
            row["timestamp"].strftime("%Y-%m-%d"),
            row["track"][:40] + "..." if len(row["track"]) > 40 else row["track"],
            str(int(row["total_plays"])),
            str(int(row["plays_in_year"])),
        )

    console.print(table)
    console.print(f"\nTotal artists abandoned in {year}: {len(result)}")


@app.command(name="first")
def listen_first(
    ctx: typer.Context,
    artist: str = typer.Argument(..., help="Artist name to look up"),
):
    """Show when you first played an artist."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

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


@app.command(name="plays")
def listen_plays(
    ctx: typer.Context,
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Filter to last N days"),
    artist: Optional[str] = typer.Option(None, "--artist", "-a", help="Filter to specific artist"),
    limit: int = typer.Option(50, "--limit", "-n", help="Number of results"),
):
    """List recent plays with optional filters."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

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
