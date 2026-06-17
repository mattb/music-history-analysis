"""Reusable analysis session state for Last.fm data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from . import crossref, data, embeddings


def to_serializable(obj: Any) -> Any:
    """Convert numpy types to JSON-serializable Python types."""
    if isinstance(obj, dict):
        return {k: to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_serializable(v) for v in obj]
    if isinstance(obj, set):
        return [to_serializable(v) for v in obj]
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    if isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def find_csv(cwd: Path | None = None) -> Path | None:
    """Find CSV file from environment or glob."""
    env_path = os.environ.get("MUSIC_HISTORY_CSV")
    if env_path:
        path = Path(env_path)
        if path.exists():
            return path

    base = cwd or Path.cwd()
    csvs = list(base.glob("recenttracks-*.csv"))
    if csvs:
        return sorted(csvs)[-1]

    return None


class AnalysisState:
    """Holds loaded data and computed artifacts for the session."""

    def __init__(self, critics_root: Path | None = None):
        self.csv_path: Path | None = None
        self.critics_root = critics_root or Path(__file__).parent.parent
        self.df = None  # Main scrobbles DataFrame
        self.user_embeddings = None  # ArtistEmbeddings from user listening
        self.critics_embeddings = None  # CriticsEmbeddings from critics lists
        self.critic_vectors = None  # CriticVectorEmbeddings for alignment
        self._critics_cache: dict[int, list] = {}  # year -> critics data
        # Indices for fast critic lookups (built lazily)
        self._album_critics_index: dict | None = None  # (artist_norm, album_norm) -> [{critic, publication, year, rank}]
        self._critic_picks_index: dict | None = None  # critic_name -> [{artist, album, year, rank}]
        self._listened_albums_cache: set | None = None  # Cached set of (artist, album) tuples user has heard

    def is_loaded(self) -> bool:
        return self.df is not None

    def load(self, csv_path: Path | None = None) -> None:
        """Load data from CSV and build embeddings."""
        if csv_path is None:
            csv_path = find_csv()

        if csv_path is None:
            raise ValueError(
                "No CSV found. Set MUSIC_HISTORY_CSV environment variable or "
                "place recenttracks-*.csv in the working directory."
            )

        self.csv_path = Path(csv_path)
        print(f"Loading scrobbles from {self.csv_path}...")
        self.df = data.load_scrobbles(self.csv_path)
        print(f"  Loaded {len(self.df):,} plays")

        print("Building user embeddings...")
        self._build_user_embeddings()
        if self.user_embeddings is not None:
            print(f"  Built embeddings for {len(self.user_embeddings.artist_to_idx)} artists")

        print("Building critics embeddings...")
        self._build_critics_embeddings()

        print("Building critic vectors...")
        self._build_critic_vectors()

        print("Ready!")

    def _build_user_embeddings(self) -> None:
        self.user_embeddings = embeddings.build_embeddings_from_csv(self.csv_path)

    def _build_critics_embeddings(self) -> None:
        try:
            self.critics_embeddings = embeddings.get_or_build_critics_embeddings()
            print(f"  Built critics embeddings for {len(self.critics_embeddings.artist_to_idx)} artists")
        except Exception as e:
            print(f"  Warning: Could not build critics embeddings: {e}")
            self.critics_embeddings = None

    def _build_critic_vectors(self) -> None:
        try:
            self.critic_vectors = embeddings.get_or_build_critic_vectors()
            print(f"  Built vectors for {len(self.critic_vectors.critic_vectors)} critics")
        except Exception as e:
            print(f"  Warning: Could not build critic vectors: {e}")
            self.critic_vectors = None

    def metadata(self) -> dict[str, Any]:
        if self.df is None:
            return {"loaded": False}

        return to_serializable({
            "loaded": True,
            "csv_path": str(self.csv_path),
            "plays": len(self.df),
            "artists": self.df["artist"].nunique(),
            "date_range": {
                "first": self.df["timestamp"].min().isoformat(),
                "last": self.df["timestamp"].max().isoformat(),
            },
            "critics_years": self.get_all_critics_years(),
        })

    def get_critics_data(self, year: int) -> list:
        """Load critics data for a year, with caching."""
        if year not in self._critics_cache:
            critics_path = self.critics_root / f"critics-{year}.json"
            if critics_path.exists():
                with open(critics_path) as f:
                    self._critics_cache[year] = json.load(f)
            else:
                self._critics_cache[year] = []
        return self._critics_cache[year]

    def get_all_critics_years(self) -> list[int]:
        """Get list of years with critics data available."""
        years = []
        for y in range(2011, 2026):
            path = self.critics_root / f"critics-{y}.json"
            if path.exists():
                years.append(y)
        return years

    def _build_critics_indices(self) -> None:
        """Build indices for fast critic lookups. Called lazily on first use."""
        if self._album_critics_index is not None:
            return  # Already built

        self._album_critics_index = {}  # (artist_norm, album_norm) -> [{critic, publication, year, rank}]
        self._critic_picks_index = {}  # critic_name -> [{artist, album, year, rank}]

        for year in self.get_all_critics_years():
            critics_data = self.get_critics_data(year)
            for critic_list in critics_data:
                critic = critic_list.get("critic", "Unknown")
                publication = critic_list.get("publication", "Unknown")

                if critic not in self._critic_picks_index:
                    self._critic_picks_index[critic] = {
                        "publication": publication,
                        "picks": [],
                    }

                for album in critic_list.get("albums", []):
                    artist = album.get("artist", "")
                    title = album.get("title", "")
                    rank = album.get("rank")

                    if not artist or not title:
                        continue

                    # Add to album -> critics index
                    key = (
                        crossref.normalize_for_matching(artist),
                        crossref.normalize_for_matching(title),
                    )
                    if key not in self._album_critics_index:
                        self._album_critics_index[key] = {
                            "artist": artist,
                            "album": title,
                            "critics": [],
                        }
                    self._album_critics_index[key]["critics"].append({
                        "critic": critic,
                        "publication": publication,
                        "year": year,
                        "rank": rank,
                    })

                    # Add to critic -> picks index
                    self._critic_picks_index[critic]["picks"].append({
                        "artist": artist,
                        "album": title,
                        "year": year,
                        "rank": rank,
                    })

    def get_album_critics_index(self) -> dict:
        """Get album -> critics index, building if needed."""
        self._build_critics_indices()
        return self._album_critics_index

    def get_critic_picks_index(self) -> dict:
        """Get critic -> picks index, building if needed."""
        self._build_critics_indices()
        return self._critic_picks_index

    def get_listened_albums(self, min_familiarity: float = 0.4) -> set:
        """Get set of (artist_norm, album_norm) tuples user has heard."""
        if self._listened_albums_cache is None:
            listened = data.get_albums_by_familiarity(self.df, min_familiarity=min_familiarity)
            self._listened_albums_cache = {
                (crossref.normalize_for_matching(a), crossref.normalize_for_matching(t))
                for a, t in listened
            }
        return self._listened_albums_cache
