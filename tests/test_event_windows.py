from __future__ import annotations

import json
import math
from datetime import date, datetime

import pandas as pd
import pytest

from lastfm.event_windows import (
    EventWindowSpec,
    analyze_event_window,
    build_intervals,
    compare_event_window,
)


def plays(*rows: tuple[str, str, str, str]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp(ts),
                "artist": artist,
                "album": album,
                "track": track,
            }
            for ts, artist, album, track in rows
        ]
    )


def test_spec_rejects_invalid_date_timezone_entity_and_counts():
    for kwargs in (
        {"event_date": "2024-02-01"},
        {"event_date": "2024-02-30"},
        {"event_date": "02/01/2024"},
        {"event_date": datetime(2024, 2, 1)},
        {"event_date": date(2024, 2, 1), "timezone": "Mars/Olympus"},
        {"event_date": date(2024, 2, 1), "entity": "genre"},
        {"event_date": date(2024, 2, 1), "pre_days": True},
        {"event_date": date(2024, 2, 1), "event_days": 0},
        {"event_date": date(2024, 2, 1), "post_days": 1.5},
        {"event_date": date(2024, 2, 1), "baseline_days": math.nan},
        {"event_date": date(2024, 2, 1), "top_n": -1},
    ):
        with pytest.raises(ValueError):
            EventWindowSpec(**kwargs)


def test_public_contract_accepts_date_and_exposes_utc_intervals():
    spec = EventWindowSpec(
        date(2024, 3, 10),
        timezone="America/Los_Angeles",
        pre_days=1,
        event_days=1,
        post_days=1,
        baseline_days=1,
    )
    intervals = build_intervals(spec)
    assert intervals["baseline_before"].end == intervals["pre"].start
    assert intervals["pre"].end == intervals["event"].start
    assert intervals["event"].end == intervals["post"].start
    assert intervals["post"].end == intervals["baseline_after"].start
    assert (
        intervals["event"].end - intervals["event"].start
    ).total_seconds() == 23 * 3600


def test_intervals_are_half_open_local_calendar_days_and_dst_aware():
    df = plays(
        ("2024-03-08T08:00:00Z", "A", "One", "x"),
        ("2024-03-09T08:00:00Z", "A", "One", "x"),
        ("2024-03-10T07:59:59Z", "before", "One", "x"),
        ("2024-03-10T08:00:00Z", "event", "One", "x"),
        ("2024-03-11T06:59:59Z", "event", "One", "x"),
        ("2024-03-11T07:00:00Z", "post", "One", "x"),
        ("2024-03-12T07:00:00Z", "tail", "One", "x"),
    )
    result = analyze_event_window(
        df,
        EventWindowSpec(
            date(2024, 3, 10),
            timezone="America/Los_Angeles",
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=1,
        ),
    )
    event = result["periods"]["event"]
    assert event["start_utc"] == "2024-03-10T08:00:00Z"
    assert event["end_utc"] == "2024-03-11T07:00:00Z"
    assert event["plays"] == 2
    assert result["periods"]["pre"]["plays"] == 2
    assert result["periods"]["post"]["plays"] == 1


def test_coverage_clips_both_sides_and_rates_use_covered_days():
    df = plays(
        ("2024-01-03T12:00:00Z", "A", "One", "x"),
        ("2024-01-04T12:00:00Z", "A", "One", "x"),
        ("2024-01-05T12:00:00Z", "B", "Two", "y"),
        ("2024-01-06T12:00:00Z", "B", "Two", "y"),
        ("2024-01-07T12:00:00Z", "C", "Three", "z"),
    )
    result = analyze_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 5),
            pre_days=4,
            event_days=1,
            post_days=4,
            baseline_days=3,
        ),
    )
    assert result["periods"]["pre"]["requested_days"] == 4
    assert result["periods"]["pre"]["covered_days"] == 2
    assert result["periods"]["pre"]["plays_per_covered_day"] == 1.0
    assert result["periods"]["post"]["covered_days"] == 2
    assert result["diagnostics"]["baseline"]["clipped"] is True
    assert result["periods"]["baseline"]["covered_days"] == 0
    assert set(result["diagnostics"]["empty_periods"]) >= {
        "baseline_before",
        "baseline_after",
    }


def test_zero_covered_event_interval_is_an_error():
    df = plays(("2024-01-01T12:00:00Z", "A", "One", "x"))
    with pytest.raises(ValueError, match="event interval.*source coverage"):
        analyze_event_window(df, EventWindowSpec(date(2024, 2, 1)))


@pytest.mark.parametrize(
    ("entity", "expected_key", "blank_key"),
    [
        ("artist", ["A"], None),
        ("album", ["A", "One"], ["A", ""]),
        ("track", ["A", "x"], ["A", ""]),
    ],
)
def test_entity_groupings_and_blank_album_track_exclusion(
    entity, expected_key, blank_key
):
    df = plays(
        ("2024-01-01T12:00:00Z", "A", "One", "x"),
        ("2024-01-02T12:00:00Z", "A", "", ""),
        ("2024-01-03T12:00:00Z", "A", "One", "x"),
        ("2024-01-04T12:00:00Z", "A", "One", "x"),
        ("2024-01-05T12:00:00Z", "A", "One", "x"),
    )
    result = analyze_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 3),
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=1,
            entity=entity,
        ),
    )
    keys = [row["key"] for row in result["entities"]]
    assert expected_key in keys
    if blank_key is not None:
        assert blank_key not in keys


