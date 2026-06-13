import json
import inspect
import itertools

import numpy as np
import pandas as pd
import pytest

from lastfm.change_points import (
    ChangePointSpec,
    PrefixSSE,
    bin_artist_counts,
    detect_change_points,
    optimal_partition,
    transform_vectors,
)


def plays(monthly_artists):
    rows = []
    for month, artists in enumerate(monthly_artists, 1):
        for day, artist in enumerate(artists, 1):
            rows.append(
                {
                    "timestamp": pd.Timestamp(2024, month, min(day, 28), tz="UTC"),
                    "artist": artist,
                }
            )
    return pd.DataFrame(rows)


def test_spec_rejects_invalid_values_and_bool_or_fractional_counts():
    for kwargs in (
        {"frequency": "day"},
        {"vector_mode": "raw"},
        {"penalty_multiplier": float("nan")},
        {"penalty_multiplier": 0},
    ):
        with pytest.raises(ValueError):
            ChangePointSpec(**kwargs)
    for field in ("top_artists", "min_segment_bins", "top_deltas"):
        for value in (True, 1.5, 0):
            with pytest.raises(ValueError):
                ChangePointSpec(**{field: value})


def test_computed_penalty_must_remain_finite():
    with pytest.raises(ValueError, match="computed penalty"):
        detect_change_points(
            plays([["A"]] * 3 + [["B"]] * 3),
            ChangePointSpec(min_segment_bins=2, penalty_multiplier=1.7e308),
        )


def test_spec_rejects_huge_integer_penalty_cleanly():
    with pytest.raises(ValueError, match="penalty_multiplier"):
        ChangePointSpec(penalty_multiplier=10**10000)


def test_nat_timestamp_is_rejected_cleanly():
    frame = pd.DataFrame({"timestamp": [pd.NaT], "artist": ["A"]})
    with pytest.raises(ValueError, match="NaT"):
        detect_change_points(frame, ChangePointSpec(min_segment_bins=1))


def test_month_bins_are_utc_continuous_and_reconcile_with_other():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2024-01-31T23:00Z", "2024-03-01T00:00Z", "2024-03-02T00:00Z"]
            ),
            "artist": ["B", "A", "C"],
        }
    )
    binned = bin_artist_counts(
        frame, ChangePointSpec(top_artists=1, min_segment_bins=1)
    )
    assert binned.timestamps == [
        "2024-01-01T00:00:00Z",
        "2024-02-01T00:00:00Z",
        "2024-03-01T00:00:00Z",
    ]
    assert binned.artists == ["A", "__OTHER__"]
    assert binned.counts.tolist() == [[0, 1], [0, 0], [1, 1]]
    assert int(binned.counts.sum()) == len(frame)


def test_real_artist_named_other_is_escaped_from_synthetic_bucket():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                [
                    "2024-01-01T00:00Z",
                    "2024-01-02T00:00Z",
                    "2024-01-03T00:00Z",
                    "2024-02-01T00:00Z",
                ]
            ),
            "artist": ["__OTHER__", "__OTHER__", "A", "B"],
        }
    )
    binned = bin_artist_counts(
        frame, ChangePointSpec(top_artists=2, min_segment_bins=1)
    )
    assert binned.artists == ["\\__OTHER__", "A", "__OTHER__"]
    assert binned.counts.tolist() == [[2, 1, 0], [0, 0, 1]]
    assert int(binned.counts.sum()) == len(frame)


def test_week_bins_use_iso_monday_and_vocabulary_ties_are_name_ascending():
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(
                ["2024-01-07T23:59Z", "2024-01-08T00:00Z", "2024-01-21T12:00Z"]
            ),
            "artist": ["B", "A", "C"],
        }
    )
    binned = bin_artist_counts(
        frame, ChangePointSpec(frequency="week", top_artists=2, min_segment_bins=1)
    )
    assert binned.timestamps == [
        "2024-01-01T00:00:00Z",
        "2024-01-08T00:00:00Z",
        "2024-01-15T00:00:00Z",
    ]
    assert binned.artists == ["A", "B", "__OTHER__"]


