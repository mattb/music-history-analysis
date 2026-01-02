"""Evaluation commands - validate embedding and recommendation quality."""

import typer
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table

from .. import data, evaluation

app = typer.Typer(help="Evaluate embedding and recommendation quality")
console = Console()


def get_csv_path(csv: Optional[Path] = None) -> Path:
    """Get CSV path from argument, glob, or error."""
    if csv and csv.exists():
        return csv

    csvs = list(Path.cwd().glob("recenttracks-*.csv"))
    if csvs:
        return sorted(csvs)[-1]

    console.print("[red]No CSV found. Provide --csv or place recenttracks-*.csv in current dir[/red]")
    raise typer.Exit(1)


@app.command(name="holdout")
def eval_holdout(
    ctx: typer.Context,
    train_end: int = typer.Option(2022, "--train-end", "-t", help="Last year of training data"),
    test_start: int = typer.Option(2023, "--test-start", help="First year of test period"),
    test_end: int = typer.Option(2024, "--test-end", help="Last year of test period"),
    top_n: int = typer.Option(20, "--top-n", "-n", help="Neighbors per seed artist"),
    min_plays: int = typer.Option(10, "--min-plays", help="Min plays to be a seed artist"),
):
    """Test if embeddings predict future discoveries.

    Compares USER embeddings (your co-listening patterns) vs CRITICS embeddings
    (critical consensus) to see which better predicts what you'll discover.
    """
    csv = ctx.obj.get("csv") if ctx.obj else None
    csv_path = get_csv_path(csv)

    console.print("\n[bold magenta]═══ HOLDOUT EVALUATION ═══[/bold magenta]")
    console.print(f"[dim]Do embeddings predict future discoveries?[/dim]\n")

    result = evaluation.run_holdout_evaluation(
        csv_path=csv_path,
        train_end_year=train_end,
        test_start_year=test_start,
        test_end_year=test_end,
        top_n_neighbors=top_n,
        min_plays_train=min_plays,
    )

    # Display results
    console.print(f"\n[bold cyan]Results[/bold cyan]")
    console.print(f"  Train: {result.train_years[0]}-{result.train_years[1]}")
    console.print(f"  Test: {result.test_years[0]}-{result.test_years[1]}")
    console.print(f"  Discoveries: {result.total_discoveries}")
    console.print()

    table = Table(show_header=True)
    table.add_column("Embedding Type", style="cyan")
    table.add_column("Predicted", justify="right", style="green")
    table.add_column("Rate", justify="right")
    table.add_column("Baseline", justify="right", style="dim")
    table.add_column("Lift", justify="right", style="yellow")

    table.add_row(
        "User (co-listening)",
        str(result.user_predicted),
        f"{result.user_rate:.1%}",
        str(result.user_baseline),
        f"{result.user_lift:.1f}x",
    )
    table.add_row(
        "Critics (consensus)",
        str(result.critics_predicted),
        f"{result.critics_rate:.1%}",
        str(result.critics_baseline),
        f"{result.critics_lift:.1f}x",
    )

    console.print(table)

    # Winner
    if result.user_lift > result.critics_lift:
        console.print(f"\n[green]→ User embeddings win ({result.user_lift:.1f}x vs {result.critics_lift:.1f}x)[/green]")
    elif result.critics_lift > result.user_lift:
        console.print(f"\n[green]→ Critics embeddings win ({result.critics_lift:.1f}x vs {result.user_lift:.1f}x)[/green]")
    else:
        console.print(f"\n[dim]→ Both equally predictive[/dim]")

    # Show unique predictions
    if result.user_only:
        console.print(f"\n[bold]Predicted by User only:[/bold]")
        for artist in result.user_only[:5]:
            console.print(f"  {artist}")

    if result.critics_only:
        console.print(f"\n[bold]Predicted by Critics only:[/bold]")
        for artist in result.critics_only[:5]:
            console.print(f"  {artist}")

    if result.both:
        console.print(f"\n[bold]Predicted by Both:[/bold]")
        for artist in result.both[:5]:
            console.print(f"  {artist}")


