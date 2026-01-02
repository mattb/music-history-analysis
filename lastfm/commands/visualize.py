"""Visualize commands - generate visual representations of listening data."""

import typer
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
import webbrowser
import pandas as pd
import numpy as np
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from .. import data

app = typer.Typer(help="Visualize your listening patterns")
console = Console()


def get_csv_path(csv: Optional[Path] = None) -> Path:
    """Get CSV path from argument, glob, or error."""
    if csv and csv.exists():
        return csv

    # Auto-detect from glob
    csvs = list(Path.cwd().glob("recenttracks-*.csv"))
    if csvs:
        return sorted(csvs)[-1]  # Most recent

    console.print(
        "[red]No CSV found. Provide --csv or place recenttracks-*.csv in current dir[/red]"
    )
    raise typer.Exit(1)


@app.command(name="calendar")
def visualize_calendar(
    ctx: typer.Context,
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output HTML file path"
    ),
):
    """Generate a GitHub-style calendar heatmap of listening activity.

    Shows daily listening intensity over time with color-coded cells.
    Each cell represents one day, colored by play count.
    """
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None

    df = data.load_scrobbles(get_csv_path(csv))

    # Filter to year if specified
    if year:
        df = data.filter_by_year(df, year)
        year_str = str(year)
    else:
        year = df["year"].max()  # Use most recent year for default
        df = data.filter_by_year(df, year)
        year_str = str(year)

    console.print(
        f"\n[bold cyan]Generating calendar heatmap for {year_str}...[/bold cyan]\n"
    )

    # Calculate daily stats
    df["date"] = df["timestamp"].dt.date
    daily_stats = (
        df.groupby("date")
        .agg(
            {
                "track": "count",
                "artist": lambda x: ", ".join(x.value_counts().head(3).index.tolist()),
            }
        )
        .rename(columns={"track": "plays", "artist": "top_artists"})
    )

    # Generate HTML
    html = generate_calendar_html(daily_stats, year)

    # Determine output path
    if output:
        output_path = output
    else:
        output_path = Path(f"calendar-{year_str}.html")

    # Write HTML
    output_path.write_text(html)
    console.print(f"[green]✓[/green] Calendar heatmap saved to: {output_path}")

    # Open in browser
    absolute_path = output_path.resolve()
    webbrowser.open(f"file://{absolute_path}")
    console.print(f"[dim]Opening in browser...[/dim]\n")