def test_transform_vectors_matches_hand_calculations():
    counts = np.array([[3.0, 1.0], [0.0, 0.0]])
    shares, metadata = transform_vectors(counts, "shares")
    np.testing.assert_allclose(shares[0], [np.sqrt(0.75), 0.5])
    np.testing.assert_array_equal(shares[1], [0, 0])
    assert metadata["transformation"] == "sqrt_artist_share"

    count_vectors, metadata = transform_vectors(
        np.array([[0.0, 1.0], [0.0, 3.0]]), "counts"
    )
    np.testing.assert_array_equal(count_vectors[:, 0], [0, 0])
    np.testing.assert_allclose(count_vectors[:, 1], [-1, 1])
    assert metadata["standardization"] == "population"


def test_prefix_sse_matches_direct_cost():
    matrix = np.array([[1.0, 2.0], [2.0, 4.0], [7.0, 1.0], [8.0, 3.0]])
    prefix = PrefixSSE(matrix)
    direct = ((matrix[1:4] - matrix[1:4].mean(axis=0)) ** 2).sum()
    assert prefix.cost(1, 4) == pytest.approx(direct)


def test_exact_dp_finds_two_and_three_regimes_and_respects_minimum_lengths():
    two = np.vstack([np.zeros((3, 2)), np.full((3, 2), 4.0)])
    assert optimal_partition(two, penalty=1, min_segment_bins=3)[0] == [3]
    three = np.vstack([np.zeros((2, 1)), np.full((2, 1), 4.0), np.full((2, 1), -4.0)])
    assert optimal_partition(three, penalty=1, min_segment_bins=2)[0] == [2, 4]
    assert all(b - a >= 2 for a, b in zip([0, 2, 4], [2, 4, 6]))


def test_dp_ties_choose_fewer_then_lexicographically_earliest_boundaries():
    constant = np.zeros((6, 1))
    assert optimal_partition(constant, penalty=0, min_segment_bins=2)[0] == []
    alternating = np.array([[0.0], [0.0], [1.0], [1.0], [0.0], [0.0]])
    boundaries, _ = optimal_partition(alternating, penalty=-0.5, min_segment_bins=2)
    assert boundaries == [2, 4]


def _exhaustive_partition(matrix, penalty, minimum):
    n = len(matrix)
    cost = PrefixSSE(matrix)
    candidates = []
    for size in range(n):
        for boundaries in itertools.combinations(range(1, n), size):
            edges = (0, *boundaries, n)
            if any(right - left < minimum for left, right in zip(edges, edges[1:])):
                continue
            objective = (
                sum(cost.cost(left, right) for left, right in zip(edges, edges[1:]))
                + penalty * size
            )
            candidates.append((objective, boundaries))
    best_value = min(value for value, _ in candidates)
    tied = [boundaries for value, boundaries in candidates if value == best_value]
    best_boundaries = min(tied, key=lambda value: (len(value), value))
    return list(best_boundaries), best_value


