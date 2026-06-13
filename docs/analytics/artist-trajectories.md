# Artist Relationship Trajectories

`artist-trajectories` measures listening over dense UTC calendar periods. `artist-cohort-retention` measures whether newly discovered artists remain active at exact later offsets. Both commands report measurements only; they do not infer causes or sentiment.

## Identity and observation

Artist identity is an exact Unicode-casefolded name. There is no fuzzy, substring, album, critic, or embedding match. The display name is the most frequent observed spelling; lexical order breaks frequency ties. Batched queries retain their input order, including missing artists.

Month bins use `YYYY-MM`; year bins use `YYYY`. Bounds are inclusive. Explicit bounds create leading and trailing zero bins, and all periods are UTC pandas calendar periods. `first_play` and `last_play` are the exact timestamps inside the observation window, not period boundaries.

Observation diagnostics distinguish report truncation from horizon censoring. `left_truncated` is true when the caller's start follows the source start. `right_truncated` is true when the caller's end precedes the available source end; known later source data was deliberately omitted. `right_censored` is true when the caller's end reaches or extends beyond the source end; activity after the source horizon cannot be observed. Thus `right_truncated` and `right_censored` are complementary. Leading and trailing inactive runs are separately marked as censored: without threshold-active bins on both sides, they are not dormancy episodes.

## Activity, span, peak, and ramp

For period \(t\), let \(p_t\) be play count and let \(m\) be `min_period_plays`:

\[
a_t = \mathbf{1}(p_t \ge m)
\]

Total plays include bins below \(m\). If \(T\) periods are observed and \(A=\sum_t a_t\), active share is \(A/T\). If the first and last active bins are \(f\) and \(l\), inclusive active span is \(l-f+1\), and span activity share is \(A/(l-f+1)\). Span share is null when there is no active bin.

Peak count is \(\max_t p_t\). Every tied peak period is returned chronologically; the earliest is primary. Ramp starts at the first active bin and ends at the primary peak. It reports period distance, first-bin plays, absolute change, and:

\[
\text{mean change} = \frac{p_{peak}-p_f}{peak-f}
\]

Mean change is null at zero distance. OLS slope fits \(p_t=\alpha+\beta t\) across every dense ramp bin, including zeros; it is null with fewer than two points. Calculations retain full precision and emitted floats are rounded to 12 decimal places.

## Dormancy and returns

A dormancy episode is at least `dormancy_periods` consecutive inactive bins strictly between two active bins. Shorter inactive gaps remain inside one active segment. Leading and trailing inactivity are censored and never become episodes.

The return is the first active bin after the dormant run. Each return reports plays across the return bin plus the next two bins and across the return bin plus the next five bins. Completeness flags are true only when all three or six bins lie inside the observation window. These are coverage facts, not claims about why listening stopped or resumed.

## Discovery cohorts and point retention

Let \(d_i\) be artist \(i\)'s first-ever activity-granularity period in the full input history. The artist's cohort label is the cohort-granularity period containing \(d_i\), even when report bounds begin later. The artist enters that cohort only if plays in \(d_i\) meet `min_discovery_plays`. Cohort results never include artist names.

For cohort \(c\), activity-period offset \(k\), and threshold \(q\), point retention is:

\[
R_{c,k}=\frac{\#\{i \in c:d_i+k\le h\;\land\;p_{i,d_i+k}\ge q\}}{\#\{i \in c:d_i+k\le h\}}
\]

Here, \(h\) is the last observable activity period: the earlier of the requested end and actual source coverage. Each artist's target is its own \(d_i+k\), not the cohort boundary plus \(k\). This is activity in the exact target period, not cumulative activity. Artists whose targets lie after \(h\) are right-censored and excluded from the denominator. A cell with no eligible artists has a null rate, never zero. Offsets are unique, nonnegative, and sorted; defaults are 1, 3, 6, 12, and 24.

Each cohort also reports size, mean and median plays in the members' respective \(d_i\) periods, and the count/share with any later activity period meeting `min_active_plays` through \(h\). Empty cohorts remain in the dense output with null aggregates. Diagnostics report source coverage, report truncation, total/nonempty cohorts, and cohort membership counts.

## Trajectory output schema

The trajectory batch returns `artists` in query order and `count`. Each successful or `not_found` artist object contains `schema_version: 1` plus:

- `query_artist`, `status`, and resolved `artist`; a miss has `status: not_found`, a null artist, and null measurements.
- `parameters`: granularity, requested bounds, activity threshold, and dormancy threshold.
- `observation`: dense start/end, source start/end, report truncation flags, inactive-run lengths, and inactive-run censoring flags.
- `timeline`: chronological `{period, plays, active}` bins.
- `summary`: total plays, observed and active periods, active share, inclusive active span/share, and exact first/last timestamps.
- `peak`: maximum plays, every tied period, and earliest primary period.
- `ramp`: first active and primary peak periods, distance, endpoint counts/change, mean change, and OLS slope.
- `dormancy`: threshold, return count, and episodes. Each episode includes its inactive bounds/length, return bin/count, three- and six-period totals, and completeness flags.
- `segments`: active segment bounds and active-bin counts; gaps shorter than the dormancy threshold remain inside a segment.

For example, counts `[1, 0, 2, 0]` from January through April with `min_period_plays=1` and `dormancy_periods=1` produce two active segments. February is a one-period dormancy returning in March. April is only trailing censored inactivity. The primary peak is March; its ramp uses `[1, 0, 2]`, so distance is 2, change is 1, mean change is 0.5, and the zero bin participates in OLS.

## Cohort output schema

The cohort result contains top-level `schema_version: 1` plus:

- `parameters`: cohort and activity granularities, requested bounds, discovery/activity thresholds, and normalized offsets.
- `observation`: requested cohort bounds, source cohort bounds, the last actually observable activity period after clipping by `end`, truncation/censoring flags, and source artist count.
- `cohorts`: one object per dense cohort period. Each contains `cohort`, `cohort_size`, mean/median `first_period_plays`, thresholded `any_later_activity`, and retention `cells`.
- Each cell contains `offset`, `eligible_artists`, `retained_artists`, and nullable `retention_rate`.
- `diagnostics`: total and nonempty cohort counts plus total cohort membership.

Mixed granularities retain the cohort label but anchor measurements per artist. Suppose two artists join the 2024 yearly cohort: one first appears twice in January and once in February; the other first appears twice in November and once in December. With monthly activity, `min_discovery_plays=2`, and offset 1, first-period plays are `[2, 2]`. The targets are February and December—not a shared February target—so both artists are retained. If the data ends in November, the second artist's December target is right-censored and excluded from that cell's denominator.
