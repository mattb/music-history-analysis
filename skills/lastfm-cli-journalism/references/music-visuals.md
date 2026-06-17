# Music Visuals

Use this reference when creating music taste graphs or intersection Venn diagrams from listening-history sessions.

Bundled reference images:

- `assets/elle-music-graph-reference.png`: preferred single-person music graph style.
- `assets/bill-music-graph-reference.png`: alternate single-person music graph style with bridge nodes and household/context islands.
- `assets/two-person-venn-reference.png`: preferred two-person intersection Venn style.

## Shared Rules

Build the visual content from evidence before prompting image generation:

1. Confirm or create the relevant named sessions.
2. Use JSON output when available.
3. Keep thresholds explicit in notes, especially for `listening-graph`.
4. Do not treat graph communities as genre labels until inspecting member artists.
5. Use common genre names. Avoid invented labels such as "Deep Repeat" or "Same Center Different Edges."
6. Keep the visual readable: fewer high-confidence nodes are better than a dense hairball.
7. If the data is incomplete or a known subset, reflect that in interpretation, not as visual clutter.

When using image generation, provide the bundled reference images as style references if the tool supports image inputs. If not, describe the reference style directly in the prompt.

## Single-Person Music Graph Process

Use for prompts such as "make Elle's Music Graph," "draw Chance's music graph," or "make Matt's Music Graph in the same way."

Evidence flow:

1. Run `lastfm listening-graph --session <id> --json`.
2. Start with `--min-artist-plays 10 --min-shared-sessions 2`.
3. If the dataset is small or sparse, lower to `--min-artist-plays 5 --min-shared-sessions 2`.
4. Extract:
   - largest communities by member count and plays
   - strongest edges by shared sessions
   - bridge nodes by degree, strength, participation, or betweenness
   - isolated but high-play artists for context islands
5. Name each cluster from its artist membership using familiar genre terms.
6. Limit the final image to roughly 4-6 clusters, 5-10 nodes per major cluster, and 4-8 yellow bridge nodes.

Preferred music graph style:

- 16:9 cream-paper editorial poster.
- Subtle paper grain, thin technical border, small crop or registration marks.
- Tall condensed uppercase title centered at top: `<NAME>'S MUSIC GRAPH`.
- Hand-drawn rounded cluster enclosures in distinct colors.
- Sparse node-link graph inside each cluster.
- Small colored circular nodes with uppercase artist labels.
- Yellow bridge nodes with black outlines and bold black connector lines.
- Thin colored intra-cluster lines; thicker black cross-cluster lines.
- No dashboard UI, screenshots, album art, photos, icons, gradients, 3D, or dense force-directed hairball.

Music graph prompt skeleton:

```text
Create "<NAME>'S MUSIC GRAPH" as a 16:9 cream-paper editorial poster in the style of the bundled music graph references.

Title text: "<NAME>'S MUSIC GRAPH"

Clusters:
- <COLOR> outline, label "<GENRE LABEL>": <artist nodes>. Key intra-cluster edges: <A--B, B--C>.
- <COLOR> outline, label "<GENRE LABEL>": <artist nodes>. Key intra-cluster edges: <...>.

Bridge nodes in yellow with black outlines: <artists>.
Cross-cluster connections: <artist -- artist>, <artist -- cluster>.

Style constraints: cream textured paper, tall condensed uppercase typography, hand-drawn rounded enclosures, sparse readable node-link layout, corner crop marks, no dashboard UI, no photos, no album art, no dense graph hairball.
```

## Two-Person Venn Process

Use for prompts such as "make the Matt/Bill intersection Venn diagram" or "make the Matt/Chance intersection Venn diagram."

Evidence flow:

1. Get all artist counts from both session CSVs, or use `lastfm top-artists --session <id> --limit <n> --json` when CSV access is not practical.
2. Normalize artist names case-insensitively for overlap, but render the best display casing.
3. Rank shared artists by a balanced score such as `min(count_a, count_b)` and then total count.
4. Use 8-12 shared artists in the center. Prefer artists with real weight for both people; avoid filling the center with one-play coincidences unless the overlap is very small.
5. Choose 4-6 side categories per person from their dominant non-overlap artists and graph communities.
6. Keep side labels as common genre names, not clever abstractions.

Preferred Venn style:

- 16:9 cream-paper poster.
- Two large overlapping circles with colored outlines.
- Left title: first person, large uppercase in that circle's color.
- Right title: second person, large uppercase in that circle's color.
- Pale yellow overlap lens labelled `SHARED`.
- Shared artists stacked vertically with dashed separators.
- Side categories shown as large uppercase rows with dashed separators.
- Thin technical border and crop marks.
- No extra legend, explanatory paragraph, album art, icons, gradients, or UI chrome.

Two-person Venn prompt skeleton:

```text
Create a <PERSON A> / <PERSON B> intersection Venn diagram in the style of the bundled Venn reference.

Text:
Left title: "<PERSON A>"
Right title: "<PERSON B>"
Center title: "SHARED"

Left categories:
"<CATEGORY>"
"<CATEGORY>"

Right categories:
"<CATEGORY>"
"<CATEGORY>"

Shared center artists, stacked with dashed separators:
"<ARTIST>"
"<ARTIST>"
"<ARTIST>"

Style constraints: 16:9 cream-paper poster, two large overlapping outlined circles, pale yellow overlap lens, tall condensed uppercase typography, technical border, corner registration marks, readable spacing, no extra legends or decorative clutter.
```

## Three-Person Venn Process

Use for three-way overlap requests.

Evidence flow:

1. Compute pairwise overlaps and three-way overlap from normalized artist counts.
2. Put the most balanced three-way artists in the center.
3. Put pair-only artists in the pairwise lenses.
4. Put broad genre categories, not long artist lists, in the person-only regions.
5. Use fewer labels than a two-person Venn; three-way layouts become illegible quickly.

Preferred three-person style: same cream-paper technical poster style, three large translucent circles, clear pairwise lenses, center shared node/list, and no dense labels at the edges.