def test_dp_matches_exhaustive_randomized_small_series():
    rng = np.random.default_rng(20240612)
    for n in range(2, 9):
        for _ in range(20):
            matrix = rng.integers(-2, 3, size=(n, 3)).astype(float)
            minimum = int(rng.integers(1, n // 2 + 1))
            penalty = float(rng.choice([0.0, 0.25, 1.0, 3.0]))
            expected_boundaries, expected_objective = _exhaustive_partition(
                matrix, penalty, minimum
            )
            boundaries, objective = optimal_partition(matrix, penalty, minimum)
            assert boundaries == expected_boundaries
            assert objective == pytest.approx(expected_objective)


def test_dp_exact_objective_ordering_handles_scaled_counterexample():
    matrix = np.array([[-1e-6, -2e-6], [-1e-6, 0.0], [1e-6, -1e-6]])
    boundaries, objective = optimal_partition(matrix, penalty=1e-12, min_segment_bins=1)
    assert boundaries == [1, 2]
    assert objective == 2e-12


def test_dp_matches_exact_exhaustive_ordering_for_small_floating_series():
    rng = np.random.default_rng(8675309)
    for scale in (1e-7, 1.0, 1e7):
        for n in range(3, 8):
            for _ in range(12):
                matrix = rng.normal(size=(n, 2)) * scale
                minimum = int(rng.integers(1, n // 2 + 1))
                penalty = float(rng.choice([0.0, 0.1, 2.0])) * scale**2
                expected_boundaries, expected_objective = _exhaustive_partition(
                    matrix, penalty, minimum
                )
                boundaries, objective = optimal_partition(matrix, penalty, minimum)
                assert boundaries == expected_boundaries
                assert objective == pytest.approx(expected_objective)


def test_dp_uses_scalar_backpointers_not_stored_boundary_tuples():
    source = inspect.getsource(optimal_partition)
    assert "predecessors" in source
    assert "tuple[int, ...]" not in source
    assert "boundaries = best[start]" not in source


def test_constant_series_short_circuits_length_validation_and_nonconstant_does_not():
    constant = plays([["A"]] * 3)
    result = detect_change_points(constant, ChangePointSpec(min_segment_bins=6))
    assert result["diagnostics"]["constant_series"] is True
    assert result["change_points"] == []
    with pytest.raises(ValueError, match="at least 12 bins"):
        detect_change_points(
            plays([["A"], ["B"], ["A"]]), ChangePointSpec(min_segment_bins=6)
        )


def test_schema_reconciles_segments_deltas_and_has_no_interpretive_fields():
    frame = plays([["A"]] * 3 + [["B", "B"]] * 3)
    result = detect_change_points(
        frame,
        ChangePointSpec(min_segment_bins=2, penalty_multiplier=0.01, top_deltas=1),
    )
    assert result["schema_version"] == 1
    assert result["change_points"][0]["timestamp"] == "2024-04-01T00:00:00Z"
    assert result["change_points"][0]["left_segment_id"] == 1
    assert result["change_points"][0]["right_segment_id"] == 2
    assert result["change_points"][0]["top_artist_share_deltas"][0]["artist"] in {
        "A",
        "B",
    }
    assert sum(segment["plays"] for segment in result["segments"]) == len(frame)
    assert result["timezone"] == "UTC"
    assert result["vector"]["frequency"] == "month"
    assert result["vector"]["mode"] == "shares"
    assert result["change_points"][0]["bin_index"] == 3
    assert result["segments"][0]["end_exclusive"] == "2024-04-01T00:00:00Z"
    assert result["diagnostics"]["total_bins"] == 6
    assert result["diagnostics"]["low_volume_bins"][0]["plays"] < 10
    forbidden = {"name", "label", "location", "genre", "mood", "cause"}

    def keys(value):
        if isinstance(value, dict):
            return set(value).union(*(keys(v) for v in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(v) for v in value)) if value else set()
        return set()

    assert not (keys(result) & forbidden)
    assert json.dumps(result, allow_nan=False, sort_keys=True)


def test_analysis_is_stable_under_row_shuffle_and_penalty_is_monotone():
    frame = plays([["A"]] * 3 + [["B"]] * 3 + [["A"]] * 3)
    low = detect_change_points(
        frame, ChangePointSpec(min_segment_bins=2, penalty_multiplier=0.01)
    )
    high = detect_change_points(
        frame, ChangePointSpec(min_segment_bins=2, penalty_multiplier=100)
    )
    shuffled = detect_change_points(
        frame.sample(frac=1, random_state=3),
        ChangePointSpec(min_segment_bins=2, penalty_multiplier=0.01),
    )
    assert len(high["change_points"]) <= len(low["change_points"])
    assert json.dumps(low, sort_keys=True) == json.dumps(shuffled, sort_keys=True)
