"""History commands - long-term taste evolution and patterns."""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console

from .. import data

app = typer.Typer(help="Long-term taste evolution")
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


@app.command(name="loyalty")
def history_loyalty(
    ctx: typer.Context,
    min_years: int = typer.Option(5, "--min-years", "-m", help="Minimum years to be considered loyal"),
):
    """Show your artist loyalty patterns over time.

    Identifies:
    - Long-term favorites (artists you've played for 5+ years)
    - Abandoned artists (used to play, stopped completely)
    - Rediscoveries (returned after a gap)
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

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


@app.command(name="evolution")
def history_evolution(
    ctx: typer.Context,
):
    """Show how your taste has evolved over time.

    Detects 'musical eras' - periods where certain artists dominated,
    and shows when key artists became staples in your listening.
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None

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
