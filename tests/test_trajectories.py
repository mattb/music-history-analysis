import json

import pandas as pd
import pytest

from lastfm.trajectories import (
    artist_trajectories,
    artist_trajectory,
    cohort_retention,
)


def history(items):
    frame = pd.DataFrame(items, columns=["timestamp", "artist"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


def test_monthly_trajectory_is_dense_and_preserves_exact_timestamps():
    frame = history(
        [
            ("2024-01-31T23:59:59Z", "A"),
            ("2024-03-01T12:34:56Z", "a"),
            ("2024-03-02T01:02:03Z", "A"),
        ]
    )
    result = artist_trajectory(frame, "A", start="2023-12", end="2024-04")
    assert [row["plays"] for row in result["timeline"]] == [0, 1, 0, 2, 0]
    assert result["summary"] == {
        "total_plays": 3,
        "observed_periods": 5,
        "active_periods": 2,
        "active_share": 0.4,
        "active_span_periods": 3,
        "active_span_share": pytest.approx(2 / 3),
        "first_play": "2024-01-31T23:59:59+00:00",
        "last_play": "2024-03-02T01:02:03+00:00",
    }
    assert result["observation"]["leading_inactivity_periods"] == 1
    assert result["observation"]["trailing_inactivity_periods"] == 1


def test_yearly_matching_display_ties_peak_ties_and_ramp_ols():
    frame = history(
        [
            ("2020-02-29", "beta"),
            ("2022-01-01", "Beta"),
            ("2022-12-31", "Beta"),
            ("2023-01-01", "BETA"),
            ("2023-06-01", "beta"),
        ]
    ).sample(frac=1, random_state=4)
    result = artist_trajectory(frame, "BeTa", granularity="year")
    assert result["artist"] == "Beta"
    assert result["peak"] == {
        "plays": 2,
        "periods": ["2022", "2023"],
        "primary_period": "2022",
    }
    assert result["ramp"]["period_distance"] == 2
    assert result["ramp"]["first_period_plays"] == 1
    assert result["ramp"]["play_change"] == 1
    assert result["ramp"]["mean_period_change"] == 0.5
    assert result["ramp"]["ols_slope"] == pytest.approx(0.5)
    assert json.dumps(result, allow_nan=False)


def test_batch_preserves_query_order_and_not_found_is_structured():
    frame = history([("2024-01-01", "Alpha"), ("2024-01-02", "Alphabet")])
    result = artist_trajectories(frame, ["Alphabet", "alp", "ALPHA"])
    assert [item["query_artist"] for item in result["artists"]] == [
        "Alphabet",
        "alp",
        "ALPHA",
    ]
    assert [item["status"] for item in result["artists"]] == ["ok", "not_found", "ok"]


def test_minimum_activity_threshold_changes_active_metrics_not_play_counts():
    frame = history([("2024-01-01", "A"), ("2024-02-01", "A"), ("2024-02-02", "A")])
    result = artist_trajectory(frame, "A", min_period_plays=2)
    assert [row["active"] for row in result["timeline"]] == [False, True]
    assert result["summary"]["total_plays"] == 3
    assert result["summary"]["active_periods"] == 1
    assert result["ramp"]["first_period"] == "2024-02"
    assert result["ramp"]["period_distance"] == 0


def test_ramp_is_null_when_no_period_reaches_activity_threshold():
    result = artist_trajectory(
        history([("2024-01-01", "A"), ("2024-02-01", "A")]),
        "A",
        min_period_plays=2,
    )
    assert result["peak"]["plays"] == 1
    assert result["ramp"] is None


def test_dormancy_requires_exact_threshold_between_active_bins_and_keeps_short_gaps():
    frame = history(
        [
            ("2024-01-01", "A"),
            ("2024-03-01", "A"),  # one-bin short gap
            ("2024-06-01", "A"),  # two-bin dormant gap
            ("2024-10-01", "A"),  # three-bin dormant gap
        ]
    )
    result = artist_trajectory(
        frame, "A", start="2023-12", end="2025-01", dormancy_periods=2
    )
    episodes = result["dormancy"]["episodes"]
    assert [(e["inactive_periods"], e["return_period"]) for e in episodes] == [
        (2, "2024-06"),
        (3, "2024-10"),
    ]
    assert result["segments"] == [
        {"start_period": "2024-01", "end_period": "2024-03", "active_periods": 2},
        {"start_period": "2024-06", "end_period": "2024-06", "active_periods": 1},
        {"start_period": "2024-10", "end_period": "2024-10", "active_periods": 1},
    ]
    assert episodes[0]["plays_first_3_periods"] == 1
    assert episodes[0]["first_3_periods_complete"] is True
    assert episodes[1]["plays_first_6_periods"] == 1
    assert episodes[1]["first_6_periods_complete"] is False
    assert result["observation"]["leading_inactivity_censored"] is True
    assert result["observation"]["trailing_inactivity_censored"] is True


def test_single_play_has_null_zero_distance_ramp_and_no_dormancy():
    result = artist_trajectory(history([("2024-02-29", "A")]), "A")
    assert result["ramp"]["period_distance"] == 0
    assert result["ramp"]["mean_period_change"] is None
    assert result["ramp"]["ols_slope"] is None
    assert result["dormancy"]["episodes"] == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"granularity": "week"}, "granularity"),
        ({"start": "2024"}, "YYYY-MM"),
        ({"start": "2024-02", "end": "2024-01"}, "start"),
        ({"min_period_plays": 0}, "positive"),
        ({"dormancy_periods": 0}, "positive"),
    ],
)
def test_trajectory_validation(kwargs, message):
    with pytest.raises(ValueError, match=message):
        artist_trajectory(history([("2024-01-01", "A")]), "A", **kwargs)


