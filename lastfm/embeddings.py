"""Artist similarity embeddings using co-occurrence matrix and SVD."""

import json
import pickle
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import pandas as pd
import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from sklearn.metrics.pairwise import cosine_similarity


def get_csv_cache_id(csv_path: Path) -> str:
    """Generate a unique cache identifier for a CSV file.

    Uses the absolute path to create a stable hash that's unique per CSV file.

    Args:
        csv_path: Path to the CSV file

    Returns:
        Short hash string identifying this CSV
    """
    # Use absolute path to ensure consistency
    abs_path = csv_path.resolve()
    # Hash the path to get a short, stable identifier
    path_hash = hashlib.md5(str(abs_path).encode()).hexdigest()[:12]
    # Use filename + hash for readability
    return f"{csv_path.stem}_{path_hash}"


class ArtistEmbeddings:
    """Build and query artist similarity embeddings."""

    def __init__(self, csv_path: Optional[Path] = None, cache_dir: Optional[Path] = None):
        """Initialize embeddings manager.

        Args:
            csv_path: Path to CSV file (used to create CSV-specific cache subfolder)
            cache_dir: Base cache directory (default: ~/.cache/music-history-analysis)
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "music-history-analysis"

        # Create CSV-specific subfolder if csv_path provided
        if csv_path is not None:
            csv_id = get_csv_cache_id(csv_path)
            self.cache_dir = cache_dir / csv_id
        else:
            self.cache_dir = cache_dir

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = csv_path

        self.embeddings: Optional[np.ndarray] = None
        self.artist_to_idx: Optional[Dict[str, int]] = None
        self.idx_to_artist: Optional[Dict[int, str]] = None
        self.svd_model: Optional[TruncatedSVD] = None

    def build_from_scrobbles(
        self,
        df: pd.DataFrame,
        n_components: int = 50,
        time_window: str = "W",  # "W" for weekly (best), "D" for daily
        min_plays: int = 5,
        method: str = "cooccurrence",  # "cooccurrence" or "temporal"
    ) -> None:
        """Build embeddings from scrobble data.

        Args:
            df: DataFrame with scrobble data (must have 'artist' and 'timestamp')
            n_components: Number of embedding dimensions (default: 50)
            time_window: Time window for co-occurrence grouping (default: "W" for weekly).
                        Weekly was validated as best performing in eval suite.
            min_plays: Minimum plays for an artist to be included (default: 5)
            method: "cooccurrence" (artists × artists) or "temporal" (artists × time) (default: "cooccurrence")
        """
        print(f"Building artist embeddings (method: {method})...")

        # Filter to artists with minimum plays
        artist_counts = df["artist"].value_counts()
        valid_artists = artist_counts[artist_counts >= min_plays].index.tolist()
        df_filtered = df[df["artist"].isin(valid_artists)].copy()

        print(f"  Artists after filtering (min {min_plays} plays): {len(valid_artists)}")

        # Create artist index mapping
        self.artist_to_idx = {artist: idx for idx, artist in enumerate(valid_artists)}
        self.idx_to_artist = {idx: artist for artist, idx in self.artist_to_idx.items()}

        if method == "cooccurrence":
            # Build artist × artist co-occurrence matrix
            print(f"  Building artist co-occurrence matrix...")

            # Use weekly co-occurrence (tested as best performing granularity)
            # Session-based was tested but underperformed weekly in eval suite
            df_filtered["group_id"] = df_filtered["timestamp"].dt.to_period(time_window)
            groups = df_filtered["group_id"].unique()
            print(f"  Groups: {len(groups)} time windows ({time_window})")

            # Initialize symmetric matrix
            n_artists = len(valid_artists)
            matrix = np.zeros((n_artists, n_artists))

            # For each group, find which artists were played
            for group in groups:
                group_artists = df_filtered[df_filtered["group_id"] == group]["artist"].unique()
                group_indices = [self.artist_to_idx[a] for a in group_artists if a in self.artist_to_idx]

                # Increment co-occurrence for all pairs (including self)
                for i in group_indices:
                    for j in group_indices:
                        matrix[i, j] += 1

            print(f"  Matrix shape: {matrix.shape}")
            print(f"  Matrix density: {(matrix > 0).sum() / matrix.size * 100:.2f}%")

            # Normalize by the geometric mean of individual counts to avoid bias toward popular artists
            # matrix[i,j] = cooccur(i,j) / sqrt(count(i) * count(j))
            diag = np.sqrt(np.diag(matrix))
            diag[diag == 0] = 1  # Avoid division by zero
            matrix = matrix / np.outer(diag, diag)

            print(f"  Normalized by geometric mean (Jaccard-style)")

        else:  # temporal method
            # Build artists × time_windows matrix (original approach)
            df_filtered["time_window"] = df_filtered["timestamp"].dt.to_period(time_window)
            time_windows = df_filtered["time_window"].unique()

            print(f"  Time windows ({time_window}): {len(time_windows)}")
            print(f"  Building temporal matrix...")

            matrix = np.zeros((len(valid_artists), len(time_windows)))

            for window_idx, window in enumerate(time_windows):
                window_plays = df_filtered[df_filtered["time_window"] == window]
                artist_plays = window_plays["artist"].value_counts()

                for artist, plays in artist_plays.items():
                    if artist in self.artist_to_idx:
                        artist_idx = self.artist_to_idx[artist]
                        matrix[artist_idx, window_idx] = plays

            print(f"  Matrix shape: {matrix.shape}")
            print(f"  Matrix density: {(matrix > 0).sum() / matrix.size * 100:.2f}%")

            # Log scaling + TF-IDF + normalization
            matrix = np.log1p(matrix)
            artist_week_counts = (matrix > 0).sum(axis=1, keepdims=True)
            idf = np.log(matrix.shape[1] / (artist_week_counts + 1))
            matrix = matrix * idf
            matrix = normalize(matrix, norm="l2", axis=0)
            print(f"  Applied TF-IDF and normalization")

        # Normalize rows before SVD
        matrix = normalize(matrix, norm="l2", axis=1)

        # Apply SVD to reduce dimensionality
        print(f"  Applying SVD (n_components={n_components})...")
        self.svd_model = TruncatedSVD(n_components=n_components, random_state=42)
        self.embeddings = self.svd_model.fit_transform(matrix)

        # Normalize embeddings for cosine similarity
        self.embeddings = normalize(self.embeddings, norm="l2", axis=1)

        explained_var = self.svd_model.explained_variance_ratio_.sum() * 100
        print(f"  Explained variance: {explained_var:.1f}%")
        print(f"  Embeddings shape: {self.embeddings.shape}")
        print(f"✓ Embeddings built successfully\n")

    def get_dimension_poles(
        self,
        dimension: int,
        top_n: int = 5,
    ) -> Dict[str, List[Tuple[str, float]]]:
        """Get artists at extreme ends of a dimension.

        Args:
            dimension: Dimension index (0 to n_components-1)
            top_n: Number of artists to return at each pole

        Returns:
            Dict with 'positive' and 'negative' lists of (artist, loading) tuples
        """
        if self.embeddings is None:
            raise ValueError("Embeddings not built yet. Call build_from_scrobbles() first.")

        if dimension < 0 or dimension >= self.embeddings.shape[1]:
            raise ValueError(f"Dimension must be 0 to {self.embeddings.shape[1] - 1}")

        dim_values = self.embeddings[:, dimension]

        # Get indices of highest/lowest values
        top_indices = np.argsort(dim_values)[-top_n:][::-1]
        bottom_indices = np.argsort(dim_values)[:top_n]

        return {
            "positive": [(self.idx_to_artist[i], float(dim_values[i])) for i in top_indices],
            "negative": [(self.idx_to_artist[i], float(dim_values[i])) for i in bottom_indices],
        }

    def get_explained_variance(self) -> np.ndarray:
        """Get explained variance ratio for each dimension.

        Returns:
            Array of variance explained per dimension (sums to less than 1.0)
        """
        if self.svd_model is None:
            raise ValueError("Embeddings not built yet. Call build_from_scrobbles() first.")

        return self.svd_model.explained_variance_ratio_

    def get_embedding(self, artist: str) -> Optional[np.ndarray]:
        """Get embedding vector for an artist.

        Args:
            artist: Artist name

        Returns:
            Embedding vector or None if not found
        """
        if self.embeddings is None:
            return None

        # Try exact match first
        if artist in self.artist_to_idx:
            return self.embeddings[self.artist_to_idx[artist]]

        # Try case-insensitive match
        artist_lower = artist.lower()
        for a, idx in self.artist_to_idx.items():
            if a.lower() == artist_lower:
                return self.embeddings[idx]

        return None

    def find_similar(
        self,
        artist: str,
        top_n: int = 10,
        min_similarity: float = 0.0,
    ) -> List[Tuple[str, float]]:
        """Find artists similar to the given artist.

        Args:
            artist: Artist name to find similar artists for
            top_n: Number of similar artists to return
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of (artist_name, similarity_score) tuples, sorted by similarity
        """
        if self.embeddings is None:
            raise ValueError("Embeddings not built yet. Call build_from_scrobbles() first.")

        # Normalize artist name for matching (case-insensitive)
        artist_lower = artist.lower()
        matching_artists = [
            a for a in self.artist_to_idx.keys()
            if artist_lower in a.lower() or a.lower() in artist_lower
        ]

        if not matching_artists:
            raise ValueError(f"Artist '{artist}' not found in embeddings")

        # Use exact match if available, otherwise first match
        if artist in matching_artists:
            query_artist = artist
        else:
            query_artist = matching_artists[0]

        query_idx = self.artist_to_idx[query_artist]
        query_embedding = self.embeddings[query_idx].reshape(1, -1)

        # Compute cosine similarities
        similarities = cosine_similarity(query_embedding, self.embeddings)[0]

        # Get top similar artists (excluding the query artist itself)
        similar_indices = similarities.argsort()[::-1]
        results = []

        for idx in similar_indices:
            if idx == query_idx:
                continue  # Skip the query artist itself

            similarity = similarities[idx]
            if similarity < min_similarity:
                break

            artist_name = self.idx_to_artist[idx]
            results.append((artist_name, float(similarity)))

            if len(results) >= top_n:
                break

        return results

    def save(self, cache_name: str = "artist_embeddings") -> None:
        """Save embeddings to cache.

        Args:
            cache_name: Name for the cache file (without extension)
        """
        cache_path = self.cache_dir / f"{cache_name}.pkl"

        cache_data = {
            "embeddings": self.embeddings,
            "artist_to_idx": self.artist_to_idx,
            "idx_to_artist": self.idx_to_artist,
            "svd_model": self.svd_model,
        }

        with open(cache_path, "wb") as f:
            pickle.dump(cache_data, f)

        print(f"Saved embeddings to: {cache_path}")

    def load(self, cache_name: str = "artist_embeddings") -> bool:
        """Load embeddings from cache.

        Args:
            cache_name: Name of the cache file (without extension)

        Returns:
            True if loaded successfully, False otherwise
        """
        cache_path = self.cache_dir / f"{cache_name}.pkl"

        if not cache_path.exists():
            return False

        with open(cache_path, "rb") as f:
            cache_data = pickle.load(f)

        self.embeddings = cache_data["embeddings"]
        self.artist_to_idx = cache_data["artist_to_idx"]
        self.idx_to_artist = cache_data["idx_to_artist"]
        self.svd_model = cache_data["svd_model"]

        print(f"Loaded embeddings from cache: {cache_path}")
        print(f"  {len(self.artist_to_idx)} artists, {self.embeddings.shape[1]} dimensions")

        return True


def build_embeddings_from_csv(
    csv_path: Path,
    n_components: int = 50,
    time_window: str = "W",  # Weekly co-occurrence (validated as best)
    min_plays: int = 5,
    method: str = "cooccurrence",
    force_rebuild: bool = False,
) -> ArtistEmbeddings:
    """Build or load artist embeddings from CSV file.

    Args:
        csv_path: Path to scrobbles CSV
        n_components: Number of embedding dimensions
        time_window: Time window for co-occurrence (default: "W" for weekly)
        min_plays: Minimum plays for an artist to be included
        method: "cooccurrence" (artist × artist) or "temporal" (artist × time)
        force_rebuild: Force rebuild even if cached embeddings exist

    Returns:
        ArtistEmbeddings instance
    """
    from . import data

    # Pass csv_path to create CSV-specific cache directory
    embeddings = ArtistEmbeddings(csv_path=csv_path)

    # Create cache name that reflects the configuration
    cache_name = f"artist_embeddings_{method}_{time_window}_minplays{min_plays}"

    # Try to load from cache
    if not force_rebuild and embeddings.load(cache_name=cache_name):
        return embeddings

    # Build from scratch
    df = data.load_scrobbles(csv_path)
    embeddings.build_from_scrobbles(
        df,
        n_components=n_components,
        time_window=time_window,
        min_plays=min_plays,
        method=method,
    )

    # Save to cache
    embeddings.save(cache_name=cache_name)

    return embeddings


class CriticsEmbeddings:
    """Build embeddings from critics' co-listing patterns.

    Artists that appear on the same critic's list are considered similar.
    This captures "critical consensus" similarity, which differs from
    personal listening patterns.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize critics embeddings manager.

        Args:
            cache_dir: Base cache directory (default: ~/.cache/music-history-analysis)
        """
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "music-history-analysis"

        self.cache_dir = cache_dir / "critics_embeddings"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.embeddings: Optional[np.ndarray] = None
        self.artist_to_idx: Optional[Dict[str, int]] = None
        self.idx_to_artist: Optional[Dict[int, str]] = None
        self.svd_model: Optional[TruncatedSVD] = None
        self.years_covered: Optional[List[int]] = None

    def build_from_critics_data(
        self,
        critics_loader,  # Callable[[int], dict] - loads critics data for a year
        years: Optional[List[int]] = None,
        n_components: int = 50,
        min_critics: int = 2,
        use_rank_weights: bool = True,
    ) -> None:
        """Build embeddings from critics' co-listing patterns.

        For each critic's list, all artists on that list are considered
        to co-occur. The co-occurrence weight depends on rank position.

        Args:
            critics_loader: Function that takes a year and returns critics data dict
            years: Years to include (default: 2011-2025)
            n_components: Number of embedding dimensions
            min_critics: Minimum critics listing an artist to be included
            use_rank_weights: Weight co-occurrences by rank (default: True)
                #1 pick contributes more than #50
        """
        from . import crossref

        if years is None:
            years = list(range(2011, 2026))

        print(f"Building critics embeddings from {len(years)} years of data...")
        if use_rank_weights:
            print(f"  Using rank weighting: weight = 1 / log2(rank + 1)")

        # First pass: collect all artists and their critics
        artist_critics: Dict[str, set] = {}  # normalized_artist -> set of (critic, year)

        for year in years:
            try:
                data = critics_loader(year)
                raw = data.get("raw", data)  # Handle both formats

                for lst in raw:
                    critic = lst.get("critic", "unknown")
                    albums = lst.get("albums", [])

                    for album in albums:
                        artist = album.get("artist", "")
                        if artist:
                            norm_artist = crossref.normalize_for_matching(artist)
                            if norm_artist not in artist_critics:
                                artist_critics[norm_artist] = set()
                            artist_critics[norm_artist].add((critic, year))
            except (IOError, json.JSONDecodeError, KeyError):
                continue

        # Filter to artists with sufficient presence
        valid_artists = [
            a for a, critics in artist_critics.items()
            if len(critics) >= min_critics
        ]
        valid_artists = sorted(valid_artists)

        print(f"  Artists with {min_critics}+ critic listings: {len(valid_artists)}")

        if len(valid_artists) < n_components:
            raise ValueError(
                f"Not enough artists ({len(valid_artists)}) for {n_components} components. "
                f"Try lowering min_critics or adding more years."
            )

        # Build index mappings
        self.artist_to_idx = {a: i for i, a in enumerate(valid_artists)}
        self.idx_to_artist = {i: a for a, i in self.artist_to_idx.items()}

        # Second pass: build co-occurrence matrix with rank weighting
        print(f"  Building co-listing matrix...")
        n = len(valid_artists)
        matrix = np.zeros((n, n), dtype=np.float32)

        for year in years:
            try:
                data = critics_loader(year)
                raw = data.get("raw", data)

                for lst in raw:
                    # Get all valid artists on this list with their ranks and weights
                    list_entries = []  # [(norm_artist, weight), ...]
                    list_length = len(lst.get("albums", []))

                    for album in lst.get("albums", []):
                        artist = album.get("artist", "")
                        rank = album.get("rank", 50)  # Default to 50 if no rank

                        if artist:
                            norm_artist = crossref.normalize_for_matching(artist)
                            if norm_artist in self.artist_to_idx:
                                if use_rank_weights:
                                    # Weight by reciprocal log of rank
                                    # #1 → 1.0, #2 → 0.63, #10 → 0.30, #50 → 0.18
                                    weight = 1.0 / np.log2(rank + 1)
                                    # Also normalize by list length (10-item list #1 > 100-item list #1)
                                    length_factor = 1.0 / np.log2(list_length + 1)
                                    weight *= length_factor
                                else:
                                    weight = 1.0
                                list_entries.append((norm_artist, weight))

                    # Increment co-occurrence for all pairs on this list
                    # Weight is the product of individual weights (geometric mean style)
                    for a1, w1 in list_entries:
                        for a2, w2 in list_entries:
                            pair_weight = np.sqrt(w1 * w2)  # Geometric mean of weights
                            matrix[self.artist_to_idx[a1], self.artist_to_idx[a2]] += pair_weight
            except (IOError, json.JSONDecodeError, KeyError):
                continue

        print(f"  Matrix shape: {matrix.shape}")
        print(f"  Matrix density: {(matrix > 0).sum() / matrix.size * 100:.2f}%")

        # Normalize by geometric mean (same as user embeddings)
        diag = np.sqrt(np.diag(matrix))
        diag[diag == 0] = 1
        matrix = matrix / np.outer(diag, diag)

        print(f"  Normalized by geometric mean")

        # Normalize rows before SVD
        matrix = normalize(matrix, norm="l2", axis=1)

        # Apply SVD
        print(f"  Applying SVD (n_components={n_components})...")
        self.svd_model = TruncatedSVD(n_components=n_components, random_state=42)
        self.embeddings = self.svd_model.fit_transform(matrix)

        # Normalize embeddings for cosine similarity
        self.embeddings = normalize(self.embeddings, norm="l2", axis=1)

        self.years_covered = years

        explained_var = self.svd_model.explained_variance_ratio_.sum() * 100
        print(f"  Explained variance: {explained_var:.1f}%")
        print(f"  Embeddings shape: {self.embeddings.shape}")
        print(f"✓ Critics embeddings built successfully\n")

    def find_similar(
        self,
        artist: str,
        top_n: int = 10,
        min_similarity: float = 0.0,
    ) -> List[Tuple[str, float]]:
        """Find artists similar to the given artist in critics-space.

        Args:
            artist: Artist name (will be normalized for matching)
            top_n: Number of similar artists to return
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of (artist_name, similarity_score) tuples
        """
        from . import crossref

        if self.embeddings is None:
            raise ValueError("Embeddings not built. Call build_from_critics_data() first.")

        # Normalize for matching
        norm_artist = crossref.normalize_for_matching(artist)

        if norm_artist not in self.artist_to_idx:
            raise ValueError(f"Artist '{artist}' not found in critics embeddings")

        query_idx = self.artist_to_idx[norm_artist]
        query_embedding = self.embeddings[query_idx].reshape(1, -1)

        # Compute cosine similarities
        similarities = cosine_similarity(query_embedding, self.embeddings)[0]

        # Get top similar artists (excluding query)
        similar_indices = similarities.argsort()[::-1]
        results = []

        for idx in similar_indices:
            if idx == query_idx:
                continue

            similarity = similarities[idx]
            if similarity < min_similarity:
                break

            # Return normalized artist name
            results.append((self.idx_to_artist[idx], float(similarity)))

            if len(results) >= top_n:
                break

        return results

    def get_embedding(self, artist: str) -> Optional[np.ndarray]:
        """Get embedding vector for an artist.

        Args:
            artist: Artist name (will be normalized)

        Returns:
            Embedding vector or None if not found
        """
        from . import crossref

        if self.embeddings is None:
            return None

        norm_artist = crossref.normalize_for_matching(artist)
        if norm_artist in self.artist_to_idx:
            return self.embeddings[self.artist_to_idx[norm_artist]]

        return None

    def get_dimension_poles(
        self,
        dimension: int,
        top_n: int = 5,
    ) -> Dict[str, List[Tuple[str, float]]]:
        """Get artists at extreme ends of a dimension."""
        if self.embeddings is None:
            raise ValueError("Embeddings not built.")

        if dimension < 0 or dimension >= self.embeddings.shape[1]:
            raise ValueError(f"Dimension must be 0 to {self.embeddings.shape[1] - 1}")

        dim_values = self.embeddings[:, dimension]

        top_indices = np.argsort(dim_values)[-top_n:][::-1]
        bottom_indices = np.argsort(dim_values)[:top_n]

        return {
            "positive": [(self.idx_to_artist[i], float(dim_values[i])) for i in top_indices],
            "negative": [(self.idx_to_artist[i], float(dim_values[i])) for i in bottom_indices],
        }

    def get_explained_variance(self) -> np.ndarray:
        """Get explained variance ratio for each dimension."""
        if self.svd_model is None:
            raise ValueError("Embeddings not built.")
        return self.svd_model.explained_variance_ratio_

    def save(self, cache_name: str = "critics_embeddings_2011-2025") -> None:
        """Save embeddings to cache."""
        cache_path = self.cache_dir / f"{cache_name}.pkl"

        cache_data = {
            "embeddings": self.embeddings,
            "artist_to_idx": self.artist_to_idx,
            "idx_to_artist": self.idx_to_artist,
            "svd_model": self.svd_model,
            "years_covered": self.years_covered,
        }

        with open(cache_path, "wb") as f:
            pickle.dump(cache_data, f)

        print(f"Saved critics embeddings to: {cache_path}")

    def load(self, cache_name: str = "critics_embeddings_2011-2025") -> bool:
        """Load embeddings from cache."""
        cache_path = self.cache_dir / f"{cache_name}.pkl"

        if not cache_path.exists():
            return False

        with open(cache_path, "rb") as f:
            cache_data = pickle.load(f)

        self.embeddings = cache_data["embeddings"]
        self.artist_to_idx = cache_data["artist_to_idx"]
        self.idx_to_artist = cache_data["idx_to_artist"]
        self.svd_model = cache_data["svd_model"]
        self.years_covered = cache_data.get("years_covered", [])

        print(f"Loaded critics embeddings from cache: {cache_path}")
        print(f"  {len(self.artist_to_idx)} artists, {self.embeddings.shape[1]} dimensions")

        return True


def get_or_build_critics_embeddings(
    force_rebuild: bool = False,
    min_critics: int = 2,
    use_rank_weights: bool = True,
    max_year: Optional[int] = None,
) -> CriticsEmbeddings:
    """Get or build critics embeddings.

    Args:
        force_rebuild: Force rebuild even if cached
        min_critics: Minimum critics for an artist to be included
        use_rank_weights: Weight co-occurrences by rank position
        max_year: Maximum year to include (for time-bounded holdout evaluation)
                  If None, uses all years (2011-2025)

    Returns:
        CriticsEmbeddings instance
    """
    from . import crossref
    from pathlib import Path
    import json

    def critics_loader(year: int) -> dict:
        """Load critics data for a year."""
        json_path = Path(__file__).parent.parent / f"critics-{year}.json"
        if not json_path.exists():
            raise IOError(f"No critics data for {year}")
        with open(json_path) as f:
            return {"raw": json.load(f)}

    embeddings = CriticsEmbeddings()
    rank_suffix = "_ranked" if use_rank_weights else ""

    # Determine year range
    if max_year is None:
        years = list(range(2011, 2026))
        year_range = "2011-2025"
    else:
        years = list(range(2011, max_year + 1))
        year_range = f"2011-{max_year}"

    cache_name = f"critics_embeddings_{year_range}_min{min_critics}{rank_suffix}"

    if not force_rebuild and embeddings.load(cache_name=cache_name):
        return embeddings

    # Build from scratch
    embeddings.build_from_critics_data(
        critics_loader=critics_loader,
        years=years,
        n_components=50,
        min_critics=min_critics,
        use_rank_weights=use_rank_weights,
    )
    embeddings.save(cache_name=cache_name)

    return embeddings


class CriticVectorEmbeddings:
    """Embed critics as vectors in the same space as artists.

    Each critic is represented by the weighted average of their picked artists'
    embeddings. This enables:
    - Finding critics most similar to your taste-vector
    - Tracking critic drift over time
    - Weighting recommendations by critic-to-user similarity
    """

    def __init__(self, artist_embeddings: CriticsEmbeddings):
        """Initialize with artist embeddings in critics-space.

        Args:
            artist_embeddings: CriticsEmbeddings instance with built embeddings
        """
        self.artist_embeddings = artist_embeddings
        self.critic_vectors: Dict[str, np.ndarray] = {}
        self.critic_metadata: Dict[str, dict] = {}  # critic -> {years, album_count, ...}
        self.yearly_vectors: Dict[str, Dict[int, np.ndarray]] = {}  # critic -> {year -> vector}

    def build_from_critics_data(
        self,
        critics_loader,  # Callable[[int], dict]
        years: Optional[List[int]] = None,
    ) -> None:
        """Build critic vectors from their list picks.

        Each critic's vector is the rank-weighted average of their picked artists.
        Also builds per-year vectors for drift detection.

        Args:
            critics_loader: Function that takes a year and returns critics data
            years: Years to include (default: 2011-2025)
        """
        from . import crossref

        if years is None:
            years = list(range(2011, 2026))

        if self.artist_embeddings.embeddings is None:
            raise ValueError("Artist embeddings must be built first")

        print(f"Building critic vectors from {len(years)} years...")

        # Collect all picks per critic, per year
        critic_picks: Dict[str, Dict[int, List[Tuple[str, float]]]] = {}
        # critic -> year -> [(norm_artist, weight), ...]

        for year in years:
            try:
                data = critics_loader(year)
                raw = data.get("raw", data)

                for lst in raw:
                    critic = lst.get("critic", "unknown")
                    if critic not in critic_picks:
                        critic_picks[critic] = {}
                    if year not in critic_picks[critic]:
                        critic_picks[critic][year] = []

                    list_length = len(lst.get("albums", []))

                    for album in lst.get("albums", []):
                        artist = album.get("artist", "")
                        rank = album.get("rank", 50)

                        if artist:
                            norm_artist = crossref.normalize_for_matching(artist)
                            # Only include artists we have embeddings for
                            if norm_artist in self.artist_embeddings.artist_to_idx:
                                # Rank weight: #1 → 1.0, #10 → 0.30, #50 → 0.18
                                weight = 1.0 / np.log2(rank + 1)
                                # List length normalization
                                weight *= 1.0 / np.log2(list_length + 1)
                                critic_picks[critic][year].append((norm_artist, weight))
            except (IOError, json.JSONDecodeError, KeyError):
                continue

        # Build vectors for each critic
        n_dims = self.artist_embeddings.embeddings.shape[1]

        for critic, years_picks in critic_picks.items():
            # Aggregate across all years for overall vector
            all_picks: List[Tuple[str, float]] = []
            yearly_vectors: Dict[int, np.ndarray] = {}

            for year, picks in years_picks.items():
                all_picks.extend(picks)

                # Build per-year vector
                if picks:
                    year_vector = self._weighted_average_vector(picks)
                    if year_vector is not None:
                        yearly_vectors[year] = year_vector

            if all_picks:
                overall_vector = self._weighted_average_vector(all_picks)
                if overall_vector is not None:
                    self.critic_vectors[critic] = overall_vector
                    self.yearly_vectors[critic] = yearly_vectors
                    self.critic_metadata[critic] = {
                        "years": sorted(years_picks.keys()),
                        "album_count": len(all_picks),
                        "year_count": len(years_picks),
                    }

        print(f"  Built vectors for {len(self.critic_vectors)} critics")
        print(f"  Critics with multi-year data: {sum(1 for m in self.critic_metadata.values() if m['year_count'] > 1)}")

    def _weighted_average_vector(
        self,
        picks: List[Tuple[str, float]],
    ) -> Optional[np.ndarray]:
        """Compute weighted average of artist vectors."""
        if not picks:
            return None

        vectors = []
        weights = []

        for norm_artist, weight in picks:
            vec = self.artist_embeddings.get_embedding(norm_artist)
            if vec is not None:
                vectors.append(vec)
                weights.append(weight)

        if not vectors:
            return None

        vectors = np.array(vectors)
        weights = np.array(weights)
        weights = weights / weights.sum()  # Normalize weights

        avg_vector = np.average(vectors, axis=0, weights=weights)
        # L2 normalize for cosine similarity
        avg_vector = avg_vector / np.linalg.norm(avg_vector)

        return avg_vector

    def find_similar_critics(
        self,
        user_vector: np.ndarray,
        top_n: int = 20,
    ) -> List[Tuple[str, float, dict]]:
        """Find critics most similar to a user's taste vector.

        Args:
            user_vector: User's taste vector (same dimensionality as critic vectors)
            top_n: Number of critics to return

        Returns:
            List of (critic_name, similarity, metadata) tuples
        """
        if not self.critic_vectors:
            raise ValueError("Critic vectors not built yet")

        # Normalize user vector
        user_vector = user_vector / np.linalg.norm(user_vector)

        similarities = []
        for critic, vector in self.critic_vectors.items():
            sim = np.dot(user_vector, vector)
            similarities.append((critic, float(sim), self.critic_metadata[critic]))

        # Sort by similarity descending
        similarities.sort(key=lambda x: -x[1])

        return similarities[:top_n]

    def compute_user_vector(
        self,
        df: "pd.DataFrame",
        top_n_artists: int = 100,
    ) -> np.ndarray:
        """Compute a user's taste vector from their listening history.

        The user vector is the play-weighted average of their top artists'
        embeddings in critics-space.

        Args:
            df: DataFrame with scrobble data
            top_n_artists: Number of top artists to use

        Returns:
            User's taste vector
        """
        from . import crossref

        # Get top artists by play count
        artist_plays = df.groupby("artist").size().sort_values(ascending=False)
        top_artists = artist_plays.head(top_n_artists)

        vectors = []
        weights = []

        for artist, plays in top_artists.items():
            norm_artist = crossref.normalize_for_matching(artist)
            vec = self.artist_embeddings.get_embedding(norm_artist)
            if vec is not None:
                vectors.append(vec)
                # Log-scale plays to avoid extreme weights
                weights.append(np.log1p(plays))

        if not vectors:
            raise ValueError("No artists found in critics embeddings")

        vectors = np.array(vectors)
        weights = np.array(weights)
        weights = weights / weights.sum()

        user_vector = np.average(vectors, axis=0, weights=weights)
        user_vector = user_vector / np.linalg.norm(user_vector)

        return user_vector

    def detect_critic_drift(
        self,
        critic: str,
        user_vector: np.ndarray,
    ) -> List[Tuple[int, float]]:
        """Detect how a critic's alignment with you has changed over time.

        Args:
            critic: Critic name
            user_vector: Your taste vector

        Returns:
            List of (year, similarity) tuples showing alignment over time
        """
        if critic not in self.yearly_vectors:
            return []

        user_vector = user_vector / np.linalg.norm(user_vector)

        drift = []
        for year, vector in sorted(self.yearly_vectors[critic].items()):
            sim = float(np.dot(user_vector, vector))
            drift.append((year, sim))

        return drift

    def get_critic_vector(self, critic: str) -> Optional[np.ndarray]:
        """Get a critic's taste vector."""
        return self.critic_vectors.get(critic)


def get_or_build_critic_vectors(
    force_rebuild: bool = False,
) -> CriticVectorEmbeddings:
    """Get or build critic vector embeddings.

    Args:
        force_rebuild: Force rebuild even if cached

    Returns:
        CriticVectorEmbeddings instance
    """
    import json
    from pathlib import Path

    # First get/build the artist embeddings
    artist_emb = get_or_build_critics_embeddings(force_rebuild=force_rebuild)

    def critics_loader(year: int) -> dict:
        json_path = Path(__file__).parent.parent / f"critics-{year}.json"
        if not json_path.exists():
            raise IOError(f"No critics data for {year}")
        with open(json_path) as f:
            return {"raw": json.load(f)}

    critic_vectors = CriticVectorEmbeddings(artist_emb)
    critic_vectors.build_from_critics_data(critics_loader)

    return critic_vectors