def test_baseline_excludes_analysis_windows_and_expected_residuals_are_hand_checkable():
    df = plays(
        ("2024-01-01T12:00:00Z", "A", "One", "x"),
        ("2024-01-02T12:00:00Z", "A", "One", "x"),
        ("2024-01-03T12:00:00Z", "A", "One", "x"),
        ("2024-01-04T12:00:00Z", "B", "Two", "y"),
        ("2024-01-05T12:00:00Z", "A", "One", "x"),
        ("2024-01-05T13:00:00Z", "A", "One", "x"),
        ("2024-01-06T12:00:00Z", "A", "One", "x"),
        ("2024-01-06T13:00:00Z", "A", "One", "x"),
        ("2024-01-06T14:00:00Z", "A", "One", "x"),
        ("2024-01-07T12:00:00Z", "A", "One", "x"),
        ("2024-01-08T12:00:00Z", "A", "One", "x"),
        ("2024-01-09T12:00:00Z", "C", "Three", "z"),
    )
    result = analyze_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 5),
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=3,
        ),
    )
    row = next(row for row in result["entities"] if row["key"] == ["A"])
    assert result["periods"]["baseline"]["plays"] == 6
    assert row["counts"] == {"pre": 0, "event": 2, "post": 3, "baseline": 5}
    assert row["expected_from_baseline"]["event"] == pytest.approx(5 / 6)
    assert row["standardized_residual"]["event"] == pytest.approx(
        (2 - 5 / 6) / math.sqrt(5 / 6)
    )
    assert row["post_minus_pre"]["count"] == 3
    assert row["presence"] == {
        "pre": False,
        "event": True,
        "post": True,
        "baseline": True,
    }
    assert row["first_ever_play_in_event_window"] is False


def test_residual_is_null_when_baseline_expected_is_zero_and_first_play_is_exact():
    df = plays(
        ("2023-12-31T12:00:00Z", "old", "One", "x"),
        ("2024-01-01T12:00:00Z", "old", "One", "x"),
        ("2024-01-02T00:00:00Z", "new", "Two", "y"),
        ("2024-01-03T12:00:00Z", "old", "One", "x"),
        ("2024-01-04T12:00:00Z", "old", "One", "x"),
    )
    result = analyze_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 2),
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=1,
        ),
    )
    row = next(row for row in result["entities"] if row["key"] == ["new"])
    assert row["expected_from_baseline"]["event"] == 0.0
    assert row["standardized_residual"]["event"] is None
    assert row["first_ever_play_in_event_window"] is True


def test_top_n_is_union_with_stable_delta_tie_breaking_and_json_has_no_nan():
    df = plays(
        ("2024-01-01T12:00:00Z", "base", "B", "b"),
        ("2024-01-02T12:00:00Z", "z", "Z", "z"),
        ("2024-01-03T12:00:00Z", "event", "E", "e"),
        ("2024-01-04T12:00:00Z", "a", "A", "a"),
        ("2024-01-05T12:00:00Z", "tail", "T", "t"),
    )
    spec = EventWindowSpec(
        date(2024, 1, 3),
        pre_days=1,
        event_days=1,
        post_days=1,
        baseline_days=1,
        top_n=1,
    )
    first = analyze_event_window(df, spec)
    second = analyze_event_window(df.sample(frac=1, random_state=7), spec)
    assert [row["key"] for row in first["entities"]] == [
        ["a"],
        ["z"],
        ["base"],
        ["event"],
    ]
    assert first == second
    assert "NaN" not in json.dumps(first, allow_nan=False, sort_keys=True)
    assert first["schema_version"] == 1
    assert first["event_date"] == "2024-01-03"


def test_compare_contract_excludes_missing_artists_and_rejects_missing_timestamps():
    df = plays(
        ("2024-01-01T12:00:00Z", "base", "B", "b"),
        ("2024-01-02T12:00:00Z", "event", "E", "e"),
        ("2024-01-03T12:00:00Z", "post", "P", "p"),
    )
    df.loc[len(df)] = [pd.Timestamp("2024-01-02T13:00:00Z"), None, "Unknown", "unknown"]
    result = compare_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 2),
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=1,
        ),
    )
    assert [""] not in [row["key"] for row in result["entities"]]
    assert result["periods"]["event"]["unique_artists"] == 1

    missing_timestamp = df.copy()
    missing_timestamp.loc[0, "timestamp"] = pd.NaT
    with pytest.raises(ValueError, match="timestamp"):
        compare_event_window(
            missing_timestamp,
            EventWindowSpec(
                date(2024, 1, 2),
                pre_days=1,
                event_days=1,
                post_days=1,
                baseline_days=1,
            ),
        )


