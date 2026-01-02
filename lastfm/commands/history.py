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

    # Statistical trend detection using Mann-Kendall test
    if len(yearly_data) >= 3:
        from scipy.stats import kendalltau

        console.print(f"\n[bold cyan]📊 STATISTICAL TREND ANALYSIS[/bold cyan]")
        console.print("[dim]Mann-Kendall test for monotonic trends (α=0.05)[/dim]\n")

        years = [yd["year"] for yd in yearly_data]
        concentrations = [yd["concentration"] for yd in yearly_data]
        unique_artists = [yd["unique_artists"] for yd in yearly_data]
        total_plays = [yd["total_plays"] for yd in yearly_data]

        # Test 1: Taste concentration trend
        tau_conc, p_conc = kendalltau(years, concentrations)
        if p_conc < 0.05:
            trend_conc = "narrowing" if tau_conc > 0 else "broadening"
            symbol_conc = "🔺" if tau_conc > 0 else "🔻"
            console.print(f"  {symbol_conc} [bold]Taste Concentration:[/bold] {trend_conc} (τ={tau_conc:.3f}, p={p_conc:.4f})")
            if tau_conc > 0:
                console.print(f"     [dim]Your listening is becoming more focused on fewer artists over time[/dim]")
            else:
                console.print(f"     [dim]Your listening is becoming more diverse over time[/dim]")
        else:
            console.print(f"  ➖ [bold]Taste Concentration:[/bold] stable (no significant trend, p={p_conc:.4f})")

        # Test 2: Artist diversity trend
        tau_artists, p_artists = kendalltau(years, unique_artists)
        if p_artists < 0.05:
            trend_artists = "increasing" if tau_artists > 0 else "decreasing"
            symbol_artists = "🔺" if tau_artists > 0 else "🔻"
            console.print(f"  {symbol_artists} [bold]Artist Discovery:[/bold] {trend_artists} (τ={tau_artists:.3f}, p={p_artists:.4f})")
            if tau_artists > 0:
                console.print(f"     [dim]You're discovering more unique artists each year[/dim]")
            else:
                console.print(f"     [dim]You're discovering fewer unique artists each year[/dim]")
        else:
            console.print(f"  ➖ [bold]Artist Discovery:[/bold] stable (no significant trend, p={p_artists:.4f})")

        # Test 3: Listening volume trend
        tau_plays, p_plays = kendalltau(years, total_plays)
        if p_plays < 0.05:
            trend_plays = "increasing" if tau_plays > 0 else "decreasing"
            symbol_plays = "🔺" if tau_plays > 0 else "🔻"
            console.print(f"  {symbol_plays} [bold]Listening Volume:[/bold] {trend_plays} (τ={tau_plays:.3f}, p={p_plays:.4f})")
            if tau_plays > 0:
                console.print(f"     [dim]Your total listening is increasing over time[/dim]")
            else:
                console.print(f"     [dim]Your total listening is decreasing over time[/dim]")
        else:
            console.print(f"  ➖ [bold]Listening Volume:[/bold] stable (no significant trend, p={p_plays:.4f})")

        console.print()

    # Detect "eras" using hierarchical clustering
    console.print(f"\n[bold cyan]🎭 MUSICAL ERAS (Clustering Analysis)[/bold cyan]")
    console.print("[dim]Detecting periods of similar listening patterns[/dim]\n")

    if len(yearly_data) >= 3:
        from sklearn.cluster import AgglomerativeClustering
        from sklearn.metrics import silhouette_score
        import numpy as np

        # Build year × artist feature matrix
        # Get all artists that appeared in top 20 of any year
        all_artists = set()
        for yd in yearly_data:
            year_df = df[df["year"] == yd["year"]]
            top_artists = year_df.groupby("artist").size().nlargest(20).index.tolist()
            all_artists.update(top_artists)

        all_artists = sorted(all_artists)
        artist_to_idx = {artist: idx for idx, artist in enumerate(all_artists)}

        # Build feature matrix: years × artists (normalized play counts)
        feature_matrix = np.zeros((len(yearly_data), len(all_artists)))

        for year_idx, yd in enumerate(yearly_data):
            year_df = df[df["year"] == yd["year"]]
            artist_plays = year_df.groupby("artist").size()

            for artist, plays in artist_plays.items():
                if artist in artist_to_idx:
                    artist_idx = artist_to_idx[artist]
                    feature_matrix[year_idx, artist_idx] = plays

        # Normalize each row (year) to unit norm
        from sklearn.preprocessing import normalize
        feature_matrix = normalize(feature_matrix, norm='l2', axis=1)

        # Find optimal number of clusters using silhouette analysis
        # Try 2 to min(10, n_years-1) clusters
        max_clusters = min(10, len(yearly_data) - 1)
        best_n_clusters = 2
        best_silhouette = -1

        if len(yearly_data) >= 4:  # Need at least 4 years for meaningful clustering
            silhouette_scores = []
            for n_clusters in range(2, max_clusters + 1):
                clusterer = AgglomerativeClustering(n_clusters=n_clusters, linkage='ward')
                labels = clusterer.fit_predict(feature_matrix)
                silhouette_avg = silhouette_score(feature_matrix, labels)
                silhouette_scores.append((n_clusters, silhouette_avg))

                if silhouette_avg > best_silhouette:
                    best_silhouette = silhouette_avg
                    best_n_clusters = n_clusters

            console.print(f"[dim]Silhouette analysis: optimal clusters = {best_n_clusters} (score: {best_silhouette:.3f})[/dim]\n")

        # Apply clustering with optimal number of clusters
        clusterer = AgglomerativeClustering(n_clusters=best_n_clusters, linkage='ward')
        cluster_labels = clusterer.fit_predict(feature_matrix)

        # Group years by cluster
        clusters = {}
        for year_idx, cluster_id in enumerate(cluster_labels):
            if cluster_id not in clusters:
                clusters[cluster_id] = []
            clusters[cluster_id].append(yearly_data[year_idx])

        # Sort clusters by first year
        sorted_clusters = sorted(clusters.items(), key=lambda x: min(yd["year"] for yd in x[1]))

        # Display eras
        for era_num, (cluster_id, cluster_years) in enumerate(sorted_clusters, 1):
            years = [yd["year"] for yd in cluster_years]
            years.sort()

            # Find defining artists (artists with highest average plays across this era)
            era_artist_plays = {}
            for yd in cluster_years:
                year_df = df[df["year"] == yd["year"]]
                artist_plays = year_df.groupby("artist").size()
                for artist, plays in artist_plays.items():
                    if artist not in era_artist_plays:
                        era_artist_plays[artist] = []
                    era_artist_plays[artist].append(plays)

            # Calculate average plays and take top 5
            artist_avg_plays = {
                artist: np.mean(plays)
                for artist, plays in era_artist_plays.items()
            }
            top_artists = sorted(artist_avg_plays.items(), key=lambda x: -x[1])[:5]
            defining = ", ".join(a[0] for a in top_artists[:4])

            # Format year range
            if len(years) == 1:
                span = str(years[0])
            elif years == list(range(years[0], years[-1] + 1)):
                # Contiguous years
                span = f"{years[0]}-{years[-1]}"
            else:
                # Non-contiguous years
                span = ", ".join(str(y) for y in years)

            console.print(f"  [bold]Era {era_num}: {span}[/bold]")
            console.print(f"  [dim]Defined by: {defining}[/dim]")
            console.print(f"  [dim]Years: {len(years)} | Total plays: {sum(yd['total_plays'] for yd in cluster_years):,}[/dim]\n")
    else:
        console.print(f"  [dim]Need at least 3 years of data for clustering analysis[/dim]\n")

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


