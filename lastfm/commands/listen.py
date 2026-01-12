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
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show top artists, albums, or tracks by play count."""
    import json
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
        table.add_column("Rank", justify="right", style="dim")
        table.add_column("Artist", style="cyan")
        table.add_column("Plays", justify="right", style="green")
        table.add_column("Margin", justify="right", style="yellow")

        # Calculate margins between consecutive ranks
        plays_list = result["plays"].tolist()
        for idx, (_, row) in enumerate(result.iterrows()):
            rank = idx + 1
            plays = row["plays"]

            # Calculate margin to next rank
            if idx < len(plays_list) - 1:
                next_plays = plays_list[idx + 1]
                margin_pct = ((plays - next_plays) / plays * 100) if plays > 0 else 0

                # Visual indicator: close margins get a "~" to show statistical similarity
                if margin_pct < 5:
                    margin_str = f"~{margin_pct:.1f}%"  # Very close
                elif margin_pct < 15:
                    margin_str = f"{margin_pct:.1f}%"   # Close
                else:
                    margin_str = f"+{margin_pct:.0f}%"  # Clear lead
            else:
                margin_str = "—"

            table.add_row(str(rank), row["artist"], str(plays), margin_str)

    elif what == "albums":
        result = data.top_albums(df, limit)
        table = Table(title=f"Top {limit} Albums" + (f" ({year})" if year else ""))
        table.add_column("Rank", justify="right", style="dim")
        table.add_column("Artist", style="cyan")
        table.add_column("Album", style="yellow")
        table.add_column("Plays", justify="right", style="green")
        table.add_column("Margin", justify="right", style="yellow")

        plays_list = result["plays"].tolist()
        for idx, (_, row) in enumerate(result.iterrows()):
            rank = idx + 1
            plays = row["plays"]

            if idx < len(plays_list) - 1:
                next_plays = plays_list[idx + 1]
                margin_pct = ((plays - next_plays) / plays * 100) if plays > 0 else 0
                if margin_pct < 5:
                    margin_str = f"~{margin_pct:.1f}%"
                elif margin_pct < 15:
                    margin_str = f"{margin_pct:.1f}%"
                else:
                    margin_str = f"+{margin_pct:.0f}%"
            else:
                margin_str = "—"

            table.add_row(str(rank), row["artist"], row["album"], str(plays), margin_str)

    elif what == "tracks":
        result = data.top_tracks(df, limit)
        table = Table(title=f"Top {limit} Tracks" + (f" ({year})" if year else ""))
        table.add_column("Rank", justify="right", style="dim")
        table.add_column("Artist", style="cyan")
        table.add_column("Track", style="yellow")
        table.add_column("Plays", justify="right", style="green")
        table.add_column("Margin", justify="right", style="yellow")

        plays_list = result["plays"].tolist()
        for idx, (_, row) in enumerate(result.iterrows()):
            rank = idx + 1
            plays = row["plays"]

            if idx < len(plays_list) - 1:
                next_plays = plays_list[idx + 1]
                margin_pct = ((plays - next_plays) / plays * 100) if plays > 0 else 0
                if margin_pct < 5:
                    margin_str = f"~{margin_pct:.1f}%"
                elif margin_pct < 15:
                    margin_str = f"{margin_pct:.1f}%"
                else:
                    margin_str = f"+{margin_pct:.0f}%"
            else:
                margin_str = "—"

            table.add_row(str(rank), row["artist"], row["track"], str(plays), margin_str)

    else:
        console.print(f"[red]Unknown type: {what}. Use artists, albums, or tracks.[/red]")
        raise typer.Exit(1)

    if json_output:
        # Convert result DataFrame to JSON-friendly format
        records = []
        for idx, (_, row) in enumerate(result.iterrows()):
            record = {"rank": idx + 1, "plays": int(row["plays"])}
            if what == "artists":
                record["artist"] = row["artist"]
            elif what == "albums":
                record["artist"] = row["artist"]
                record["album"] = row["album"]
            elif what == "tracks":
                record["artist"] = row["artist"]
                record["track"] = row["track"]
            records.append(record)
        output = {"type": what, "year": year, "results": records}
        print(json.dumps(output, indent=2))
        return

    console.print(table)


@app.command(name="discovered")
def listen_discovered(
    ctx: typer.Context,
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
    show_gateway: bool = typer.Option(False, "--gateway", "-g", help="Show gateway artists (similar artists you knew before)"),
):
    """Show artists discovered (first played) in a given year."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025

    df = data.load_scrobbles(get_csv_path(csv))
    result = data.artists_discovered_in_year(df, year)

    # Build gateway artist lookup if requested
    gateway_lookup = {}  # artist -> list of gateway artists
    if show_gateway:
        from .. import embeddings

        console.print("[dim]Loading embeddings for gateway artist analysis...[/dim]\n")

        csv_path = get_csv_path(csv)
        try:
            user_emb = embeddings.build_embeddings_from_csv(csv_path, min_plays=5)

            # Get artists you played BEFORE the discovery year
            df_before = df[df["year"] < year]
            prior_artists = set(df_before["artist"].unique())

            for _, row in result.head(limit).iterrows():
                discovered_artist = row["artist"]
                discovery_date = row["timestamp"]

                # Find similar artists from your prior library
                try:
                    similar = user_emb.find_similar(discovered_artist, top_n=20)

                    # Filter to artists you played BEFORE this discovery
                    gateway_artists = []
                    for similar_artist, similarity in similar:
                        # Check if you played this artist before the discovery
                        artist_df = df[df["artist"] == similar_artist]
                        if not artist_df.empty:
                            first_play = artist_df["timestamp"].min()
                            if first_play < discovery_date:
                                gateway_artists.append((similar_artist, similarity))
                                if len(gateway_artists) >= 2:
                                    break

                    gateway_lookup[discovered_artist] = gateway_artists
                except ValueError:
                    # Artist not in embeddings
                    gateway_lookup[discovered_artist] = []
        except Exception as e:
            console.print(f"[yellow]Could not load embeddings: {e}[/yellow]\n")
            show_gateway = False

    table = Table(title=f"Artists Discovered in {year}")
    table.add_column("Artist", style="cyan")
    table.add_column("First Played", style="yellow")
    table.add_column(f"Plays in {year}", justify="right", style="green")
    if show_gateway:
        table.add_column("Gateway From", style="magenta")
    else:
        table.add_column("First Track", style="dim")

    for _, row in result.head(limit).iterrows():
        if show_gateway:
            gateways = gateway_lookup.get(row["artist"], [])
            gateway_str = ", ".join([a for a, _ in gateways]) if gateways else "-"
            table.add_row(
                row["artist"],
                row["timestamp"].strftime("%Y-%m-%d"),
                str(int(row["plays_in_year"])),
                gateway_str,
            )
        else:
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


