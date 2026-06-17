# Listening change points

`music-history listening-change-points` finds candidate boundaries where the measured composition or volume of listening changes. It does not name eras or infer places, moods, events, or causes.

## Bins and vocabulary

All timestamps are converted to UTC. Monthly bins begin at 00:00 on the first day of each calendar month. Weekly bins are ISO-style Monday-through-Sunday bins (`W-SUN`). The series includes every bin from the first play through the last play, including empty bins.

Artists are ranked by total plays, descending, then by artist name, ascending. The first `top_artists` become explicit dimensions; all remaining plays enter `__OTHER__`. Every play therefore contributes to exactly one bin and one dimension.

`__OTHER__` is reserved for the synthetic bucket. A real artist with that exact name is reported as `\__OTHER__`; real artist names already beginning with `\` gain one additional leading `\`. This deterministic escaping is injective, so vocabulary entries and deltas remain unambiguous.

## Vectors

For share vectors, let \(c_{ti}\) be plays for artist dimension \(i\) in bin \(t\), and \(C_t = \sum_i c_{ti}\). The vector is

\[
x_{ti} = \sqrt{c_{ti}/C_t}.
\]

An empty bin is the all-zero vector. Euclidean distance between these vectors is the Hellinger distance multiplied by \(\sqrt{2}\).

For count vectors, each component is transformed with \(z_{ti}=\log(1+c_{ti})\), then standardized across bins using its population mean and population standard deviation. A constant component is set to zero.

## Exact segmentation

For a segment \([a,b)\), the cost is multivariate within-segment squared error:

\[
SSE(a,b) = \sum_{t=a}^{b-1}\lVert x_t-\bar{x}_{a:b}\rVert^2.
\]

Prefix sums of vectors and squared norms make each segment-cost query \(O(d)\), where \(d\) is the vocabulary size. Dynamic programming examines every legal previous boundary and minimizes

\[
\sum_j SSE(s_j,s_{j+1}) + \beta K,
\]

where \(K\) is the number of change points and every segment has at least `min_segment_bins` bins. The algorithm is exact, not greedy. Finite float objectives use exact ordering. Only exactly equal objectives invoke the deterministic tie rules: fewer boundaries, then the lexicographically earliest boundary sequence.

Noise variance is estimated from adjacent squared vector distances divided by twice the number of active dimensions. The median is used; if it is zero but positive estimates exist, the smallest positive estimate is used. The penalty is

\[
\beta = m\,\hat{\sigma}^2 d_a \log n,
\]

where \(m\) is `penalty_multiplier`, \(d_a\) is the number of nonconstant dimensions, and \(n\) is the number of bins. A constant series reports no changes. A nonconstant series needs at least twice the minimum segment length.

Time complexity is \(O(n^2d + q)\), where \(q\) is the number of distinct backpointer comparisons needed only for objective-and-count ties and is at most \(O(n^2)\). The DP stores scalar objectives, change counts, and predecessor indexes, then reconstructs the winning boundaries once. Prefix data and ordinary DP state require \(O(nd+n)\) space; memoized exact-tie comparisons require an additional \(O(q)\) scalar entries in the pathological all-ties case. No state or candidate stores a copied boundary sequence.

## Schema v1

The JSON result contains:

- `parameters`: the complete analysis specification.
- `vector`: vocabulary, dimension counts, and transformation metadata.
- `model`: algorithm, estimated variance, penalty, and final objective.
- `change_points`: numbered right-bin timestamps, adjacent segment IDs, centroid distance, plays-per-bin delta, and the largest artist-share deltas.
- `segments`: numbered UTC boundaries, bin and play counts, play rate, unique artists, empty-bin count, and top artist shares.
- `diagnostics`: all empty bins, all mechanically low-volume bins (fewer than 10 plays), and the constant-series flag.

All floats are finite and rounded. Stable vocabulary ordering, deterministic tie-breaking, and stable JSON structures make shuffled input rows produce the same result.

## Worked example

Suppose six monthly bins contain only A for January–March and only B for April–June. With a two-bin minimum and a sufficiently small multiplier, the optimal boundary is `2024-04-01T00:00:00Z`, the start of the right segment. Segment 1 has A share 1 and B share 0; segment 2 has A share 0 and B share 1. The reported deltas are A `-1` and B `+1`. Those measurements support inspection, but they do not explain why the change happened.

## Comparability

Compare runs only when frequency, vector mode, vocabulary size, minimum segment length, penalty multiplier, and input coverage are the same. Changing any of them changes the scale or the set of legal models. In particular, penalties from weekly and monthly runs are not directly comparable, and share-mode objectives are not count-mode objectives.
