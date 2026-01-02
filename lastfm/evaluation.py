"""Evaluation harness for validating embedding and recommendation quality."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from . import crossref, data, embeddings


@dataclass
class HoldoutResult:
    """Results from future holdout evaluation."""
    train_years: Tuple[int, int]  # (start, end) inclusive
    test_years: Tuple[int, int]   # (start, end) inclusive

    # Discovery prediction
    total_discoveries: int        # Artists first played in test period
    predicted_by_neighbors: int   # Discoveries in top-N neighbors of train artists
    random_baseline: int          # Expected by chance (same N from all artists)

    # Top-N settings
    top_n_neighbors: int

    # Detailed results
    predicted_artists: List[str] = field(default_factory=list)
    missed_artists: List[str] = field(default_factory=list)

    @property
    def prediction_rate(self) -> float:
        return self.predicted_by_neighbors / self.total_discoveries if self.total_discoveries > 0 else 0

    @property
    def baseline_rate(self) -> float:
        return self.random_baseline / self.total_discoveries if self.total_discoveries > 0 else 0

    @property
    def lift(self) -> float:
        """How much better than random."""
        return self.prediction_rate / self.baseline_rate if self.baseline_rate > 0 else 0


@dataclass
class CriticFollowThroughResult:
    """Results from critic follow-through evaluation."""
    reference_year: int           # Year of critic recommendations
    followup_years: Tuple[int, int]  # Years to check for plays

    total_unheard: int            # Albums recommended but unheard at reference time
    played_any: int               # Albums you played at all in followup
    played_5plus: int             # Albums you played 5+ times
    played_10plus: int            # Albums you played 10+ times
    played_50plus: int            # Albums you became a fan of

    # By weighting method
    top_by_count: List[dict] = field(default_factory=list)      # Top unheard by critic count
    top_by_vector: List[dict] = field(default_factory=list)     # Top unheard by vector score

    # Ranking metrics at various K
    ndcg_by_count: Dict[int, float] = field(default_factory=dict)   # K -> NDCG
    ndcg_by_vector: Dict[int, float] = field(default_factory=dict)  # K -> NDCG
    hits_by_count: Dict[int, Dict[int, int]] = field(default_factory=dict)   # K -> {threshold -> hits}
    hits_by_vector: Dict[int, Dict[int, int]] = field(default_factory=dict)  # K -> {threshold -> hits}

    # Coverage metrics
    coverage_by_count: Dict[str, float] = field(default_factory=dict)
    coverage_by_vector: Dict[str, float] = field(default_factory=dict)

    # Novelty metrics
    novelty_by_count: Dict[str, float] = field(default_factory=dict)
    novelty_by_vector: Dict[str, float] = field(default_factory=dict)

    @property
    def any_rate(self) -> float:
        return self.played_any / self.total_unheard if self.total_unheard > 0 else 0

    @property
    def love_rate(self) -> float:
        return self.played_10plus / self.total_unheard if self.total_unheard > 0 else 0


def compute_ndcg(ranked_items: List[dict], k: int, relevance_key: str = 'followup_plays') -> float:
    """Compute NDCG@K for a ranked list.

    Args:
        ranked_items: List of items in ranked order
        k: Cutoff
        relevance_key: Key to get relevance score from each item

    Returns:
        NDCG@K score (0-1)
    """
    if not ranked_items or k <= 0:
        return 0.0

    # Get relevances for top K
    relevances = [item.get(relevance_key, 0) for item in ranked_items[:k]]

    # DCG
    dcg = sum(rel / np.log2(i + 2) for i, rel in enumerate(relevances))

    # Ideal DCG (sort by relevance)
    ideal_relevances = sorted(relevances, reverse=True)
    idcg = sum(rel / np.log2(i + 2) for i, rel in enumerate(ideal_relevances))

    if idcg == 0:
        return 0.0

    return dcg / idcg


def compute_hits_at_k(ranked_items: List[dict], k: int, thresholds: List[int]) -> Dict[int, int]:
    """Compute hits@K at various play thresholds.

    Args:
        ranked_items: List of items in ranked order
        k: Cutoff
        thresholds: Play count thresholds to check

    Returns:
        Dict mapping threshold -> number of hits in top K
    """
    hits = {}
    top_k = ranked_items[:k]

    for threshold in thresholds:
        hits[threshold] = sum(1 for item in top_k if item.get('followup_plays', 0) >= threshold)

    return hits


def compute_coverage_metrics(
    ranked_items: List[dict],
    k: int,
    score_key: str = 'critics_count',
) -> Dict[str, float]:
    """Compute coverage and concentration metrics for a ranking.

    Args:
        ranked_items: All items in ranked order
        k: Cutoff for top-K analysis
        score_key: Key for the score used in ranking

    Returns:
        Dict with:
        - unique_artists: Number of unique artists in top K
        - unique_artists_all: Number of unique artists in all items
        - artist_coverage: Fraction of all artists in top K
        - gini: Gini coefficient of score distribution (higher = more concentrated)
        - top10_pct: Fraction of total score from top 10 items
    """
    if not ranked_items:
        return {'unique_artists': 0, 'unique_artists_all': 0,
                'artist_coverage': 0.0, 'gini': 0.0, 'top10_pct': 0.0}

    # Unique artists
    all_artists = set(item.get('artist', '') for item in ranked_items)
    top_k_artists = set(item.get('artist', '') for item in ranked_items[:k])

    # Score concentration
    scores = [item.get(score_key, 0) for item in ranked_items]
    total_score = sum(scores)

    # Top 10 concentration
    top10_score = sum(scores[:10])
    top10_pct = top10_score / total_score if total_score > 0 else 0

    # Gini coefficient (0 = equal, 1 = maximally concentrated)
    n = len(scores)
    if n > 0 and total_score > 0:
        sorted_scores = sorted(scores)
        cumulative = np.cumsum(sorted_scores)
        gini = (n + 1 - 2 * sum((n + 1 - i) * s for i, s in enumerate(sorted_scores, 1)) / total_score) / n
        gini = max(0, min(1, gini))  # Clamp to [0, 1]
    else:
        gini = 0.0

    return {
        'unique_artists': len(top_k_artists),
        'unique_artists_all': len(all_artists),
        'artist_coverage': len(top_k_artists) / len(all_artists) if all_artists else 0,
        'gini': gini,
        'top10_pct': top10_pct,
    }


def compute_novelty_metrics(
    ranked_items: List[dict],
    k: int,
    score_key: str = 'critics_count',
) -> Dict[str, float]:
    """Compute novelty metrics for a ranking.

    Lower scores = recommending less popular (more novel) items.

    Args:
        ranked_items: All items in ranked order
        k: Cutoff for top-K analysis
        score_key: Key for popularity score

    Returns:
        Dict with:
        - mean_popularity_topk: Average popularity of top K items
        - mean_popularity_all: Average popularity of all items
        - popularity_ratio: Ratio of topK / all (>1 = recommending popular items)
    """
    if not ranked_items:
        return {'mean_popularity_topk': 0.0, 'mean_popularity_all': 0.0, 'popularity_ratio': 1.0}

    all_scores = [item.get(score_key, 0) for item in ranked_items]
    topk_scores = [item.get(score_key, 0) for item in ranked_items[:k]]

    mean_all = np.mean(all_scores) if all_scores else 0
    mean_topk = np.mean(topk_scores) if topk_scores else 0

    return {
        'mean_popularity_topk': float(mean_topk),
        'mean_popularity_all': float(mean_all),
        'popularity_ratio': mean_topk / mean_all if mean_all > 0 else 1.0,
    }


@dataclass
class EvaluationBaseline:
    """Stored baseline metrics for comparison."""
    timestamp: str
    description: str

    holdout: Optional[HoldoutResult] = None
    critic_followthrough: Optional[CriticFollowThroughResult] = None

    # Model configuration at time of baseline
    embedding_method: str = "cooccurrence"
    n_components: int = 50
    use_rank_weights: bool = True


@dataclass
class DualHoldoutResult:
    """Results comparing user vs critics embeddings for holdout."""
    train_years: Tuple[int, int]
    test_years: Tuple[int, int]
    total_discoveries: int

    # User embeddings results
    user_predicted: int
    user_baseline: int

    # Critics embeddings results
    critics_predicted: int
    critics_baseline: int

    top_n_neighbors: int

    # Sample predictions
    user_only: List[str] = field(default_factory=list)  # Predicted by user, not critics
    critics_only: List[str] = field(default_factory=list)  # Predicted by critics, not user
    both: List[str] = field(default_factory=list)  # Predicted by both

    @property
    def user_rate(self) -> float:
        return self.user_predicted / self.total_discoveries if self.total_discoveries > 0 else 0

    @property
    def critics_rate(self) -> float:
        return self.critics_predicted / self.total_discoveries if self.total_discoveries > 0 else 0

    @property
    def user_lift(self) -> float:
        return self.user_predicted / self.user_baseline if self.user_baseline > 0 else 0

    @property
    def critics_lift(self) -> float:
        return self.critics_predicted / self.critics_baseline if self.critics_baseline > 0 else 0


@dataclass
class GranularityResult:
    """Results for a single granularity setting."""
    granularity: str  # "weekly", "session30", etc.
    total_discoveries: int
    predicted: int
    baseline: int
    neighbor_pool_size: int

    @property
    def rate(self) -> float:
        return self.predicted / self.total_discoveries if self.total_discoveries > 0 else 0

    @property
    def lift(self) -> float:
        return self.predicted / self.baseline if self.baseline > 0 else 0


@dataclass
class SessionContinuationResult:
    """Results for session continuation prediction."""
    granularity: str
    total_sessions: int
    total_predictions: int
    hits_at_10: int
    hits_at_20: int
    hits_at_50: int
    mean_reciprocal_rank: float
    artists_in_space: int  # How many test artists are in embedding space

    @property
    def hr_at_10(self) -> float:
        return self.hits_at_10 / self.total_predictions if self.total_predictions > 0 else 0

    @property
    def hr_at_20(self) -> float:
        return self.hits_at_20 / self.total_predictions if self.total_predictions > 0 else 0

    @property
    def hr_at_50(self) -> float:
        return self.hits_at_50 / self.total_predictions if self.total_predictions > 0 else 0


@dataclass
class GranularityComparisonResult:
    """Results comparing different time granularities for embedding co-occurrence."""
    train_years: Tuple[int, int]
    test_years: Tuple[int, int]
    total_discoveries: int
    results: Dict[str, GranularityResult]  # granularity -> result

    def best_by_lift(self) -> str:
        """Return granularity with highest lift."""
        return max(self.results.items(), key=lambda x: x[1].lift)[0]


def run_session_continuation_evaluation(
    csv_path: Path,
    train_end_year: int = 2022,
    test_start_year: int = 2023,
    test_end_year: int = 2024,
    session_gap_minutes: int = 30,
    min_session_artists: int = 3,
    granularities: List[str] | None = None,
) -> Dict[str, SessionContinuationResult]:
    """Evaluate embeddings by predicting session continuation.

    This tests the core hypothesis: do embeddings capture which artists
    are played together? For each test session, use one artist as seed
    and check if other session artists appear as neighbors.

    Args:
        csv_path: Path to scrobbles CSV
        train_end_year: Last year to include in training
        test_start_year: First year of test period
        test_end_year: Last year of test period
        session_gap_minutes: Gap for detecting test sessions
        min_session_artists: Minimum unique artists per test session
        granularities: List to test (weekly, session30, etc.)

    Returns:
        Dict mapping granularity -> SessionContinuationResult
    """
    if granularities is None:
        granularities = ["weekly", "session30"]

    print(f"Running session continuation evaluation...")
    print(f"  Train: up to {train_end_year}")
    print(f"  Test: {test_start_year}-{test_end_year}")
    print(f"  Session gap: {session_gap_minutes}min, min artists: {min_session_artists}")

    # Load full data
    df = data.load_scrobbles(csv_path)

    # Split into train/test
    df_train = df[df['year'] <= train_end_year]
    df_test = df[(df['year'] >= test_start_year) & (df['year'] <= test_end_year)]

    print(f"  Train plays: {len(df_train):,}")
    print(f"  Test plays: {len(df_test):,}")

    # Detect sessions in test data
    df_test = data.detect_sessions(df_test, gap_minutes=session_gap_minutes)

    # Group artists by session
    test_sessions = []
    for session_id in df_test['session_id'].unique():
        session_df = df_test[df_test['session_id'] == session_id]
        artists = session_df['artist'].unique().tolist()
        if len(artists) >= min_session_artists:
            test_sessions.append(artists)

    print(f"  Test sessions (>= {min_session_artists} artists): {len(test_sessions)}")

    results = {}

    for granularity in granularities:
        print(f"\n  [{granularity}] Building embeddings...")

        # Build embeddings with the specified granularity
        user_emb = embeddings.ArtistEmbeddings(csv_path=csv_path)

        # Map granularity names to time windows
        time_window_map = {
            "weekly": "W",
            "daily": "D",
            "monthly": "M",
        }
        if granularity in time_window_map:
            user_emb.build_from_scrobbles(df_train, n_components=50, time_window=time_window_map[granularity], min_plays=5)
        else:
            print(f"  [{granularity}] Unknown, skipping")
            continue

        embedded_artists = set(user_emb.artist_to_idx.keys())

        # Evaluate on test sessions
        total_predictions = 0
        hits_at_10 = 0
        hits_at_20 = 0
        hits_at_50 = 0
        reciprocal_ranks = []

        for session_artists in test_sessions:
            # Filter to artists in embedding space
            in_space = [a for a in session_artists if a in embedded_artists]
            if len(in_space) < 2:
                continue

            # For each artist, try to predict others
            for i, seed_artist in enumerate(in_space):
                targets = [a for j, a in enumerate(in_space) if j != i]
                if not targets:
                    continue

                # Get neighbors
                try:
                    neighbors = user_emb.find_similar(seed_artist, top_n=100)
                    neighbor_names = [n for n, _ in neighbors]
                except ValueError:
                    continue

                # Check if any targets are in neighbors
                for target in targets:
                    total_predictions += 1
                    if target in neighbor_names[:10]:
                        hits_at_10 += 1
                    if target in neighbor_names[:20]:
                        hits_at_20 += 1
                    if target in neighbor_names[:50]:
                        hits_at_50 += 1

                    # Reciprocal rank
                    try:
                        rank = neighbor_names.index(target) + 1
                        reciprocal_ranks.append(1.0 / rank)
                    except ValueError:
                        reciprocal_ranks.append(0.0)

        mrr = np.mean(reciprocal_ranks) if reciprocal_ranks else 0.0

        result = SessionContinuationResult(
            granularity=granularity,
            total_sessions=len(test_sessions),
            total_predictions=total_predictions,
            hits_at_10=hits_at_10,
            hits_at_20=hits_at_20,
            hits_at_50=hits_at_50,
            mean_reciprocal_rank=mrr,
            artists_in_space=len(embedded_artists),
        )
        results[granularity] = result

        print(f"  [{granularity}] Predictions: {total_predictions}")
        print(f"  [{granularity}] HR@10: {result.hr_at_10:.1%}, HR@20: {result.hr_at_20:.1%}, MRR: {mrr:.3f}")

    return results


def run_granularity_evaluation(
    csv_path: Path,
    train_end_year: int = 2022,
    test_start_year: int = 2023,
    test_end_year: int = 2024,
    top_n_neighbors: int = 20,
    min_plays_train: int = 10,
    granularities: List[str] | None = None,
) -> GranularityComparisonResult:
    """Compare holdout performance across different time granularities.

    Tests which grouping approach (weekly vs session-based) best predicts
    future artist discoveries.

    Args:
        csv_path: Path to scrobbles CSV
        train_end_year: Last year to include in training
        test_start_year: First year of test period
        test_end_year: Last year of test period
        top_n_neighbors: Number of neighbors to consider
        min_plays_train: Minimum plays in train period to be a "seed" artist
        granularities: List of granularities to test. Options:
            - "weekly" (time_window="W")
            - "daily" (time_window="D")
            - "session30" (30-minute session gap)
            - "session60" (60-minute session gap)

    Returns:
        GranularityComparisonResult with results for each granularity
    """
    if granularities is None:
        granularities = ["weekly", "session30", "session60"]

    print(f"Running granularity comparison evaluation...")
    print(f"  Train: up to {train_end_year}")
    print(f"  Test: {test_start_year}-{test_end_year}")
    print(f"  Testing: {', '.join(granularities)}")

    # Load full data
    df = data.load_scrobbles(csv_path)

    # Split into train/test
    df_train = df[df['year'] <= train_end_year]
    df_test = df[(df['year'] >= test_start_year) & (df['year'] <= test_end_year)]

    print(f"  Train plays: {len(df_train):,}")
    print(f"  Test plays: {len(df_test):,}")

    # Find discoveries: artists first played in test period
    train_artists = set(df_train['artist'].unique())
    test_artists = set(df_test['artist'].unique())
    discoveries = test_artists - train_artists
    discoveries_norm = {crossref.normalize_for_matching(a): a for a in discoveries}

    print(f"  Discoveries in test period: {len(discoveries)}")

    # Get seed artists
    train_plays = df_train.groupby('artist').size()
    seed_artists = train_plays[train_plays >= min_plays_train].index.tolist()
    print(f"  Seed artists (>= {min_plays_train} plays): {len(seed_artists)}")

    results = {}

    for granularity in granularities:
        print(f"\n  [{granularity}] Building embeddings...")

        # Build embeddings with the specified granularity
        user_emb = embeddings.ArtistEmbeddings(csv_path=csv_path)

        # Map granularity names to time windows
        time_window_map = {
            "weekly": "W",
            "daily": "D",
            "monthly": "M",
        }
        if granularity in time_window_map:
            user_emb.build_from_scrobbles(df_train, n_components=50, time_window=time_window_map[granularity], min_plays=5)
        else:
            print(f"  [{granularity}] Unknown granularity, skipping")
            continue

        # Find neighbors of seed artists
        neighbor_pool = set()
        for artist in seed_artists:
            if artist in user_emb.artist_to_idx:
                try:
                    neighbors = user_emb.find_similar(artist, top_n=top_n_neighbors)
                    for neighbor, _ in neighbors:
                        neighbor_pool.add(crossref.normalize_for_matching(neighbor))
                except ValueError:
                    continue

        print(f"  [{granularity}] Unique neighbors: {len(neighbor_pool)}")

        # Calculate predictions
        predicted_norm = neighbor_pool & set(discoveries_norm.keys())
        all_artists = set(crossref.normalize_for_matching(a) for a in user_emb.artist_to_idx.keys())
        discoveries_possible = len(set(discoveries_norm.keys()) & all_artists)
        random_baseline = len(neighbor_pool) * (discoveries_possible / max(len(all_artists), 1))

        result = GranularityResult(
            granularity=granularity,
            total_discoveries=len(discoveries),
            predicted=len(predicted_norm),
            baseline=int(random_baseline),
            neighbor_pool_size=len(neighbor_pool),
        )

        results[granularity] = result
        print(f"  [{granularity}] Predicted: {result.predicted} (baseline: {result.baseline}) - {result.lift:.2f}x lift")

    return GranularityComparisonResult(
        train_years=(int(df_train['year'].min()), train_end_year),
        test_years=(test_start_year, test_end_year),
        total_discoveries=len(discoveries),
        results=results,
    )


def run_holdout_evaluation(
    csv_path: Path,
    train_end_year: int = 2022,
    test_start_year: int = 2023,
    test_end_year: int = 2024,
    top_n_neighbors: int = 20,
    min_plays_train: int = 10,
) -> DualHoldoutResult:
    """Run future holdout evaluation comparing user vs critics embeddings.

    Tests if neighbors in either embedding space predict discoveries.
    User embeddings capture YOUR co-listening patterns.
    Critics embeddings capture critical consensus patterns.

    Args:
        csv_path: Path to scrobbles CSV
        train_end_year: Last year to include in training
        test_start_year: First year of test period
        test_end_year: Last year of test period
        top_n_neighbors: Number of neighbors to consider
        min_plays_train: Minimum plays in train period to be a "seed" artist

    Returns:
        DualHoldoutResult comparing both embedding types
    """
    print(f"Running holdout evaluation...")
    print(f"  Train: up to {train_end_year}")
    print(f"  Test: {test_start_year}-{test_end_year}")

    # Load full data
    df = data.load_scrobbles(csv_path)

    # Split into train/test
    df_train = df[df['year'] <= train_end_year]
    df_test = df[(df['year'] >= test_start_year) & (df['year'] <= test_end_year)]

    print(f"  Train plays: {len(df_train):,}")
    print(f"  Test plays: {len(df_test):,}")

    # Find discoveries: artists first played in test period
    train_artists = set(df_train['artist'].unique())
    test_artists = set(df_test['artist'].unique())
    discoveries = test_artists - train_artists
    discoveries_norm = {crossref.normalize_for_matching(a): a for a in discoveries}

    print(f"  Discoveries in test period: {len(discoveries)}")

    if len(discoveries) == 0:
        return DualHoldoutResult(
            train_years=(df_train['year'].min(), train_end_year),
            test_years=(test_start_year, test_end_year),
            total_discoveries=0,
            user_predicted=0, user_baseline=0,
            critics_predicted=0, critics_baseline=0,
            top_n_neighbors=top_n_neighbors,
        )

    # Get seed artists
    train_plays = df_train.groupby('artist').size()
    seed_artists = train_plays[train_plays >= min_plays_train].index.tolist()
    print(f"  Seed artists (>= {min_plays_train} plays): {len(seed_artists)}")

    # === USER EMBEDDINGS ===
    print(f"\n  [User Embeddings] Building from train data...")
    user_emb = embeddings.ArtistEmbeddings(csv_path=csv_path)
    user_emb.build_from_scrobbles(df_train, n_components=50, time_window="W", min_plays=5)

    user_neighbor_pool = set()
    for artist in seed_artists:
        if artist in user_emb.artist_to_idx:
            try:
                neighbors = user_emb.find_similar(artist, top_n=top_n_neighbors)
                for neighbor, _ in neighbors:
                    user_neighbor_pool.add(crossref.normalize_for_matching(neighbor))
            except ValueError:
                continue

    print(f"  [User] Unique neighbors: {len(user_neighbor_pool)}")

    # User predictions (can only predict artists already in train data)
    user_predicted_norm = user_neighbor_pool & set(discoveries_norm.keys())
    # User baseline - random from user-embedded artists
    user_all_artists = set(crossref.normalize_for_matching(a) for a in user_emb.artist_to_idx.keys())
    user_discoveries_possible = len(set(discoveries_norm.keys()) & user_all_artists)
    user_random = len(user_neighbor_pool) * (user_discoveries_possible / max(len(user_all_artists), 1))

    print(f"  [User] Predicted: {len(user_predicted_norm)} (baseline: {int(user_random)})")

    # === CRITICS EMBEDDINGS (time-bounded to avoid leakage) ===
    print(f"\n  [Critics Embeddings] Loading (bounded to {train_end_year})...")
    try:
        # Use time-bounded embeddings to avoid leakage from future years
        critics_emb = embeddings.get_or_build_critics_embeddings(max_year=train_end_year)
    except Exception as e:
        print(f"  Error: {e}")
        critics_emb = None

    critics_neighbor_pool = set()
    critics_predicted_norm = set()
    critics_random = 0

    if critics_emb:
        seeds_in_critics = 0
        for artist in seed_artists:
            norm_artist = crossref.normalize_for_matching(artist)
            if norm_artist in critics_emb.artist_to_idx:
                seeds_in_critics += 1
                try:
                    neighbors = critics_emb.find_similar(artist, top_n=top_n_neighbors)
                    for neighbor, _ in neighbors:
                        critics_neighbor_pool.add(neighbor)
                except ValueError:
                    continue

        print(f"  [Critics] Seeds in space: {seeds_in_critics}")
        print(f"  [Critics] Unique neighbors: {len(critics_neighbor_pool)}")

        critics_predicted_norm = critics_neighbor_pool & set(discoveries_norm.keys())
        critics_all = set(critics_emb.artist_to_idx.keys())
        critics_discoveries_possible = len(set(discoveries_norm.keys()) & critics_all)
        critics_random = len(critics_neighbor_pool) * (critics_discoveries_possible / max(len(critics_all), 1))

        print(f"  [Critics] Predicted: {len(critics_predicted_norm)} (baseline: {int(critics_random)})")

    # Find overlaps
    user_only = [discoveries_norm[n] for n in (user_predicted_norm - critics_predicted_norm)]
    critics_only = [discoveries_norm[n] for n in (critics_predicted_norm - user_predicted_norm)]
    both = [discoveries_norm[n] for n in (user_predicted_norm & critics_predicted_norm)]

    result = DualHoldoutResult(
        train_years=(int(df_train['year'].min()), train_end_year),
        test_years=(test_start_year, test_end_year),
        total_discoveries=len(discoveries),
        user_predicted=len(user_predicted_norm),
        user_baseline=int(user_random),
        critics_predicted=len(critics_predicted_norm),
        critics_baseline=int(critics_random),
        top_n_neighbors=top_n_neighbors,
        user_only=sorted(user_only)[:10],
        critics_only=sorted(critics_only)[:10],
        both=sorted(both)[:10],
    )

    print(f"\n  Summary:")
    print(f"    User embeddings: {result.user_predicted}/{result.total_discoveries} ({result.user_rate:.1%}) - {result.user_lift:.1f}x lift")
    print(f"    Critics embeddings: {result.critics_predicted}/{result.total_discoveries} ({result.critics_rate:.1%}) - {result.critics_lift:.1f}x lift")

    return result


def run_critic_followthrough(
    csv_path: Path,
    reference_year: int = 2020,
    followup_start: int = 2021,
    followup_end: int = 2024,
    top_n_albums: int = 50,
    min_familiarity: float | None = None,
) -> CriticFollowThroughResult:
    """Evaluate if critic recommendations became favorites.

    Look at albums critics recommended in reference_year that you hadn't
    heard, then check how many you played in subsequent years.

    Args:
        csv_path: Path to scrobbles CSV
        reference_year: Year of critic recommendations
        followup_start: Start of followup period
        followup_end: End of followup period
        top_n_albums: Number of top recommendations to track
        min_familiarity: If provided, use continuous familiarity scoring (0-1)
                        instead of binary 5x5 rule. Default None uses 5x5.

    Returns:
        CriticFollowThroughResult with conversion metrics
    """
    print(f"Running critic follow-through evaluation...")
    print(f"  Reference year: {reference_year}")
    print(f"  Follow-up: {followup_start}-{followup_end}")
    if min_familiarity is not None:
        print(f"  Familiarity threshold: {min_familiarity}")

    # Load data
    df = data.load_scrobbles(csv_path)

    # Load critics data for reference year
    critics_path = Path(__file__).parent.parent / f"critics-{reference_year}.json"
    if not critics_path.exists():
        raise FileNotFoundError(f"No critics data for {reference_year}")

    with open(critics_path) as f:
        critics_raw = json.load(f)

    # What had you heard by end of reference year?
    df_before = df[df['year'] <= reference_year]
    listened_before = data.get_listened_albums(df_before, min_familiarity=min_familiarity)
    heard_before = set()
    for artist, album in listened_before:
        key = (crossref.normalize_for_matching(artist),
               crossref.normalize_for_matching(album))
        heard_before.add(key)

    print(f"  Albums heard by end of {reference_year}: {len(heard_before)}")

    # Collect unheard critic recommendations
    unheard_albums = {}  # key -> {artist, album, critics, critics_count}

    for lst in critics_raw:
        critic = lst['critic']
        for album in lst['albums']:
            artist = album.get('artist', '')
            title = album.get('title', '')
            if artist and title:
                key = (crossref.normalize_for_matching(artist),
                       crossref.normalize_for_matching(title))
                if key not in heard_before:
                    if key not in unheard_albums:
                        unheard_albums[key] = {
                            'artist': artist,
                            'album': title,
                            'critics': [],
                            'critics_count': 0,
                        }
                    unheard_albums[key]['critics'].append(critic)
                    unheard_albums[key]['critics_count'] += 1

    print(f"  Unheard recommendations: {len(unheard_albums)}")

    # Sort by critic count
    by_count = sorted(unheard_albums.values(), key=lambda x: -x['critics_count'])[:top_n_albums]

    # Try to get vector-weighted ranking too
    by_vector = []
    try:
        critic_vectors = embeddings.get_or_build_critic_vectors()
        user_vector = critic_vectors.compute_user_vector(df_before, top_n_artists=100)
        all_similar = critic_vectors.find_similar_critics(user_vector, top_n=500)
        vector_sims = {c: sim for c, sim, _ in all_similar}

        for info in unheard_albums.values():
            info['vector_score'] = sum(vector_sims.get(c, 0) for c in info['critics'])

        by_vector = sorted(unheard_albums.values(), key=lambda x: -x.get('vector_score', 0))[:top_n_albums]
    except Exception as e:
        print(f"  Could not compute vector scores: {e}")

    # Check follow-up plays
    df_followup = df[(df['year'] >= followup_start) & (df['year'] <= followup_end)]

    # Count plays per album in followup
    followup_plays = {}
    for _, row in df_followup.iterrows():
        artist = row.get('artist', '')
        album = row.get('album', '')
        if pd.notna(artist) and pd.notna(album) and artist and album:
            key = (crossref.normalize_for_matching(artist),
                   crossref.normalize_for_matching(album))
            followup_plays[key] = followup_plays.get(key, 0) + 1

    # Evaluate conversion at multiple thresholds
    played_any = 0
    played_5plus = 0
    played_10plus = 0
    played_50plus = 0

    for key in unheard_albums:
        plays = followup_plays.get(key, 0)
        if plays > 0:
            played_any += 1
        if plays >= 5:
            played_5plus += 1
        if plays >= 10:
            played_10plus += 1
        if plays >= 50:
            played_50plus += 1

    # Add followup plays to ALL albums for sorting/ranking
    all_albums_list = list(unheard_albums.values())
    for item in all_albums_list:
        key = (crossref.normalize_for_matching(item['artist']),
               crossref.normalize_for_matching(item['album']))
        item['followup_plays'] = followup_plays.get(key, 0)

    # Sort full lists for ranking evaluation
    full_by_count = sorted(all_albums_list, key=lambda x: -x['critics_count'])
    full_by_vector = sorted(all_albums_list, key=lambda x: -x.get('vector_score', 0)) if by_vector else []

    # Top N for display
    by_count = full_by_count[:top_n_albums]
    by_vector = full_by_vector[:top_n_albums]

    # Compute ranking metrics at multiple K values
    k_values = [10, 20, 50, 100]
    thresholds = [1, 5, 10, 50]  # play count thresholds

    ndcg_by_count = {}
    ndcg_by_vector = {}
    hits_by_count = {}
    hits_by_vector = {}

    for k in k_values:
        # NDCG uses raw play counts as relevance
        ndcg_by_count[k] = compute_ndcg(full_by_count, k, 'followup_plays')
        hits_by_count[k] = compute_hits_at_k(full_by_count, k, thresholds)

        if full_by_vector:
            ndcg_by_vector[k] = compute_ndcg(full_by_vector, k, 'followup_plays')
            hits_by_vector[k] = compute_hits_at_k(full_by_vector, k, thresholds)

    # Compute coverage and novelty metrics at K=50 (reasonable recommendation list size)
    eval_k = 50
    coverage_by_count = compute_coverage_metrics(full_by_count, eval_k, 'critics_count')
    coverage_by_vector = compute_coverage_metrics(full_by_vector, eval_k, 'vector_score') if full_by_vector else {}

    novelty_by_count = compute_novelty_metrics(full_by_count, eval_k, 'critics_count')
    novelty_by_vector = compute_novelty_metrics(full_by_vector, eval_k, 'vector_score') if full_by_vector else {}

    result = CriticFollowThroughResult(
        reference_year=reference_year,
        followup_years=(followup_start, followup_end),
        total_unheard=len(unheard_albums),
        played_any=played_any,
        played_5plus=played_5plus,
        played_10plus=played_10plus,
        played_50plus=played_50plus,
        top_by_count=by_count,
        top_by_vector=by_vector,
        ndcg_by_count=ndcg_by_count,
        ndcg_by_vector=ndcg_by_vector,
        hits_by_count=hits_by_count,
        hits_by_vector=hits_by_vector,
        coverage_by_count=coverage_by_count,
        coverage_by_vector=coverage_by_vector,
        novelty_by_count=novelty_by_count,
        novelty_by_vector=novelty_by_vector,
    )

    print(f"\n  Conversion Rates:")
    print(f"    Played at all: {result.played_any}/{result.total_unheard} ({result.any_rate:.1%})")
    print(f"    Played 5+:     {result.played_5plus}/{result.total_unheard}")
    print(f"    Played 10+:    {result.played_10plus}/{result.total_unheard} ({result.love_rate:.1%})")
    print(f"    Played 50+:    {result.played_50plus}/{result.total_unheard}")

    print(f"\n  Ranking Quality (NDCG@K):")
    for k in k_values:
        count_ndcg = ndcg_by_count.get(k, 0)
        vector_ndcg = ndcg_by_vector.get(k, 0) if ndcg_by_vector else 0
        print(f"    @{k:3d}: Count={count_ndcg:.3f}  Vector={vector_ndcg:.3f}")

    print(f"\n  Coverage & Novelty (at K={eval_k}):")
    print(f"    Count: {coverage_by_count.get('unique_artists', 0)} artists, top10={coverage_by_count.get('top10_pct', 0):.1%} concentration")
    if coverage_by_vector:
        print(f"    Vector: {coverage_by_vector.get('unique_artists', 0)} artists, top10={coverage_by_vector.get('top10_pct', 0):.1%} concentration")
    print(f"    Popularity ratio (>1 = favoring popular): Count={novelty_by_count.get('popularity_ratio', 1):.2f}, Vector={novelty_by_vector.get('popularity_ratio', 1):.2f}")

    return result


def save_baseline(
    baseline: EvaluationBaseline,
    cache_dir: Optional[Path] = None,
) -> Path:
    """Save evaluation baseline to cache."""
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "lastfm-analysis" / "evaluation"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Create filename from timestamp
    ts = baseline.timestamp.replace(":", "-").replace(" ", "_")
    path = cache_dir / f"baseline_{ts}.json"

    # Convert to JSON-serializable dict
    data = {
        "timestamp": baseline.timestamp,
        "description": baseline.description,
        "embedding_method": baseline.embedding_method,
        "n_components": baseline.n_components,
        "use_rank_weights": baseline.use_rank_weights,
    }

    if baseline.holdout:
        data["holdout"] = {
            "train_years": baseline.holdout.train_years,
            "test_years": baseline.holdout.test_years,
            "total_discoveries": baseline.holdout.total_discoveries,
            "user_predicted": baseline.holdout.user_predicted,
            "user_baseline": baseline.holdout.user_baseline,
            "user_lift": baseline.holdout.user_lift,
            "critics_predicted": baseline.holdout.critics_predicted,
            "critics_baseline": baseline.holdout.critics_baseline,
            "critics_lift": baseline.holdout.critics_lift,
            "top_n_neighbors": baseline.holdout.top_n_neighbors,
        }

    if baseline.critic_followthrough:
        ft = baseline.critic_followthrough
        data["critic_followthrough"] = {
            "reference_year": ft.reference_year,
            "followup_years": ft.followup_years,
            "total_unheard": ft.total_unheard,
            "played_any": ft.played_any,
            "played_5plus": ft.played_5plus,
            "played_10plus": ft.played_10plus,
            "played_50plus": ft.played_50plus,
            "any_rate": ft.any_rate,
            "love_rate": ft.love_rate,
            # Ranking metrics - convert int keys to strings for JSON
            "ndcg_by_count": {str(k): v for k, v in ft.ndcg_by_count.items()},
            "ndcg_by_vector": {str(k): v for k, v in ft.ndcg_by_vector.items()},
            "hits_by_count": {str(k): {str(t): c for t, c in v.items()} for k, v in ft.hits_by_count.items()},
            "hits_by_vector": {str(k): {str(t): c for t, c in v.items()} for k, v in ft.hits_by_vector.items()},
            # Coverage and novelty
            "coverage_by_count": ft.coverage_by_count,
            "coverage_by_vector": ft.coverage_by_vector,
            "novelty_by_count": ft.novelty_by_count,
            "novelty_by_vector": ft.novelty_by_vector,
        }

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

    return path


def load_baselines(cache_dir: Optional[Path] = None) -> List[dict]:
    """Load all saved baselines."""
    if cache_dir is None:
        cache_dir = Path.home() / ".cache" / "lastfm-analysis" / "evaluation"

    if not cache_dir.exists():
        return []

    baselines = []
    for path in sorted(cache_dir.glob("baseline_*.json")):
        with open(path) as f:
            baselines.append(json.load(f))

    return baselines