@app.command(name="streaks")
def listen_streaks(
    ctx: typer.Context,
    top_artists: int = typer.Option(10, "--artists", "-a", help="Show top N artist-specific streaks"),
):
    """Show listening streaks - consecutive days with activity."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    # Get unique dates (ignore time, just date)
    df['date'] = df['timestamp'].dt.date
    unique_dates = sorted(df['date'].unique())

    if not unique_dates:
        console.print("[yellow]No listening history found[/yellow]")
        return

    # Calculate overall streaks
    current_date = datetime.now(timezone.utc).date()
    all_streaks = []
    current_streak_days = []

    for i, date in enumerate(unique_dates):
        if i == 0:
            current_streak_days = [date]
        else:
            # Check if consecutive day
            prev_date = unique_dates[i - 1]
            if (date - prev_date).days == 1:
                current_streak_days.append(date)
            else:
                # Streak broken, save it
                if len(current_streak_days) > 0:
                    all_streaks.append(current_streak_days.copy())
                current_streak_days = [date]

    # Don't forget the last streak
    if current_streak_days:
        all_streaks.append(current_streak_days)

    # Find longest overall streak
    longest_streak = max(all_streaks, key=len) if all_streaks else []

    # Check if current streak is active (includes today or yesterday)
    active_streak = []
    if all_streaks:
        last_streak = all_streaks[-1]
        last_date = last_streak[-1]
        days_since = (current_date - last_date).days
        if days_since <= 1:  # Today or yesterday
            active_streak = last_streak

    # Display overall streaks
    console.print("\n[bold cyan]═══ Listening Streaks ═══[/bold cyan]\n")

    console.print(f"[bold]Longest Overall Streak:[/bold] {len(longest_streak)} days")
    if longest_streak:
        console.print(f"  {longest_streak[0]} to {longest_streak[-1]}")

    if active_streak:
        console.print(f"\n[bold green]Current Streak:[/bold green] {len(active_streak)} days 🔥")
        console.print(f"  Started: {active_streak[0]}")
    else:
        last_date = unique_dates[-1] if unique_dates else None
        if last_date:
            days_since = (current_date - last_date).days
            console.print(f"\n[dim]Current Streak: 0 days (last listened {days_since} days ago)[/dim]")

    # Calculate artist-specific streaks
    console.print(f"\n[bold]Top {top_artists} Artist Streaks:[/bold]\n")

    artist_streaks = {}  # artist -> (start_date, end_date, length)

    for artist in df['artist'].unique():
        if pd.isna(artist):
            continue

        artist_df = df[df['artist'] == artist]
        artist_dates = sorted(artist_df['date'].unique())

        if not artist_dates:
            continue

        # Find streaks for this artist
        streaks = []
        current = [artist_dates[0]]

        for i in range(1, len(artist_dates)):
            if (artist_dates[i] - artist_dates[i-1]).days == 1:
                current.append(artist_dates[i])
            else:
                if len(current) >= 2:  # Only count streaks of 2+ days
                    streaks.append((current[0], current[-1], len(current)))
                current = [artist_dates[i]]

        # Don't forget last streak
        if len(current) >= 2:
            streaks.append((current[0], current[-1], len(current)))

        if streaks:
            # Keep only longest streak for this artist
            longest = max(streaks, key=lambda x: x[2])
            artist_streaks[artist] = longest

    # Sort by streak length
    top_artist_streaks = sorted(artist_streaks.items(), key=lambda x: x[1][2], reverse=True)[:top_artists]

    if top_artist_streaks:
        table = Table(show_header=True)
        table.add_column("#", justify="right", width=3)
        table.add_column("Artist", style="cyan")
        table.add_column("Streak", justify="right", style="green")
        table.add_column("Period", style="dim")

        for i, (artist, (start, end, length)) in enumerate(top_artist_streaks, 1):
            table.add_row(
                str(i),
                artist[:30] + "..." if len(artist) > 30 else artist,
                f"{length} days",
                f"{start} to {end}"
            )

        console.print(table)
    else:
        console.print("[dim]No artist streaks of 2+ days found[/dim]")


@app.command(name="echo-chamber")
def listen_echo_chamber(
    ctx: typer.Context,
):
    """Measure your taste insularity - are you stuck in an echo chamber?"""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else datetime.now(timezone.utc).year

    df = data.load_scrobbles(get_csv_path(csv))
    df_year = data.filter_by_year(df, year)

    if df_year.empty:
        console.print(f"[yellow]No listening data for {year}[/yellow]")
        return

    total_plays = len(df_year)

    # 1. Artist Repeat Rate - % plays from top 10 artists
    artist_plays = df_year.groupby('artist').size().sort_values(ascending=False)
    top10_plays = artist_plays.head(10).sum()
    repeat_rate = (top10_plays / total_plays * 100) if total_plays > 0 else 0

    # 2. Artist Concentration (Herfindahl-style)
    # Higher = more concentrated (worse for diversity)
    top20_plays = artist_plays.head(20).sum()
    concentration_pct = (top20_plays / total_plays * 100) if total_plays > 0 else 0

    # 3. Discovery Rate - unique artists per 1000 plays
    unique_artists = df_year['artist'].nunique()
    discovery_rate = (unique_artists / total_plays * 1000) if total_plays > 0 else 0

    # 4. Artist Diversity Score (inverse of concentration)
    # How many artists does it take to reach 50% of plays?
    cumsum = artist_plays.cumsum()
    half_plays = total_plays / 2
    artists_for_50pct = len(cumsum[cumsum <= half_plays]) + 1

    # Calculate overall Echo Chamber Score (0-100, higher = worse)
    # Weight the metrics
    repeat_score = min(100, repeat_rate)  # 0-100
    concentration_score = min(100, concentration_pct)  # 0-100
    diversity_score = max(0, 100 - (artists_for_50pct * 5))  # Inverse: fewer artists = higher score
    discovery_score = max(0, 100 - (discovery_rate * 2))  # Inverse: lower discovery = higher score

    echo_chamber_score = (
        repeat_score * 0.35 +
        concentration_score * 0.35 +
        diversity_score * 0.20 +
        discovery_score * 0.10
    )

    # Determine risk level
    if echo_chamber_score >= 70:
        risk_level = "[bold red]CRITICAL"
        risk_emoji = "🚨"
        recommendation = "You're in a serious echo chamber! Try exploring new artists."
    elif echo_chamber_score >= 55:
        risk_level = "[bold yellow]HIGH"
        risk_emoji = "⚠️"
        recommendation = "Your listening is quite concentrated. Consider diversifying."
    elif echo_chamber_score >= 40:
        risk_level = "[bold cyan]MODERATE"
        risk_emoji = "ℹ️"
        recommendation = "Decent balance, but room for more exploration."
    else:
        risk_level = "[bold green]LOW"
        risk_emoji = "✅"
        recommendation = "Great diversity! You're exploring widely."

    # Display results
    console.print(f"\n[bold magenta]═══ Echo Chamber Analysis ({year}) ═══[/bold magenta]\n")

    console.print(f"{risk_emoji}  [bold]Echo Chamber Risk Score: {echo_chamber_score:.1f}/100[/bold]")
    console.print(f"   Risk Level: {risk_level}\n")

    # Detailed metrics
    table = Table(show_header=True, box=None)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="yellow")
    table.add_column("Assessment", style="dim")

    # Top 10 repeat rate
    if repeat_rate >= 70:
        assessment = "Very narrow"
    elif repeat_rate >= 50:
        assessment = "Concentrated"
    elif repeat_rate >= 30:
        assessment = "Balanced"
    else:
        assessment = "Diverse"
    table.add_row("Top 10 Artist Repeat Rate", f"{repeat_rate:.1f}%", assessment)

    # Top 20 concentration
    if concentration_pct >= 85:
        assessment = "Extremely concentrated"
    elif concentration_pct >= 70:
        assessment = "Concentrated"
    elif concentration_pct >= 50:
        assessment = "Moderate"
    else:
        assessment = "Well distributed"
    table.add_row("Top 20 Concentration", f"{concentration_pct:.1f}%", assessment)

    # Artists for 50%
    if artists_for_50pct <= 5:
        assessment = "Very narrow taste"
    elif artists_for_50pct <= 10:
        assessment = "Narrow taste"
    elif artists_for_50pct <= 20:
        assessment = "Moderate diversity"
    else:
        assessment = "High diversity"
    table.add_row("Artists for 50% of Plays", str(artists_for_50pct), assessment)

    # Discovery rate
    if discovery_rate >= 40:
        assessment = "Highly exploratory"
    elif discovery_rate >= 25:
        assessment = "Exploratory"
    elif discovery_rate >= 15:
        assessment = "Moderate"
    else:
        assessment = "Low exploration"
    table.add_row("Discovery Rate", f"{discovery_rate:.1f}/1000", assessment)

    # Total unique artists
    table.add_row("Total Unique Artists", f"{unique_artists:,}", "")

    console.print(table)

    console.print(f"\n[italic]{recommendation}[/italic]\n")

    # Show top artists contributing to concentration
    console.print("[bold]Your Top 10 Most-Played Artists:[/bold]")
    for i, (artist, plays) in enumerate(artist_plays.head(10).items(), 1):
        pct = (plays / total_plays * 100)
        console.print(f"  {i:2d}. {artist[:35]:<35} {plays:>5} plays ({pct:>5.1f}%)")


@app.command(name="similar")
def listen_similar(
    ctx: typer.Context,
    artist: str = typer.Argument(..., help="Artist name to find similar artists for"),
    limit: int = typer.Option(15, "--limit", "-n", help="Number of similar artists to show"),
    rebuild: bool = typer.Option(False, "--rebuild", help="Force rebuild embeddings cache"),
):
    """Find artists similar to a given artist using listening patterns.

    Uses co-occurrence analysis and machine learning to find artists you listen to
    in similar contexts (same time periods, similar listening patterns).
    """
    from .. import embeddings

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

    console.print(f"\n[bold cyan]Finding artists similar to: {artist}[/bold cyan]\n")

    # Build or load embeddings
    csv_path = get_csv_path(csv)
    try:
        artist_embeddings = embeddings.build_embeddings_from_csv(
            csv_path,
            n_components=50,
            min_plays=5,
            force_rebuild=rebuild,
        )
    except Exception as e:
        console.print(f"[red]Error building embeddings: {e}[/red]")
        raise typer.Exit(1)

    # Find similar artists
    try:
        similar = artist_embeddings.find_similar(artist, top_n=limit, min_similarity=0.1)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        console.print(f"\n[dim]Try searching for a partial name or check spelling.[/dim]\n")
        raise typer.Exit(1)

    if not similar:
        console.print(f"[yellow]No similar artists found.[/yellow]\n")
        return

    # Display results
    console.print(f"[dim]Based on co-listening patterns across your history[/dim]\n")

    table = Table(show_header=True)
    table.add_column("Rank", justify="right", style="dim")
    table.add_column("Artist", style="cyan")
    table.add_column("Similarity", justify="right", style="green")
    table.add_column("Match", justify="right", style="yellow")

    for i, (similar_artist, similarity) in enumerate(similar, 1):
        # Convert similarity to percentage
        match_pct = similarity * 100

        # Visual indicator
        if match_pct >= 80:
            indicator = "▰▰▰▰▰"
        elif match_pct >= 60:
            indicator = "▰▰▰▰▱"
        elif match_pct >= 40:
            indicator = "▰▰▰▱▱"
        elif match_pct >= 20:
            indicator = "▰▰▱▱▱"
        else:
            indicator = "▰▱▱▱▱"

        table.add_row(
            str(i),
            similar_artist,
            f"{similarity:.3f}",
            f"{indicator} {match_pct:.1f}%"
        )

    console.print(table)
    console.print(f"\n[dim]💡 Tip: Use --rebuild to recalculate embeddings with updated data[/dim]\n")


@app.command(name="dimensions")
def listen_dimensions(
    ctx: typer.Context,
    dimension: Optional[int] = typer.Option(None, "--dim", "-d", help="Specific dimension to analyze (0-49)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of top dimensions to show"),
    poles: int = typer.Option(5, "--poles", "-p", help="Number of artists to show at each pole"),
    critics: bool = typer.Option(False, "--critics", "-c", help="Analyze critics embeddings instead of user embeddings"),
    rebuild: bool = typer.Option(False, "--rebuild", help="Force rebuild embeddings cache"),
):
    """Interpret SVD dimensions - what each axis represents in your taste space.

    Shows the artists at each extreme of the embedding dimensions, revealing
    what each dimension captures (e.g., genre, era, or listening context).

    Examples:
        lastfm listen dimensions           # Top 10 dimensions by variance
        lastfm listen dimensions --dim 3   # Analyze dimension 3 specifically
        lastfm listen dimensions --critics # Analyze critics embedding dimensions
    """
    from .. import embeddings

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

    if critics:
        console.print("\n[bold cyan]Critics Embedding Dimensions[/bold cyan]")
        console.print("[dim]What dimensions capture 'critical consensus' similarity[/dim]\n")

        try:
            emb = embeddings.get_or_build_critics_embeddings(force_rebuild=rebuild)
        except Exception as e:
            console.print(f"[red]Error building critics embeddings: {e}[/red]")
            raise typer.Exit(1)
    else:
        console.print("\n[bold cyan]Your Taste Space Dimensions[/bold cyan]")
        console.print("[dim]What dimensions capture your co-listening patterns[/dim]\n")

        csv_path = get_csv_path(csv)
        try:
            emb = embeddings.build_embeddings_from_csv(
                csv_path,
                n_components=50,
                min_plays=5,
                force_rebuild=rebuild,
            )
        except Exception as e:
            console.print(f"[red]Error building embeddings: {e}[/red]")
            raise typer.Exit(1)

    # Get explained variance
    variance = emb.get_explained_variance()
    total_variance = variance.sum() * 100

    console.print(f"[bold]Total variance explained:[/bold] {total_variance:.1f}%")
    console.print(f"[bold]Dimensions:[/bold] {len(variance)}\n")

    if dimension is not None:
        # Analyze single dimension
        if dimension < 0 or dimension >= len(variance):
            console.print(f"[red]Dimension must be 0 to {len(variance) - 1}[/red]")
            raise typer.Exit(1)

        _show_dimension(emb, dimension, variance[dimension], poles)
    else:
        # Show top dimensions by variance
        sorted_dims = sorted(range(len(variance)), key=lambda i: -variance[i])

        for i, dim_idx in enumerate(sorted_dims[:limit]):
            if i > 0:
                console.print()  # Blank line between dimensions
            _show_dimension(emb, dim_idx, variance[dim_idx], poles)


def _show_dimension(emb, dim_idx: int, variance_ratio: float, poles: int):
    """Display a single dimension's pole artists."""
    poles_data = emb.get_dimension_poles(dim_idx, top_n=poles)

    console.print(f"[bold yellow]Dimension {dim_idx}[/bold yellow] ({variance_ratio * 100:.1f}% variance)")

    # Show positive pole
    pos_artists = [a for a, _ in poles_data["positive"]]
    console.print(f"  [green]+[/green] {', '.join(pos_artists)}")

    # Show negative pole
    neg_artists = [a for a, _ in poles_data["negative"]]
    console.print(f"  [red]-[/red] {', '.join(neg_artists)}")


