# Life-Event Window Analytics

`life-event-window` measures listening around a date supplied by the user. It compares the periods immediately before, during, and after that date with a surrounding baseline. The command parses its required ISO `YYYY-MM-DD` option before constructing the pure Python `EventWindowSpec`, whose `event_date` is strictly a `datetime.date` rather than a string or `datetime`. The command reports measurements only; it does not infer what the event meant or whether it caused a change.

## Intervals and time

The event date is interpreted in the supplied IANA timezone. Boundaries are local midnights, then converted to UTC for filtering. This preserves local calendar days across daylight-saving transitions: a day may contain 23, 24, or 25 elapsed hours.

All intervals are half-open, so a play at an end boundary belongs to the next interval. If `e` is the event date and the configured lengths are `d_pre`, `d_event`, `d_post`, and `d_base`, the requested local-date intervals are:

\[
\begin{aligned}
pre &= [e-d_{pre}, e) \\
event &= [e, e+d_{event}) \\
post &= [e+d_{event}, e+d_{event}+d_{post}) \\
baseline_{before} &= [e-d_{pre}-d_{base}, e-d_{pre}) \\
baseline_{after} &= [e+d_{event}+d_{post}, e+d_{event}+d_{post}+d_{base})
\end{aligned}
\]

The combined `baseline` is the union of `baseline_before` and `baseline_after`; it excludes `pre`, `event`, and `post`.

## Coverage

Source coverage runs from the local date containing the first scrobble through the local date containing the last scrobble, inclusive. Each requested interval is clipped to that coverage. Rates use covered local calendar days, not requested days:

\[
plays\_per\_covered\_day = \frac{plays}{covered\_days}
\]

The result distinguishes two cases that must not be conflated. A covered interval with zero plays is observed as zero. An interval outside source coverage has zero covered days and a null rate; absent history is not converted into zero listening. The event interval must overlap source coverage or the command fails. Coverage only describes the bounds of the supplied history: it cannot detect unscrobbled listening or gaps inside those bounds.

## Entity measurements

The `entity` parameter selects exact grouping keys:

- `artist`: `[artist]`
- `album`: `[artist, album]`
- `track`: `[artist, track]`

Entity strings are stripped. Blank or missing artists are always excluded; album and track groupings also require a nonblank album or track. Unique album and track counts apply the same rules. No fuzzy matching or alias merging occurs. Each period reports plays, plays per covered day, unique artist/album/track counts, and ranked entity counts and shares. An entity share is its count divided by all plays in that period.

For entity `i` and period `w`, let `O_i,w` be its observed count, `B_i` its count in the combined baseline, `D_B` the baseline's covered days, and `D_w` the period's covered days. The baseline expectation and standardized residual are:

\[
E_{i,w} = \frac{B_i}{D_B}D_w
\]

\[
r_{i,w} = \frac{O_{i,w}-E_{i,w}}{\sqrt{E_{i,w}}}
\]

The expectation is null when the baseline has no covered days. The residual is null when the expectation is null or zero. `post_minus_pre` reports both the count difference and share difference. `presence` records whether the entity appears in each comparison period. `first_ever_play_in_event_window` is true only when the entity's first timestamp in the full supplied history falls inside the covered event interval.

The returned entity set is the union of the top `top_n` entities from `pre`, `event`, `post`, and `baseline`. Rows are sorted by descending absolute post-minus-pre share, then descending post count, then lexicographic entity key. Floating-point measurements are rounded to ten decimal places.

## Schema version 1

The top-level result contains:

- `schema_version`, `timezone`, and `event_date`.
- `parameters`: window lengths, entity type, and `top_n`.
- `periods`: `baseline_before`, `pre`, `event`, `post`, `baseline_after`, and their combined `baseline`.
- `entities`: counts, shares, post-minus-pre deltas, baseline expectations and residuals, presence flags, and first-play evidence.
- `diagnostics`: source timestamp and local-date bounds, combined-baseline clipping, and empty requested periods.

Each period includes requested and covered UTC boundaries, requested and covered local boundaries, day counts, plays and daily rate, unique counts, ranked `entity_counts`, and the covered interval parts. The combined baseline may contain two non-contiguous parts.

## Worked example

Suppose an artist has five baseline plays across six covered baseline days, no plays in a one-day pre window, two in the one-day event window, and three in the one-day post window. Its counts are:

```json
{"pre": 0, "event": 2, "post": 3, "baseline": 5}
```

The event expectation is `5 / 6 = 0.833333`, and its standardized residual is:

\[
\frac{2-5/6}{\sqrt{5/6}} \approx 1.278019
\]

The post-minus-pre count is `3 - 0 = 3`. These values show that the observed event and post counts differ from this local baseline; they do not show that the supplied life event caused the difference.