@app.command(name="followthrough")
def eval_followthrough(
    ctx: typer.Context,
    ref_year: int = typer.Option(2020, "--ref-year", "-r", help="Year of critic recommendations"),
    followup_start: int = typer.Option(2021, "--followup-start", help="Start of followup period"),
    followup_end: int = typer.Option(2024, "--followup-end", help="End of followup period"),
    top_n: int = typer.Option(30, "--top-n", "-n", help="Top albums to show"),
    familiarity: float = typer.Option(None, "--familiarity", "-f",
        help="Use continuous familiarity scoring (0-1) instead of 5x5 rule."),
):
    """Test if critic recommendations became favorites.

    Looks at albums critics recommended in ref-year that you hadn't heard,
    then checks how many you played in subsequent years.

    Compares critic-count ranking vs vector-weighted ranking to see
    which better predicts what you'll actually enjoy.
    """
    csv = ctx.obj.get("csv") if ctx.obj else None
    csv_path = get_csv_path(csv)

    console.print("\n[bold magenta]═══ CRITIC FOLLOW-THROUGH ═══[/bold magenta]")
    console.print(f"[dim]Did {ref_year} recommendations become favorites?[/dim]")
    if familiarity is not None:
        console.print(f"[dim]Using familiarity threshold: {familiarity}[/dim]")
    console.print()

    result = evaluation.run_critic_followthrough(
        csv_path=csv_path,
        reference_year=ref_year,
        followup_start=followup_start,
        followup_end=followup_end,
        top_n_albums=top_n,
        min_familiarity=familiarity,
    )

    # Summary stats
    console.print(f"\n[bold cyan]Conversion Summary[/bold cyan]")
    console.print(f"  Reference year: {result.reference_year}")
    console.print(f"  Follow-up: {result.followup_years[0]}-{result.followup_years[1]}")
    console.print()

    table = Table(show_header=True, box=None)
    table.add_column("Threshold", style="cyan")
    table.add_column("Count", justify="right", style="green")
    table.add_column("Rate", justify="right", style="yellow")

    table.add_row("Unheard recommendations", str(result.total_unheard), "")
    table.add_row("Played at all (1+)", str(result.played_any), f"{result.any_rate:.1%}")
    table.add_row("Played 5+ times", str(result.played_5plus), f"{result.played_5plus / result.total_unheard:.1%}" if result.total_unheard > 0 else "")
    table.add_row("Played 10+ times", str(result.played_10plus), f"{result.love_rate:.1%}")
    table.add_row("Played 50+ times", str(result.played_50plus), f"{result.played_50plus / result.total_unheard:.1%}" if result.total_unheard > 0 else "")

    console.print(table)

    # NDCG@K comparison
    if result.ndcg_by_count:
        console.print(f"\n[bold cyan]Ranking Quality (NDCG@K)[/bold cyan]")
        console.print(f"[dim]Higher = better at surfacing albums you'll actually play[/dim]\n")

        ndcg_table = Table(show_header=True, box=None)
        ndcg_table.add_column("K", justify="right", style="dim")
        ndcg_table.add_column("Count", justify="right", style="cyan")
        ndcg_table.add_column("Vector", justify="right", style="green")
        ndcg_table.add_column("Winner", style="yellow")

        for k in sorted(result.ndcg_by_count.keys()):
            count_ndcg = result.ndcg_by_count.get(k, 0)
            vector_ndcg = result.ndcg_by_vector.get(k, 0) if result.ndcg_by_vector else 0
            if vector_ndcg > count_ndcg:
                winner = f"+{(vector_ndcg - count_ndcg):.3f} Vector"
            elif count_ndcg > vector_ndcg:
                winner = f"+{(count_ndcg - vector_ndcg):.3f} Count"
            else:
                winner = "Tie"
            ndcg_table.add_row(str(k), f"{count_ndcg:.3f}", f"{vector_ndcg:.3f}", winner)

        console.print(ndcg_table)

    # Hits@K comparison
    if result.hits_by_count:
        console.print(f"\n[bold cyan]Hits@K (Albums meeting threshold in top K)[/bold cyan]")
        console.print(f"[dim]How many of top K recommendations did you actually play?[/dim]\n")

        hits_table = Table(show_header=True, box=None)
        hits_table.add_column("K", justify="right", style="dim")
        hits_table.add_column("1+ (Count)", justify="right")
        hits_table.add_column("1+ (Vec)", justify="right")
        hits_table.add_column("10+ (Count)", justify="right")
        hits_table.add_column("10+ (Vec)", justify="right")

        for k in sorted(result.hits_by_count.keys()):
            count_hits = result.hits_by_count.get(k, {})
            vector_hits = result.hits_by_vector.get(k, {}) if result.hits_by_vector else {}
            hits_table.add_row(
                str(k),
                str(count_hits.get(1, 0)),
                str(vector_hits.get(1, 0)),
                str(count_hits.get(10, 0)),
                str(vector_hits.get(10, 0)),
            )

        console.print(hits_table)

    # Coverage and novelty metrics
    if result.coverage_by_count:
        console.print(f"\n[bold cyan]Coverage & Novelty (at K=50)[/bold cyan]")
        console.print(f"[dim]Higher coverage = more diverse; lower popularity ratio = more novel[/dim]\n")

        cov_table = Table(show_header=True, box=None)
        cov_table.add_column("Method", style="cyan")
        cov_table.add_column("Unique Artists", justify="right")
        cov_table.add_column("Top10 Concentration", justify="right")
        cov_table.add_column("Popularity Ratio", justify="right")

        cov_table.add_row(
            "Count",
            str(result.coverage_by_count.get('unique_artists', 0)),
            f"{result.coverage_by_count.get('top10_pct', 0):.1%}",
            f"{result.novelty_by_count.get('popularity_ratio', 1):.2f}x",
        )
        if result.coverage_by_vector:
            cov_table.add_row(
                "Vector",
                str(result.coverage_by_vector.get('unique_artists', 0)),
                f"{result.coverage_by_vector.get('top10_pct', 0):.1%}",
                f"{result.novelty_by_vector.get('popularity_ratio', 1):.2f}x",
            )

        console.print(cov_table)

    # Show top albums by each ranking
    if result.top_by_count:
        console.print(f"\n[bold]Top 10 by Critic Count:[/bold]")
        for i, album in enumerate(result.top_by_count[:10], 1):
            plays = album.get('followup_plays', 0)
            status = "🔥" if plays >= 10 else "✓" if plays > 0 else "✗"
            console.print(f"  {i:2}. {album['artist'][:20]} - {album['album'][:25]} ({album['critics_count']} critics) → {plays} plays {status}")

    if result.top_by_vector:
        console.print(f"\n[bold]Top 10 by Vector Score:[/bold]")
        for i, album in enumerate(result.top_by_vector[:10], 1):
            plays = album.get('followup_plays', 0)
            score = album.get('vector_score', 0)
            status = "🔥" if plays >= 10 else "✓" if plays > 0 else "✗"
            console.print(f"  {i:2}. {album['artist'][:20]} - {album['album'][:25]} ({score:.1f} score) → {plays} plays {status}")