@app.command(name="obsessions")
def listen_obsessions(
    ctx: typer.Context,
    min_plays: int = typer.Option(20, "--min-plays", "-m", help="Minimum plays for a track"),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Find tracks you obsessed over without exploring their albums.

    Shows tracks with high play counts where you never really explored
    the rest of the album - your "obsession tracks".
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    familiarity = ctx.obj.get("familiarity", 0.4) if ctx.obj else 0.4

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)

    result = data.get_obsession_tracks(
        df,
        min_plays=min_plays,
        max_familiarity=familiarity,
    )

    if result.empty:
        console.print(f"[yellow]No obsession tracks found with {min_plays}+ plays[/yellow]")
        return

    result = result.head(limit)

    title = f"Obsession Tracks ({year})" if year else "Obsession Tracks (All Time)"
    table = Table(title=title)
    table.add_column("Track", style="cyan")
    table.add_column("Artist", style="dim")
    table.add_column("Plays", justify="right", style="green")
    table.add_column("Peak Year(s)", justify="right", style="magenta")
    table.add_column("Album", style="yellow")
    table.add_column("Familiarity", justify="right", style="red")
    table.add_column("% of Album", justify="right", style="dim")

    for _, row in result.iterrows():
        track = row["track"]
        if len(track) > 35:
            track = track[:32] + "..."

        album = row["album"]
        if len(album) > 25:
            album = album[:22] + "..."

        fam_score = row["album_familiarity"]
        tracks_on = int(row["tracks_on_album"])
        fam_display = f"{fam_score:.2f} ({tracks_on} trk)"

        # Format peak years
        peak_years = row.get("peak_years", [])
        if peak_years:
            peak_str = ", ".join(str(y) for y in peak_years)
        else:
            peak_str = "-"

        table.add_row(
            track,
            row["artist"],
            str(int(row["plays"])),
            peak_str,
            album,
            fam_display,
            f"{row['pct_of_album_plays']:.0f}%",
        )

    console.print(table)
    console.print(f"\n[dim]Showing tracks with {min_plays}+ plays from albums with <{familiarity:.0%} familiarity[/dim]")


