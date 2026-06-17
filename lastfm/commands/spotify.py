"""Spotify commands - Spotify integration."""

import typer
from pathlib import Path
from typing import Optional
import json
from rich.console import Console

from .. import data, crossref

app = typer.Typer(help="Spotify integration")
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


@app.command(name="auth")
def spotify_auth(
    ctx: typer.Context,
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
    from .. import spotify

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
            console.print("4. Run: [cyan]music-history spotify auth --client-id YOUR_ID --client-secret YOUR_SECRET[/cyan]")
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


@app.command(name="playlist")
def spotify_playlist(
    ctx: typer.Context,
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
    from .. import spotify

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025

    # Check for Spotify credentials
    sp = spotify.get_spotify_client()
    if not sp:
        console.print("[red]Spotify not configured.[/red]")
        console.print("Run [cyan]music-history spotify auth[/cyan] first.")
        raise typer.Exit(1)

    # Load data (same as review command)
    df_full = data.load_scrobbles(get_csv_path(csv))
    df = data.filter_by_year(df_full, year)

    json_path = get_critics_path(year)
    if not json_path.exists():
        console.print(f"[red]No critics data for {year}. Run 'music-history critics fetch --year {year}' first.[/red]")
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


@app.command(name="convert")
def spotify_convert(
    directory: Path = typer.Argument(..., help="Directory containing Spotify JSON files"),
    output: Path = typer.Option(None, "--output", "-o", help="Output CSV path"),
    min_duration: int = typer.Option(30, "--min-duration", "-d", help="Minimum play duration in seconds"),
    include_skipped: bool = typer.Option(False, "--include-skipped", help="Include skipped tracks"),
):
    """Convert Spotify Extended Streaming History to CSV format.

    Downloads your data from: https://www.spotify.com/account/privacy/
    Request "Extended streaming history" and extract the ZIP.

    The output CSV is compatible with all Music History commands and includes
    extended Spotify-specific columns (ms_played, shuffle, platform, etc.)
    for future analysis features.
    """
    from ..spotify_converter import convert_spotify_directory

    if not directory.exists():
        console.print(f"[red]Directory not found: {directory}[/red]")
        raise typer.Exit(1)

    # Check for JSON files
    json_files = list(directory.glob("Streaming_History_Audio_*.json"))
    if not json_files:
        console.print(f"[red]No Spotify streaming history files found in {directory}[/red]")
        console.print("[dim]Expected files like: Streaming_History_Audio_2023-2024_0.json[/dim]")
        raise typer.Exit(1)

    if output is None:
        output = Path.cwd() / "spotify-scrobbles.csv"

    console.print(f"[dim]Found {len(json_files)} Spotify history files[/dim]")

    min_ms = min_duration * 1000
    total, kept = convert_spotify_directory(
        directory, output,
        min_ms=min_ms,
        exclude_skipped=not include_skipped,
    )

    console.print(f"[green]Converted {kept:,} plays to {output}[/green]")
    console.print(f"[dim]Filtered out {total - kept:,} records (short plays, skips, podcasts)[/dim]")