@app.command(name="funnel")
def history_funnel(
    ctx: typer.Context,
):
    """Show artist discovery funnel - from first play to superfan.

    Tracks conversion rates across stages:
    - Discovery: First play
    - Curiosity: 5+ plays
    - Fan: 50+ plays
    - Superfan: 200+ plays
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025

    df_full = data.load_scrobbles(get_csv_path(csv))
    df = data.filter_by_year(df_full, year)

    console.print(f"\n[bold magenta]═══ DISCOVERY FUNNEL ({year}) ═══[/bold magenta]\n")

    # Get all artists and their play counts for the year
    artist_plays = df.groupby("artist").size().reset_index(name="plays")

    # Define stages
    discovered = len(artist_plays)  # All artists with 1+ plays
    curiosity = len(artist_plays[artist_plays["plays"] >= 5])  # 5+ plays
    fans = len(artist_plays[artist_plays["plays"] >= 50])  # 50+ plays
    superfans = len(artist_plays[artist_plays["plays"] >= 200])  # 200+ plays

    # Calculate conversion rates
    disc_to_curiosity = (curiosity / discovered * 100) if discovered > 0 else 0
    curiosity_to_fan = (fans / curiosity * 100) if curiosity > 0 else 0
    fan_to_superfan = (superfans / fans * 100) if fans > 0 else 0
    overall_conversion = (superfans / discovered * 100) if discovered > 0 else 0

    console.print("[bold cyan]Conversion Funnel:[/bold cyan]\n")

    # Stage 1
    bar1_width = 50
    bar1 = "█" * bar1_width
    console.print(f"  [bold]Stage 1: Discovery (first play)[/bold]")
    console.print(f"  [green]{bar1}[/green] {discovered:,} artists")
    console.print(f"  [dim]100% of artists you tried this year[/dim]\n")

    # Stage 2
    bar2_width = int(bar1_width * (curiosity / discovered)) if discovered > 0 else 0
    bar2 = "█" * bar2_width
    console.print(f"  [bold]Stage 2: Curiosity (5+ plays)[/bold]")
    console.print(f"  [yellow]{bar2}[/yellow] {curiosity:,} artists")
    console.print(f"  [dim]{disc_to_curiosity:.1f}% conversion from discovery[/dim]\n")

    # Stage 3
    bar3_width = int(bar1_width * (fans / discovered)) if discovered > 0 else 0
    bar3 = "█" * bar3_width
    console.print(f"  [bold]Stage 3: Fan (50+ plays)[/bold]")
    console.print(f"  [cyan]{bar3}[/cyan] {fans:,} artists")
    console.print(f"  [dim]{curiosity_to_fan:.1f}% conversion from curiosity[/dim]\n")

    # Stage 4
    bar4_width = int(bar1_width * (superfans / discovered)) if discovered > 0 else 0
    bar4 = "█" * bar4_width
    console.print(f"  [bold]Stage 4: Superfan (200+ plays)[/bold]")
    console.print(f"  [magenta]{bar4}[/magenta] {superfans:,} artists")
    console.print(f"  [dim]{fan_to_superfan:.1f}% conversion from fan[/dim]\n")

    # Summary
    console.print(f"[bold cyan]Summary:[/bold cyan]")
    console.print(f"  Overall Conversion: {overall_conversion:.2f}% (discovery → superfan)")
    console.print(f"  Discovery Efficiency: You discovered {discovered:,} artists but only {superfans:,} became superfans\n")

    # Show examples from each stage
    console.print(f"[bold cyan]Examples:[/bold cyan]\n")

    # Superfans
    superfan_artists = artist_plays[artist_plays["plays"] >= 200].nlargest(5, "plays")
    if not superfan_artists.empty:
        console.print(f"  [bold magenta]Superfans (200+ plays):[/bold magenta]")
        for _, row in superfan_artists.iterrows():
            console.print(f"    • {row['artist']} ({row['plays']} plays)")
        console.print()

    # Fans (50-199 plays)
    fan_artists = artist_plays[
        (artist_plays["plays"] >= 50) & (artist_plays["plays"] < 200)
    ].nlargest(5, "plays")
    if not fan_artists.empty:
        console.print(f"  [bold cyan]Fans (50-199 plays):[/bold cyan]")
        for _, row in fan_artists.iterrows():
            console.print(f"    • {row['artist']} ({row['plays']} plays)")
        console.print()

    # Curiosity (5-49 plays)
    curiosity_artists = artist_plays[
        (artist_plays["plays"] >= 5) & (artist_plays["plays"] < 50)
    ].nlargest(5, "plays")
    if not curiosity_artists.empty:
        console.print(f"  [bold yellow]Curiosity (5-49 plays):[/bold yellow]")
        for _, row in curiosity_artists.iterrows():
            console.print(f"    • {row['artist']} ({row['plays']} plays)")
        console.print()

    # One-hit wonders (1-4 plays)
    oneshot_count = len(artist_plays[artist_plays["plays"] < 5])
    console.print(f"  [bold red]One-hit wonders (1-4 plays):[/bold red]")
    console.print(f"    {oneshot_count:,} artists didn't make it past curiosity\n")