@app.command(name="one-hit")
def listen_one_hit(
    ctx: typer.Context,
    min_concentration: float = typer.Option(0.7, "--min-concentration", "-c", help="Min % of plays on top track (0-1)"),
    min_plays: int = typer.Option(10, "--min-plays", "-m", help="Minimum plays on top track"),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Find artists where one track dominates your listening.

    Shows artists where you've only really engaged with a single song -
    your "one-hit" relationships with artists.
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)

    result = data.get_one_track_artists(
        df,
        min_concentration=min_concentration,
        min_top_track_plays=min_plays,
    )

    if result.empty:
        console.print(f"[yellow]No one-hit artists found[/yellow]")
        return

    result = result.head(limit)

    title = f"One-Hit Artists ({year})" if year else "One-Hit Artists (All Time)"
    table = Table(title=title)
    table.add_column("Artist", style="cyan")
    table.add_column("The Track", style="yellow")
    table.add_column("Plays", justify="right", style="green")
    table.add_column("Peak Year(s)", justify="right", style="magenta")
    table.add_column("Concentration", justify="right", style="dim")

    for _, row in result.iterrows():
        artist = row["artist"]
        if len(artist) > 25:
            artist = artist[:22] + "..."

        track = row["top_track"]
        if len(track) > 35:
            track = track[:32] + "..."

        concentration_pct = row["concentration"] * 100

        # Format peak years
        peak_years = row.get("peak_years", [])
        if peak_years:
            if len(peak_years) == 1:
                years_str = str(peak_years[0])
            elif len(peak_years) == 2:
                years_str = f"{peak_years[0]}, {peak_years[1]}"
            else:
                # Show range if more than 2
                years_str = f"{peak_years[0]}-{peak_years[-1]}"
        else:
            years_str = "-"

        table.add_row(
            artist,
            track,
            str(int(row["top_track_plays"])),
            years_str,
            f"{concentration_pct:.0f}%",
        )

    console.print(table)
    console.print(f"\n[dim]Artists where {min_concentration*100:.0f}%+ of plays are on one track[/dim]")