def test_empty_history_and_empty_artist_batch_are_rejected():
    with pytest.raises(ValueError, match="history"):
        artist_trajectory(history([]), "A")
    with pytest.raises(ValueError, match="artist"):
        artist_trajectories(history([("2024-01-01", "A")]), [])


def test_cohort_uses_first_ever_activity_and_exact_offset_retention():
    frame = history(
        [
            ("2023-12-01", "old"),
            ("2024-01-01", "old"),
            ("2024-01-01", "A"),
            ("2024-01-02", "A"),
            ("2024-02-01", "A"),
            ("2024-04-01", "A"),
            ("2024-01-03", "B"),
            ("2024-03-01", "B"),
        ]
    )
    result = cohort_retention(
        frame,
        start="2024-01",
        end="2024-04",
        offsets=[1, 2, 3, 5, 3],
        min_discovery_plays=1,
    )
    cohort = result["cohorts"][0]
    assert cohort["cohort"] == "2024-01"
    assert cohort["cohort_size"] == 2  # old is excluded by full-history discovery
    assert cohort["first_period_plays"] == {"mean": 1.5, "median": 1.5}
    assert cohort["any_later_activity"] == {"count": 2, "rate": 1.0}
    assert cohort["cells"] == [
        {
            "offset": 1,
            "eligible_artists": 2,
            "retained_artists": 1,
            "retention_rate": 0.5,
        },
        {
            "offset": 2,
            "eligible_artists": 2,
            "retained_artists": 1,
            "retention_rate": 0.5,
        },
        {
            "offset": 3,
            "eligible_artists": 2,
            "retained_artists": 1,
            "retention_rate": 0.5,
        },
        {
            "offset": 5,
            "eligible_artists": 0,
            "retained_artists": 0,
            "retention_rate": None,
        },
    ]
    assert json.dumps(result, allow_nan=False)


def test_cohort_thresholds_year_transition_censoring_and_empty_cohorts():
    frame = history(
        [
            ("2023-12-31", "A"),
            ("2024-01-01", "A"),
            ("2023-12-01", "B"),
            ("2023-12-02", "B"),
            ("2024-01-02", "B"),
            ("2024-01-03", "B"),
        ]
    )
    result = cohort_retention(
        frame,
        start="2023-12",
        end="2024-01",
        offsets=[1, 2],
        min_discovery_plays=2,
        min_active_plays=2,
    )
    assert result["cohorts"][0]["cohort_size"] == 1
    assert result["cohorts"][0]["cells"][0]["retention_rate"] == 1.0
    assert result["cohorts"][0]["cells"][1]["retention_rate"] is None
    assert result["cohorts"][1]["cohort_size"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cohort_granularity": "week"},
        {"activity_granularity": "week"},
        {"offsets": [-1]},
        {"offsets": []},
        {"min_discovery_plays": 0},
        {"min_active_plays": 0},
    ],
)
def test_cohort_validation(kwargs):
    with pytest.raises(ValueError):
        cohort_retention(history([("2024-01-01", "A")]), **kwargs)