@pytest.mark.parametrize("entity", ["artist", "album", "track"])
def test_entity_keys_strip_strings_and_exclude_blank_or_missing_artists(entity):
    df = plays(
        ("2024-01-01T12:00:00Z", "base", "Base", "base"),
        ("2024-01-02T10:00:00Z", "  A  ", "  One  ", "  x  "),
        ("2024-01-02T11:00:00Z", "   ", "Ghost", "ghost"),
        ("2024-01-02T12:00:00Z", "A", "   ", "   "),
        ("2024-01-03T12:00:00Z", "tail", "Tail", "tail"),
    )
    df.loc[len(df)] = [pd.Timestamp("2024-01-02T13:00:00Z"), None, "Ghost", "ghost"]
    df.loc[len(df)] = [pd.Timestamp("2024-01-02T14:00:00Z"), "A", None, None]
    result = compare_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 2),
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=1,
            entity=entity,
        ),
    )
    expected = {
        "artist": [["A"]],
        "album": [["A", "One"]],
        "track": [["A", "x"]],
    }[entity]
    event_counts = result["periods"]["event"]["entity_counts"]
    assert [row["key"] for row in event_counts] == expected
    assert result["periods"]["event"]["unique_artists"] == 1
    assert result["periods"]["event"]["unique_albums"] == 1
    assert result["periods"]["event"]["unique_tracks"] == 1


def test_schema_rounds_floats_to_ten_decimal_places():
    df = plays(
        ("2024-01-01T12:00:00Z", "A", "One", "x"),
        ("2024-01-02T12:00:00Z", "B", "Two", "y"),
        ("2024-01-02T13:00:00Z", "A", "One", "x"),
        ("2024-01-02T14:00:00Z", "C", "Three", "z"),
        ("2024-01-03T12:00:00Z", "A", "One", "x"),
    )
    result = compare_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 2),
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=1,
        ),
    )
    row = next(row for row in result["entities"] if row["key"] == ["A"])
    assert row["shares"]["event"] == 0.3333333333


def test_nonexistent_local_event_date_rejects_zero_duration_utc_interval():
    df = plays(
        ("2011-12-29T12:00:00Z", "before", "One", "x"),
        ("2011-12-31T12:00:00Z", "after", "Two", "y"),
    )
    spec = EventWindowSpec(
        date(2011, 12, 30),
        timezone="Pacific/Apia",
        pre_days=1,
        event_days=1,
        post_days=1,
        baseline_days=1,
    )
    with pytest.raises(ValueError, match="event.*positive UTC duration"):
        build_intervals(spec)
    with pytest.raises(ValueError, match="event.*positive UTC duration"):
        compare_event_window(df, spec)


def test_unrepresentable_window_bounds_raise_clear_value_error():
    spec = EventWindowSpec(
        date.min,
        pre_days=10**100,
        event_days=1,
        post_days=1,
        baseline_days=1,
    )
    with pytest.raises(ValueError, match="window bounds.*unrepresentable"):
        build_intervals(spec)


def test_period_entity_payload_is_top_n_bounded_with_diagnostics_and_stable_ties():
    df = plays(
        ("2024-01-01T12:00:00Z", "base", "Base", "base"),
        ("2024-01-02T10:00:00Z", "c", "C", "c"),
        ("2024-01-02T11:00:00Z", "b", "B", "b"),
        ("2024-01-02T12:00:00Z", "a", "A", "a"),
        ("2024-01-03T12:00:00Z", "tail", "Tail", "tail"),
    )
    result = compare_event_window(
        df,
        EventWindowSpec(
            date(2024, 1, 2),
            pre_days=1,
            event_days=1,
            post_days=1,
            baseline_days=1,
            top_n=2,
        ),
    )
    event = result["periods"]["event"]
    assert [row["key"] for row in event["entity_counts"]] == [["a"], ["b"]]
    assert [row["key"] for row in event["entity_shares"]] == [["a"], ["b"]]
    assert event["total_entities"] == 3
    assert event["entities_returned"] == 2
    assert result["diagnostics"]["period_entities"]["event"] == {
        "total_entities": 3,
        "entities_returned": 2,
    }


def test_public_and_analysis_interval_boundaries_are_identical():
    df = plays(
        ("2024-03-01T08:00:00Z", "first", "One", "x"),
        ("2024-03-20T07:00:00Z", "last", "Two", "y"),
    )
    spec = EventWindowSpec(
        date(2024, 3, 10),
        timezone="America/Los_Angeles",
        pre_days=2,
        event_days=1,
        post_days=2,
        baseline_days=3,
    )
    intervals = build_intervals(spec)
    result = compare_event_window(df, spec)
    for name, interval in intervals.items():
        period = result["periods"][name]
        assert period["requested_start_utc"] == interval.start.isoformat().replace(
            "+00:00", "Z"
        )
        assert period["requested_end_utc"] == interval.end.isoformat().replace(
            "+00:00", "Z"
        )