@app.command(name="ep-artists")
def listen_ep_artists(
    ctx: typer.Context,
    min_ratio: float = typer.Option(0.5, "--min-ratio", "-r", help="Min ratio of EP/single plays (0-1)"),
    min_plays: int = typer.Option(20, "--min-plays", "-m", help="Minimum total plays"),
    limit: int = typer.Option(30, "--limit", "-n", help="Number of results"),
):
    """Find artists where you mainly listen to EPs/singles, not albums.

    Shows artists who primarily release EPs and singles rather than
    traditional albums - typical for electronic producers and remixers.

    Requires MusicBrainz database (run 'lastfm metadata download' first).
    """
    from .. import musicbrainz_db

    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    # Check if MusicBrainz DB exists
    if not musicbrainz_db.database_exists():
        console.print("[red]MusicBrainz database not found.[/red]")
        console.print("[yellow]Run 'lastfm metadata download' first to download release type data.[/yellow]")
        raise typer.Exit(1)

    df = data.load_scrobbles(get_csv_path(csv))

    if year:
        df = data.filter_by_year(df, year)

    # Create a lookup function that uses the MusicBrainz DB
    conn = musicbrainz_db.get_connection()

    def lookup(artist: str, album: str):
        return musicbrainz_db.lookup_release(artist, album, conn)

    console.print("[dim]Looking up release types from MusicBrainz...[/dim]\n")

    result = data.get_ep_single_artists(
        df,
        musicbrainz_lookup=lookup,
        min_non_album_ratio=min_ratio,
        min_total_plays=min_plays,
    )

    conn.close()

    if result.empty:
        console.print(f"[yellow]No EP/single-heavy artists found[/yellow]")
        return

    result = result.head(limit)

    title = f"EP/Single Artists ({year})" if year else "EP/Single Artists (All Time)"
    table = Table(title=title)
    table.add_column("Artist", style="cyan")
    table.add_column("Album", justify="right", style="dim")
    table.add_column("EP/Single", justify="right", style="green")
    table.add_column("Ratio", justify="right", style="yellow")
    table.add_column("Top Non-Album", style="magenta")

    for _, row in result.iterrows():
        artist = row["artist"]
        if len(artist) > 25:
            artist = artist[:22] + "..."

        top_release = row["top_non_album"] or "-"
        if len(top_release) > 30:
            top_release = top_release[:27] + "..."

        ratio_pct = row["non_album_ratio"] * 100

        table.add_row(
            artist,
            str(int(row["album_plays"])),
            str(int(row["ep_single_plays"])),
            f"{ratio_pct:.0f}%",
            top_release,
        )

    console.print(table)
    console.print(f"\n[dim]Artists with {min_ratio*100:.0f}%+ plays from EPs/singles vs albums[/dim]")
