"""Pure, deterministic change-point measurements for listening history."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ChangePointSpec:
    frequency: str = "month"
    vector_mode: str = "shares"
    top_artists: int = 100
    min_segment_bins: int = 6
    penalty_multiplier: float = 1.0
    top_deltas: int = 20

    def __post_init__(self) -> None:
        if self.frequency not in {"week", "month"}:
            raise ValueError("frequency must be week or month")
        if self.vector_mode not in {"shares", "counts"}:
            raise ValueError("vector_mode must be shares or counts")
        for field in ("top_artists", "min_segment_bins", "top_deltas"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field} must be a positive integer")
        value = self.penalty_multiplier
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ValueError("penalty_multiplier must be finite and positive")


@dataclass(frozen=True)
class BinnedCounts:
    timestamps: list[str]
    starts: tuple[pd.Timestamp, ...]
    artists: list[str]
    counts: np.ndarray
    raw: pd.DataFrame


def _utc_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _bin_start(timestamp: pd.Timestamp, frequency: str) -> pd.Timestamp:
    timestamp = _utc_timestamp(timestamp).normalize()
    if frequency == "month":
        return timestamp.replace(day=1)
    return timestamp - pd.Timedelta(days=timestamp.weekday())


def _next_bin(timestamp: pd.Timestamp, frequency: str) -> pd.Timestamp:
    return timestamp + (
        pd.offsets.MonthBegin(1) if frequency == "month" else pd.Timedelta(days=7)
    )


def _iso(timestamp: pd.Timestamp) -> str:
    value = _utc_timestamp(timestamp).isoformat()
    return value.replace("+00:00", "Z")


def bin_artist_counts(frame: pd.DataFrame, spec: ChangePointSpec) -> BinnedCounts:
    if frame.empty:
        raise ValueError("listening history must contain at least one play")
    if not {"timestamp", "artist"}.issubset(frame.columns):
        raise ValueError("listening history requires timestamp and artist columns")
    raw = frame[["timestamp", "artist"]].copy()
    raw["timestamp"] = raw["timestamp"].map(_utc_timestamp)
    raw["artist"] = raw["artist"].fillna("").astype(str)
    raw["bin"] = raw["timestamp"].map(lambda value: _bin_start(value, spec.frequency))
    first, last = raw["bin"].min(), raw["bin"].max()
    starts: list[pd.Timestamp] = []
    current = first
    while current <= last:
        starts.append(current)
        current = _next_bin(current, spec.frequency)

    totals = raw.groupby("artist", sort=False).size().items()
    vocabulary = [
        name
        for name, _ in sorted(totals, key=lambda item: (-item[1], item[0]))[
            : spec.top_artists
        ]
    ]
    artists = vocabulary + ["__OTHER__"]
    row_index = {value: index for index, value in enumerate(starts)}
    column_index = {value: index for index, value in enumerate(vocabulary)}
    counts = np.zeros((len(starts), len(artists)), dtype=np.int64)
    for (bin_start, artist), count in (
        raw.groupby(["bin", "artist"], sort=False).size().items()
    ):
        counts[row_index[bin_start], column_index.get(artist, len(vocabulary))] += int(
            count
        )
    return BinnedCounts(
        [_iso(value) for value in starts], tuple(starts), artists, counts, raw
    )


def transform_vectors(
    counts: np.ndarray, mode: str
) -> tuple[np.ndarray, dict[str, Any]]:
    values = np.asarray(counts, dtype=float)
    if mode == "shares":
        totals = values.sum(axis=1, keepdims=True)
        shares = np.divide(values, totals, out=np.zeros_like(values), where=totals != 0)
        return np.sqrt(shares), {
            "transformation": "sqrt_artist_share",
            "distance": "hellinger_scaled_euclidean",
        }
    if mode != "counts":
        raise ValueError("mode must be shares or counts")
    logged = np.log1p(values)
    means = logged.mean(axis=0)
    standard_deviations = logged.std(axis=0, ddof=0)
    vectors = np.divide(
        logged - means,
        standard_deviations,
        out=np.zeros_like(logged),
        where=standard_deviations != 0,
    )
    return vectors, {"transformation": "log1p", "standardization": "population"}


class PrefixSSE:
    def __init__(self, matrix: np.ndarray):
        self.matrix = np.asarray(matrix, dtype=float)
        self.sums = np.vstack(
            [np.zeros(self.matrix.shape[1]), np.cumsum(self.matrix, axis=0)]
        )
        self.squares = np.concatenate(
            [[0.0], np.cumsum(np.sum(self.matrix * self.matrix, axis=1))]
        )

    def cost(self, start: int, end: int) -> float:
        length = end - start
        total = self.sums[end] - self.sums[start]
        return max(
            0.0,
            float(
                self.squares[end] - self.squares[start] - np.dot(total, total) / length
            ),
        )


def _better(
    candidate: tuple[float, tuple[int, ...]],
    incumbent: tuple[float, tuple[int, ...]] | None,
) -> bool:
    if incumbent is None or candidate[0] < incumbent[0] - 1e-12:
        return True
    if abs(candidate[0] - incumbent[0]) <= 1e-12:
        return (len(candidate[1]), candidate[1]) < (len(incumbent[1]), incumbent[1])
    return False


def optimal_partition(
    matrix: np.ndarray, penalty: float, min_segment_bins: int
) -> tuple[list[int], float]:
    values = np.asarray(matrix, dtype=float)
    n = len(values)
    costs = PrefixSSE(values)
    best: list[tuple[float, tuple[int, ...]] | None] = [None] * (n + 1)
    best[0] = (0.0, ())
    for end in range(min_segment_bins, n + 1):
        incumbent = None
        for start in range(0, end - min_segment_bins + 1):
            if best[start] is None or (start and start < min_segment_bins):
                continue
            boundaries = best[start][1] + ((start,) if start else ())
            candidate = (
                best[start][0] + costs.cost(start, end) + (penalty if start else 0.0),
                boundaries,
            )
            if _better(candidate, incumbent):
                incumbent = candidate
        best[end] = incumbent
    if best[n] is None:
        raise ValueError("no valid partition")
    return list(best[n][1]), float(best[n][0])


def _noise_variance(vectors: np.ndarray, active_dims: int) -> float:
    if len(vectors) < 2 or active_dims == 0:
        return 0.0
    estimates = np.sum(np.diff(vectors, axis=0) ** 2, axis=1) / (2 * active_dims)
    positive = estimates[estimates > 0]
    if not len(positive):
        return 0.0
    median = float(np.median(estimates))
    return median if median > 0 else float(positive.min())


def _rounded(value: float) -> float:
    return round(float(value), 10)


def analyze_change_points(
    frame: pd.DataFrame, spec: ChangePointSpec | None = None
) -> dict[str, Any]:
    spec = spec or ChangePointSpec()
    binned = bin_artist_counts(frame, spec)
    vectors, vector_metadata = transform_vectors(binned.counts, spec.vector_mode)
    constant = bool(np.allclose(vectors, vectors[0], atol=1e-12, rtol=0))
    n = len(vectors)
    if not constant and n < 2 * spec.min_segment_bins:
        raise ValueError(f"analysis requires at least {2 * spec.min_segment_bins} bins")
    active_dims = int(np.count_nonzero(np.var(vectors, axis=0) > 0))
    variance = _noise_variance(vectors, active_dims)
    penalty = (
        float(spec.penalty_multiplier * variance * active_dims * math.log(n))
        if n
        else 0.0
    )
    if not math.isfinite(penalty):
        raise ValueError("computed penalty must be finite; reduce penalty_multiplier")
    boundaries, objective = (
        ([], PrefixSSE(vectors).cost(0, n))
        if constant
        else optimal_partition(vectors, penalty, spec.min_segment_bins)
    )
    edges = [0, *boundaries, n]
    row_totals = binned.counts.sum(axis=1)

    segments = []
    centroids = []
    share_centroids = []
    for segment_id, (start, end) in enumerate(zip(edges, edges[1:]), 1):
        counts = binned.counts[start:end].sum(axis=0)
        total = int(counts.sum())
        shares = counts / total if total else np.zeros(len(counts))
        centroids.append(vectors[start:end].mean(axis=0))
        share_centroids.append(shares)
        raw_start = binned.starts[start]
        raw_end = _next_bin(binned.starts[end - 1], spec.frequency)
        selected = binned.raw[
            (binned.raw["timestamp"] >= raw_start) & (binned.raw["timestamp"] < raw_end)
        ]
        ranking = sorted(
            zip(binned.artists, shares), key=lambda item: (-item[1], item[0])
        )
        segments.append(
            {
                "id": segment_id,
                "start": _iso(raw_start),
                "end_exclusive": _iso(raw_end),
                "bins": end - start,
                "plays": total,
                "plays_per_bin": _rounded(total / (end - start)),
                "unique_artists": int(selected["artist"].nunique()),
                "empty_bins": int(np.count_nonzero(row_totals[start:end] == 0)),
                "top_artist_shares": [
                    {"artist": artist, "share": _rounded(share)}
                    for artist, share in ranking[: spec.top_deltas]
                    if share > 0
                ],
            }
        )

    changes = []
    for number, boundary in enumerate(boundaries, 1):
        left, right = number - 1, number
        deltas = share_centroids[right] - share_centroids[left]
        ranking = sorted(
            zip(binned.artists, deltas), key=lambda item: (-abs(item[1]), item[0])
        )
        changes.append(
            {
                "number": number,
                "timestamp": binned.timestamps[boundary],
                "bin_index": boundary,
                "left_segment_id": number,
                "right_segment_id": number + 1,
                "centroid_distance": _rounded(
                    np.linalg.norm(centroids[right] - centroids[left])
                ),
                "plays_per_bin_delta": _rounded(
                    segments[right]["plays_per_bin"] - segments[left]["plays_per_bin"]
                ),
                "top_artist_share_deltas": [
                    {"artist": artist, "delta": _rounded(delta)}
                    for artist, delta in ranking[: spec.top_deltas]
                    if abs(delta) > 1e-15
                ],
            }
        )

    empty_bins = [
        {"timestamp": binned.timestamps[i], "plays": 0}
        for i, value in enumerate(row_totals)
        if value == 0
    ]
    low_volume = [
        {"timestamp": binned.timestamps[i], "plays": int(value)}
        for i, value in enumerate(row_totals)
        if value < 10
    ]
    return {
        "schema_version": 1,
        "timezone": "UTC",
        "parameters": asdict(spec),
        "vector": {
            "frequency": spec.frequency,
            "mode": spec.vector_mode,
            "artists": binned.artists,
            "dimensions": len(binned.artists),
            "active_dimensions": active_dims,
            **vector_metadata,
        },
        "model": {
            "algorithm": "exact_multivariate_penalized_sse_dp",
            "noise_variance": _rounded(variance),
            "penalty": _rounded(penalty),
            "objective": _rounded(objective),
        },
        "change_points": changes,
        "segments": segments,
        "diagnostics": {
            "empty_bins": empty_bins,
            "low_volume_bins": low_volume,
            "constant_series": constant,
            "total_bins": n,
        },
    }


def detect_change_points(
    frame: pd.DataFrame, spec: ChangePointSpec | None = None
) -> dict[str, Any]:
    """Public alias for the change-point analysis contract."""
    return analyze_change_points(frame, spec)