@app.command(name="baseline")
def eval_baseline(
    ctx: typer.Context,
    description: str = typer.Option("Current model", "--desc", "-d", help="Description of this baseline"),
    train_end: int = typer.Option(2022, "--train-end", help="Train period end for holdout"),
):
    """Run full evaluation suite and save as baseline.

    Runs both holdout and follow-through tests, then saves results
    for comparison with future model changes.
    """
    csv = ctx.obj.get("csv") if ctx.obj else None
    csv_path = get_csv_path(csv)

    console.print("\n[bold magenta]═══ RUNNING FULL EVALUATION BASELINE ═══[/bold magenta]\n")

    # Run holdout
    console.print("[bold]1. Holdout Evaluation[/bold]")
    holdout = evaluation.run_holdout_evaluation(
        csv_path=csv_path,
        train_end_year=train_end,
    )

    # Run follow-through
    console.print("\n[bold]2. Critic Follow-Through[/bold]")
    followthrough = evaluation.run_critic_followthrough(
        csv_path=csv_path,
        reference_year=2020,
    )

    # Create and save baseline
    baseline = evaluation.EvaluationBaseline(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        description=description,
        holdout=holdout,
        critic_followthrough=followthrough,
    )

    path = evaluation.save_baseline(baseline)

    console.print(f"\n[bold green]Baseline saved to:[/bold green] {path}")

    # Summary
    console.print(f"\n[bold cyan]Summary[/bold cyan]")
    console.print(f"  Holdout - User lift: {holdout.user_lift:.1f}x, Critics lift: {holdout.critics_lift:.1f}x")
    console.print(f"  Critic follow-through: {followthrough.love_rate:.1%} became favorites")


@app.command(name="compare")
def eval_compare(
    ctx: typer.Context,
):
    """Compare all saved baselines."""
    baselines = evaluation.load_baselines()

    if not baselines:
        console.print("[yellow]No baselines found. Run 'eval baseline' first.[/yellow]")
        raise typer.Exit(1)

    console.print("\n[bold magenta]═══ BASELINE COMPARISON ═══[/bold magenta]\n")

    table = Table(show_header=True)
    table.add_column("Timestamp", style="dim")
    table.add_column("Description", style="cyan")
    table.add_column("User Lift", justify="right", style="green")
    table.add_column("Critics Lift", justify="right", style="green")
    table.add_column("Love%", justify="right", style="yellow")

    for b in baselines:
        user_lift = b.get("holdout", {}).get("user_lift", 0)
        critics_lift = b.get("holdout", {}).get("critics_lift", 0)
        love_rate = b.get("critic_followthrough", {}).get("love_rate", 0)

        table.add_row(
            b.get("timestamp", "")[:16],
            b.get("description", ""),
            f"{user_lift:.1f}x" if user_lift else "-",
            f"{critics_lift:.1f}x" if critics_lift else "-",
            f"{love_rate:.1%}" if love_rate else "-",
        )

    console.print(table)