def generate_calendar_html(daily_stats: pd.DataFrame, year: int) -> str:
    """Generate HTML for calendar heatmap."""
    import datetime as dt

    # Get year range
    start_date = dt.date(year, 1, 1)
    end_date = dt.date(year, 12, 31)

    # Find first Monday before or on Jan 1
    days_since_monday = start_date.weekday()
    calendar_start = start_date - dt.timedelta(days=days_since_monday)

    # Find last Sunday after or on Dec 31
    days_until_sunday = 6 - end_date.weekday()
    calendar_end = end_date + dt.timedelta(days=days_until_sunday)

    # Calculate color scale based on percentiles
    import numpy as np

    play_counts = daily_stats["plays"].values
    if len(play_counts) > 0:
        percentiles = [0, 20, 40, 60, 80, 100]
        thresholds = (
            [play_counts.min()]
            + [np.percentile(play_counts, p) for p in percentiles[1:-1]]
            + [play_counts.max()]
        )
    else:
        thresholds = [0, 1, 5, 10, 20, 50]

    # Build calendar grid
    current_date = calendar_start
    weeks = []
    current_week = []

    while current_date <= calendar_end:
        date_str = current_date.strftime("%Y-%m-%d")

        # Get stats for this date
        if current_date in daily_stats.index:
            plays = int(daily_stats.loc[current_date, "plays"])
            top_artists = daily_stats.loc[current_date, "top_artists"]

            # Determine color level (0-5)
            level = 0
            for i, threshold in enumerate(thresholds):
                if plays >= threshold:
                    level = i

            in_year = start_date <= current_date <= end_date
        else:
            plays = 0
            top_artists = ""
            level = 0
            in_year = start_date <= current_date <= end_date

        current_week.append(
            {
                "date": current_date.strftime("%b %d, %Y"),
                "date_key": date_str,
                "plays": plays,
                "top_artists": top_artists,
                "level": level,
                "in_year": in_year,
            }
        )

        if current_date.weekday() == 6:  # Sunday
            weeks.append(current_week)
            current_week = []

        current_date += dt.timedelta(days=1)

    if current_week:
        weeks.append(current_week)

    # Calculate total stats
    total_plays = int(daily_stats["plays"].sum())
    active_days = len(daily_stats)
    avg_plays = total_plays / active_days if active_days > 0 else 0
    max_plays = int(daily_stats["plays"].max()) if len(daily_stats) > 0 else 0

    # Generate HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Listening Calendar {year}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #e0e0e0;
            padding: 40px 20px;
            min-height: 100vh;
        }}

        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        h1 {{
            font-size: 2.5rem;
            margin-bottom: 10px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }}

        .subtitle {{
            color: #888;
            margin-bottom: 30px;
            font-size: 1.1rem;
        }}

        .stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 40px;
        }}

        .stat-card {{
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 12px;
            padding: 20px;
        }}

        .stat-value {{
            font-size: 2rem;
            font-weight: bold;
            color: #667eea;
            margin-bottom: 5px;
        }}

        .stat-label {{
            color: #888;
            font-size: 0.9rem;
        }}

        .calendar {{
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 30px;
            overflow-x: auto;
        }}

        .calendar-grid {{
            display: inline-grid;
            grid-template-rows: repeat(7, 15px);
            grid-auto-flow: column;
            gap: 4px;
            min-width: 100%;
        }}

        .day {{
            width: 15px;
            height: 15px;
            border-radius: 3px;
            cursor: pointer;
            transition: transform 0.2s, opacity 0.2s;
            position: relative;
        }}

        .day:hover {{
            transform: scale(1.3);
            z-index: 10;
        }}

        .day.level-0 {{ background: rgba(255, 255, 255, 0.05); }}
        .day.level-1 {{ background: #0e4429; }}
        .day.level-2 {{ background: #006d32; }}
        .day.level-3 {{ background: #26a641; }}
        .day.level-4 {{ background: #39d353; }}
        .day.level-5 {{ background: #39d353; }}

        .day.out-of-year {{
            opacity: 0.3;
        }}

        .tooltip {{
            position: fixed;
            background: rgba(0, 0, 0, 0.95);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 8px;
            padding: 12px 16px;
            pointer-events: none;
            z-index: 1000;
            display: none;
            max-width: 300px;
        }}

        .tooltip-date {{
            font-weight: bold;
            margin-bottom: 8px;
            color: #667eea;
        }}

        .tooltip-plays {{
            margin-bottom: 6px;
        }}

        .tooltip-artists {{
            font-size: 0.85rem;
            color: #aaa;
            margin-top: 6px;
            padding-top: 6px;
            border-top: 1px solid rgba(255, 255, 255, 0.1);
        }}

        .legend {{
            margin-top: 20px;
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 0.85rem;
            color: #888;
        }}

        .legend-scale {{
            display: flex;
            gap: 3px;
            margin-left: 10px;
        }}

        .legend-box {{
            width: 15px;
            height: 15px;
            border-radius: 3px;
        }}

        @media (max-width: 768px) {{
            h1 {{ font-size: 1.8rem; }}
            .calendar {{ padding: 20px; }}
            .stats {{ grid-template-columns: 1fr 1fr; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎵 Listening Calendar {year}</h1>
        <p class="subtitle">Your year in music, one day at a time</p>

        <div class="stats">
            <div class="stat-card">
                <div class="stat-value">{total_plays:,}</div>
                <div class="stat-label">Total Plays</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{active_days}</div>
                <div class="stat-label">Active Days</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{avg_plays:.0f}</div>
                <div class="stat-label">Avg Plays/Day</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{max_plays}</div>
                <div class="stat-label">Peak Day</div>
            </div>
        </div>

        <div class="calendar">
            <div class="calendar-grid">
"""

    # Add day cells
    for week in weeks:
        for day in week:
            in_year_class = "" if day["in_year"] else "out-of-year"
            html += f"""                <div class="day level-{day['level']} {in_year_class}"
                     data-date="{day['date']}"
                     data-plays="{day['plays']}"
                     data-artists="{day['top_artists']}"
                     onmouseover="showTooltip(event, this)"
                     onmousemove="moveTooltip(event)"
                     onmouseout="hideTooltip()"></div>
"""

    html += """            </div>

            <div class="legend">
                <span>Less</span>
                <div class="legend-scale">
                    <div class="legend-box level-0"></div>
                    <div class="legend-box level-1"></div>
                    <div class="legend-box level-2"></div>
                    <div class="legend-box level-3"></div>
                    <div class="legend-box level-4"></div>
                </div>
                <span>More</span>
            </div>
        </div>
    </div>

    <div class="tooltip" id="tooltip">
        <div class="tooltip-date" id="tooltip-date"></div>
        <div class="tooltip-plays" id="tooltip-plays"></div>
        <div class="tooltip-artists" id="tooltip-artists"></div>
    </div>

    <script>
        function showTooltip(event, element) {
            const tooltip = document.getElementById('tooltip');
            const date = element.dataset.date;
            const plays = element.dataset.plays;
            const artists = element.dataset.artists;

            document.getElementById('tooltip-date').textContent = date;
            document.getElementById('tooltip-plays').textContent =
                plays == 0 ? 'No plays' : `${plays} plays`;

            if (plays > 0 && artists) {
                document.getElementById('tooltip-artists').textContent =
                    'Top: ' + artists;
                document.getElementById('tooltip-artists').style.display = 'block';
            } else {
                document.getElementById('tooltip-artists').style.display = 'none';
            }

            tooltip.style.display = 'block';
            moveTooltip(event);
        }

        function moveTooltip(event) {
            const tooltip = document.getElementById('tooltip');
            tooltip.style.left = (event.pageX + 15) + 'px';
            tooltip.style.top = (event.pageY + 15) + 'px';
        }

        function hideTooltip() {
            document.getElementById('tooltip').style.display = 'none';
        }
    </script>
</body>
</html>
"""

    return html


@app.command(name="genome")
def visualize_genome(
    ctx: typer.Context,
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output HTML file path"
    ),
    min_plays: int = typer.Option(
        10, "--min-plays", help="Minimum plays for an artist to be included"
    ),
    rebuild: bool = typer.Option(False, "--rebuild", help="Force rebuild embeddings"),
):
    """Generate a Musical Genome visualization - your entire musical universe in 2D.

    Creates an interactive scatter plot where each point is an artist you've listened to.
    Artists with similar listening patterns are positioned close together.
    """
    from .. import embeddings, musicbrainz_db

    # Get global options
    csv = ctx.obj.get("csv") if ctx.obj else None
    csv_path = get_csv_path(csv)

    console.print(
        f"\n[bold cyan]Generating Musical Genome visualization...[/bold cyan]\n"
    )

    # Step 1: Build or load embeddings
    console.print("[dim]Step 1/4: Loading artist embeddings...[/dim]")

    # If min_plays differs from default, rebuild embeddings with filtered data
    # This ensures SVD captures patterns specific to the filtered dataset
    force_rebuild_for_filter = min_plays != 10 and not rebuild

    if force_rebuild_for_filter:
        console.print(
            f"[dim]   Building fresh embeddings for artists with {min_plays}+ plays...[/dim]"
        )

    try:
        artist_embeddings = embeddings.build_embeddings_from_csv(
            csv_path,
            n_components=50,
            time_window="W",
            min_plays=min_plays,  # Use the requested threshold
            method="cooccurrence",  # Use artist-to-artist co-occurrence
            force_rebuild=rebuild or force_rebuild_for_filter,
        )
    except Exception as e:
        console.print(f"[red]Error building embeddings: {e}[/red]")
        raise typer.Exit(1)

    # Load scrobbles data for metadata
    df = data.load_scrobbles(csv_path)
    artist_play_counts = df.groupby("artist").size()

    # Check if we have enough artists for UMAP
    n_artists = len(artist_embeddings.idx_to_artist)
    if n_artists < 20:
        console.print(
            f"[yellow]⚠️  Only {n_artists} artists found - too few for visualization[/yellow]"
        )
        console.print(f"[yellow]   Try using a lower --min-plays threshold[/yellow]")
        raise typer.Exit(1)

    # Step 2: Reduce to 2D using UMAP
    console.print("[dim]Step 2/4: Reducing to 2D with UMAP...[/dim]")
    from umap import UMAP

    # Scale UMAP parameters based on dataset size
    # n_neighbors: 10-15% of dataset size (min 15, max 200)
    n_neighbors = min(max(int(n_artists * 0.12), 15), 200)
    # min_dist: larger for smaller datasets to spread points out
    min_dist = 0.3 if n_artists < 500 else 0.1

    console.print(
        f"[dim]   UMAP params: n_neighbors={n_neighbors}, min_dist={min_dist}[/dim]"
    )

    reducer = UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=min_dist,
        metric="cosine",
        random_state=42,
        verbose=False,
    )

    embedding_2d = reducer.fit_transform(artist_embeddings.embeddings)

    # Step 3: Get play counts and genres
    console.print("[dim]Step 3/4: Gathering artist metadata...[/dim]")
    # artist_play_counts already loaded above

    artists = []
    play_counts = []

    for idx in range(len(artist_embeddings.idx_to_artist)):
        artist = artist_embeddings.idx_to_artist[idx]
        artists.append(artist)
        play_counts.append(int(artist_play_counts.get(artist, 0)))

    # Step 4: Cluster artists and create interactive visualization
    console.print("[dim]Step 4/5: Clustering artists...[/dim]")

    import hdbscan

    # Use HDBSCAN to cluster artists based on 2D coordinates
    # min_cluster_size: aim for ~10-20 clusters
    min_cluster_size = max(10, n_artists // 25)
    min_samples = max(3, n_artists // 100)

    console.print(
        f"[dim]   Using HDBSCAN: min_cluster_size={min_cluster_size}, min_samples={min_samples}[/dim]"
    )

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size, min_samples=min_samples, metric="euclidean"
    )
    cluster_ids = clusterer.fit_predict(embedding_2d)

    # Create cluster label strings based on dominant year spans
    n_clusters = len(set(cluster_ids)) - (1 if -1 in cluster_ids else 0)
    n_noise = list(cluster_ids).count(-1)

    console.print(f"[dim]   Found {n_clusters} clusters, {n_noise} noise points[/dim]")

    # Calculate dominant year span for each cluster
    # Group scrobbles by artist to find peak listening years
    artist_peak_years = {}
    for artist in artists:
        artist_scrobbles = df[df["artist"] == artist]
        if len(artist_scrobbles) > 0:
            # Find the year with most plays for this artist
            year_counts = artist_scrobbles.groupby("year").size()
            # Get years that account for 80% of plays (core listening period)
            sorted_years = year_counts.sort_values(ascending=False)
            total = sorted_years.sum()
            cumsum = 0
            core_years = []
            for year, count in sorted_years.items():
                cumsum += count
                core_years.append(year)
                if cumsum >= total * 0.7:  # 70% threshold
                    break
            artist_peak_years[artist] = (min(core_years), max(core_years))
        else:
            artist_peak_years[artist] = (2020, 2020)  # fallback

    # Calculate cluster year spans
    cluster_year_labels = {}
    for cluster_id in set(cluster_ids):
        if cluster_id == -1:
            continue  # Skip noise
        # Get artists in this cluster
        cluster_artists = [
            artists[i] for i in range(len(artists)) if cluster_ids[i] == cluster_id
        ]
        # Get their peak years
        all_min_years = [
            artist_peak_years[a][0] for a in cluster_artists if a in artist_peak_years
        ]
        all_max_years = [
            artist_peak_years[a][1] for a in cluster_artists if a in artist_peak_years
        ]
        if all_min_years and all_max_years:
            # Use median of min/max years for more robust estimate
            median_start = int(np.median(all_min_years))
            median_end = int(np.median(all_max_years))
            if median_start == median_end:
                cluster_year_labels[cluster_id] = f"{median_start}"
            else:
                cluster_year_labels[cluster_id] = f"{median_start}-{median_end}"
        else:
            cluster_year_labels[cluster_id] = f"Group {cluster_id + 1}"

    # Build cluster labels array
    cluster_labels = []
    for cluster_id in cluster_ids:
        if cluster_id == -1:
            cluster_labels.append("")  # Empty label for unclustered (less distracting)
        else:
            cluster_labels.append(
                cluster_year_labels.get(cluster_id, f"Group {cluster_id + 1}")
            )

    # Step 5: Create interactive visualization
    console.print("[dim]Step 5/5: Creating interactive visualization...[/dim]")

    import datamapplot
    import pandas as pd

    # Create marker size array with strong variance
    # Use power of 0.3 for aggressive visual differentiation (bigger get much bigger)
    play_counts_array = np.array(play_counts, dtype=np.float32)
    # Normalize to 0-1 range then apply power transform
    max_plays = play_counts_array.max()
    min_plays = play_counts_array.min()
    normalized = (play_counts_array - min_plays) / (max_plays - min_plays + 1)
    # Power of 0.3 gives more spread than sqrt (0.5)
    marker_size_array = np.power(normalized, 0.3) * 15 + 0.5  # Range roughly 0.5-15.5

    # Create a second label layer for top artists (individual point labels)
    play_threshold = np.percentile(play_counts_array, 0)
    artist_labels = []
    for artist, plays in zip(artists, play_counts):
        if plays >= play_threshold:
            artist_labels.append(artist)
        else:
            artist_labels.append("")  # Empty label for smaller artists

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("[cyan]Generating interactive plot...", total=None)

        # Create hover text with play counts
        hover_text_with_plays = [
            f"{artist} ({plays:,} plays)" for artist, plays in zip(artists, play_counts)
        ]

        # Create extra data for richer tooltips
        extra_data = pd.DataFrame(
            {
                "artist": artists,
                "plays": play_counts,
                "cluster": cluster_labels,
            }
        )

        # Create interactive plot with cluster labels AND artist labels for top artists
        interactive_fig = datamapplot.create_interactive_plot(
            embedding_2d,
            np.array(cluster_labels),  # First layer: cluster membership
            np.array(
                artist_labels
            ),  # Second layer: individual artist names (top artists only)
            hover_text=np.array(hover_text_with_plays),  # Artist names with play counts
            title="Your Musical Genome",
            sub_title=f"{n_artists} artists clustered by listening patterns ({n_clusters} groups)",
            darkmode=True,
            marker_size_array=marker_size_array,  # Variable sizing by play count
            point_radius_min_pixels=2,
            point_radius_max_pixels=50,
            point_hover_color="lightblue",
            enable_search=True,  # Allow searching for artists
            extra_point_data=extra_data,
            noise_label="",  # Don't label unclustered points
        )

        progress.update(task, completed=True)

    # Determine output path
    if output:
        output_path = output
    else:
        output_path = Path("musical-genome.html")

    # Save interactive HTML directly from datamapplot
    interactive_fig.save(str(output_path))

    console.print(f"\n[green]✓[/green] Musical Genome saved to: {output_path}")

    # Open in browser
    absolute_path = output_path.resolve()
    webbrowser.open(f"file://{absolute_path}")
    console.print(f"[dim]Opening in browser...[/dim]\n")
