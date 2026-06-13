# Listening Graph Analytics

`listening-graph` builds a deterministic, undirected artist graph from listening sessions. Co-occurrence measures listening proximity, not musical similarity.

## Construction

The command applies inclusive `start_year` and `end_year` bounds before detecting sessions. A gap greater than `gap_minutes` starts a new session; a gap exactly equal to the threshold remains in the same session. Session detection happens before the minimum-play filter, so an excluded artist cannot split an otherwise continuous session.

Artist identity is deliberately conservative. A nonempty MusicBrainz ID becomes `mbid:<lowercase-mbid>`. Otherwise, the normalized name becomes `name:<normalize_for_matching(name)>`. Only identical keys merge. Name-only and MBID identities remain separate, and fuzzy matching is never used. The most frequent spelling is the display name; lexical order breaks ties. Aliases are sorted.

Each session contributes at most one count to each unordered eligible artist pair. Artists meeting `min_artist_plays` remain as nodes even when no edge meets `min_shared_sessions`.

For artists \(i\) and \(j\):

\[
\begin{aligned}
w_{ij} &= \text{shared sessions} \\
d_{ij} &= 1 / w_{ij} \\
J_{ij} &= \frac{\text{shared sessions}}{s_i + s_j - \text{shared sessions}}
\end{aligned}
\]

Here, \(s_i\) and \(s_j\) are each artist's session counts. `weight` is \(w_{ij}\); shortest-path measurements use `distance`, \(d_{ij}\).

## Measurements

Nodes report degree, weighted strength, degree centrality, distance-weighted closeness and betweenness, participation coefficient, articulation-point status, and an integer community ID. Participation is:

\[
P_i = 1 - \sum_c \left(\frac{s_{ic}}{s_i}\right)^2
\]

where \(s_{ic}\) is node \(i\)'s edge strength into community \(c\). Isolates receive zero.

Communities use NetworkX Louvain partitioning with sorted insertion, configurable resolution, and a fixed seed. IDs are numeric and ordered by the smallest member ID. They are partitions, not generated genre names. Reproducibility is guaranteed for the same inputs, parameters, NetworkX major version, and platform-compatible numeric behavior.

Betweenness is exact when `betweenness_samples` is at least the node count. Otherwise NetworkX samples that many pivots using `community_seed`; reports should disclose sampling.

## Output contract

JSON schema version 1 contains these top-level fields in stable order:

- `parameters`: all graph, community, sampling, and neighborhood settings.
- `source`: input play count and observed source-year bounds.
- `summary`: selected node, edge, and community counts.
- `communities`: numeric IDs and sorted member IDs.
- `nodes`: sorted identities, aliases, counts, and measurements.
- `edges`: canonical `source < target` pairs and edge measurements.
- `diagnostics`: session and threshold counts.

Floating-point values are rounded to 12 decimal places; non-finite values are not emitted. Collections are sorted. Empty graphs and graphs containing only isolates are valid.

`--artist` resolves one exact display name, case-insensitively. `--hops` returns the induced unweighted shortest-path neighborhood. Metrics and community IDs retain their full-graph values so a focused view does not silently change the analytical scope.

`--format graphml` returns an envelope containing GraphML text in `content`; it never writes a file. Node and edge attributes are scalar, with aliases joined for GraphML compatibility.

## Cost and interpretation

Session pair generation costs \(O(\sum_s a_s^2)\), where \(a_s\) is the number of eligible artists in session \(s\). Graph storage is \(O(V + E)\). Centrality and Louvain costs then depend on graph size; sampled betweenness is the main control for large graphs.

Edges mean only that two artists occurred in the same detected sessions. They do not establish genre, influence, recommendation quality, causality, or aesthetic resemblance.
