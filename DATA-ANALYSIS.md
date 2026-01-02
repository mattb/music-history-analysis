# Data Analysis Strategies

This document describes the analytical approaches used throughout the Last.fm CLI tool. Each strategy addresses a different aspect of understanding musical taste and listening behavior.

---

## Preamble: Problem Space and Solution Space

### The Problem

After 20 years of digital music listening, many of us have accumulated vast listening histories—hundreds of thousands of scrobbles across thousands of artists. This data represents a detailed record of our musical lives, but it sits largely unexamined. We face several interconnected problems:

**1. Self-Understanding**
- What do I actually listen to? Not what I think I listen to, but what the data shows.
- How has my taste evolved? Did I broaden or narrow over time?
- Which artists have I been loyal to? Which did I abandon?
- What patterns exist that I'm not consciously aware of?

**2. Discovery**
- How do I find new music that matches my taste, not just what's popular?
- Which music critics share my sensibilities? Can I trust their recommendations?
- What acclaimed albums have I overlooked that I'd probably love?
- How do I escape my filter bubble without random wandering?

**3. Context**
- How does my taste compare to critical consensus?
- Am I an early adopter or a late discoverer?
- What genres, labels, and countries dominate my listening?
- Which of my favorites are hidden gems vs. mainstream picks?

**4. Data Fragmentation**
- Last.fm provides scrobbles but not release years, genres, or labels
- Critics' lists exist but aren't connected to personal listening data
- MusicBrainz has rich metadata but requires complex lookups
- No single source answers the questions above

### The Solution Space

This project explores several analytical approaches to address these problems:

**Cross-Referencing with Critics (2011-2025)**

Rather than treating critics as authorities, we treat them as *taste profiles*. By scraping 15 years of year-end lists from hundreds of publications, we can:
- Calculate overlap between your listening and each critic's picks
- Weight recommendations by critic alignment (critics who "get" you)
- Identify blind spots: acclaimed albums from artists you already love
- Track whether past recommendations became future favorites

**Embedding Spaces for Similarity**

Two complementary 50-dimensional embedding spaces capture different notions of "similar":

| Space | Built From | Captures |
|-------|------------|----------|
| **User Embeddings** | Weekly co-listening patterns | Personal associations (what YOU group together) |
| **Critics Embeddings** | Co-listing on year-end lists | Critical consensus (what CRITICS group together) |

These enable:
- Finding similar artists in your own taste-space
- Comparing your perception vs. critical perception
- Identifying "bridge artists" who connect different regions of your taste
- Understanding which artists you perceive differently than critics do

**Temporal Pattern Detection**

Listening history is fundamentally temporal. We extract patterns including:
- **Discovery/Abandonment**: When artists enter and exit your rotation
- **Gateway Artists**: Which existing favorites led to new discoveries
- **Musical Eras**: Clustering years by listening patterns to find natural phases
- **Loyalty Patterns**: Long-term favorites, rediscoveries after gaps, abandonments
- **Trend Analysis**: Statistical tests for narrowing/broadening taste over time

**Metadata Enrichment via MusicBrainz**

Last.fm scrobbles lack rich metadata. By building a local SQLite database from MusicBrainz data dumps (~3GB), we can instantly look up:
- Release year (for catalog vs. new music analysis)
- Genres (weighted by your actual play counts)
- Record labels (revealing label affinities)
- Countries and languages (geographic distribution)
- Release types (albums vs. EPs vs. singles)

**Visualization**

Complex patterns require visual representation:
- **Calendar heatmaps**: GitHub-style contribution graphs for listening intensity
- **Musical Genome**: UMAP projection of artist embeddings with HDBSCAN clustering
- **Sparklines**: 20-year artist trajectories in a single line
- **Interactive HTML**: Searchable, hoverable visualizations for exploration

### What This Is Not

This project deliberately avoids:
- **Collaborative filtering**: We don't use other users' data, only your own history and public critics' lists
- **Audio analysis**: No spectrogram or acoustic feature extraction
- **Real-time streaming**: Batch analysis of exported history, not live integration
- **Prescriptive recommendations**: We surface patterns and possibilities; you decide what to explore

### Design Philosophy

1. **Your Data, Your Taste**: All analysis is grounded in your actual listening, not predictions about what you "should" like
2. **Critics as Peers, Not Authorities**: High critic overlap means shared taste, not superior judgment
3. **Transparency Over Magic**: Every recommendation can be traced to specific critics, similarity scores, or play counts
4. **Local-First**: MusicBrainz database and embedding caches live on your machine; no external API calls during analysis
5. **Exploration Over Optimization**: The goal is insight and discovery, not maximizing engagement metrics

---

## Table of Contents

1. [Input Data Schemas](#1-input-data-schemas)
2. [Critic Matching and Overlap](#2-critic-matching-and-overlap)
3. [Embedding Spaces](#3-embedding-spaces)
4. [Trend and Pattern Detection](#4-trend-and-pattern-detection)
5. [Clustering and Segmentation](#5-clustering-and-segmentation)
6. [Visualization Techniques](#6-visualization-techniques)
7. [Cross-Space Analysis](#7-cross-space-analysis)
8. [MusicBrainz Metadata Enrichment](#8-musicbrainz-metadata-enrichment)
9. [Recommendation Algorithms](#9-recommendation-algorithms)
10. [Prediction and Validation](#10-prediction-and-validation)

---

## 1. Input Data Schemas

### Last.fm Scrobble Data

**Source**: Last.fm API (`user.getrecenttracks` method)

**File**: `recenttracks-{username}-{timestamp}.csv`

**Schema**:
| Column | Type | Description | Example |
|--------|------|-------------|---------|
| `uts` | integer | Unix timestamp (seconds since epoch) | `1766539433` |
| `utc_time` | string | Human-readable UTC time | `"24 Dec 2025, 01:23"` |
| `artist` | string | Artist name | `"Longpigs"` |
| `artist_mbid` | string | MusicBrainz artist ID (UUID, may be empty) | `"a7378d57-a9a5-408a-80e0-a14d7203df77"` |
| `album` | string | Album name (may be empty) | `"The Sun Is Often Out"` |
| `album_mbid` | string | MusicBrainz release ID (UUID, may be empty) | `"66dbe2bd-afa3-4d24-a19b-bd61900035c9"` |
| `track` | string | Track name | `"Sleep"` |
| `track_mbid` | string | MusicBrainz recording ID (UUID, may be empty) | `"2464cd4f-530e-33c8-9a1f-0e94746c3453"` |

**Sample Row**:
```csv
"1766539433","24 Dec 2025, 01:23","Longpigs","a7378d57-a9a5-408a-80e0-a14d7203df77","The Sun Is Often Out","66dbe2bd-afa3-4d24-a19b-bd61900035c9","Sleep","2464cd4f-530e-33c8-9a1f-0e94746c3453"
```

**Internal Processing**:
```python
df["timestamp"] = pd.to_datetime(df["uts"], unit="s", utc=True)
df["year"] = df["timestamp"].dt.year
```

**Acquisition**:
- `lastfm fetch <username>` downloads via API
- 200 tracks per page, rate-limited (0.2s delay)
- Auto-retry on 500 errors (3 attempts, 10s backoff)
- `--start-year` flag for incremental updates

---

### Critics Year-End Lists

**Source**: yearendlists.com (web scraping)

**File**: `critics-{year}.json` (one file per year, 2011-2025)

**Schema**:
```json
[
  {
    "url": "https://www.yearendlists.com/2024/...",
    "title": "The Top 20 Albums of 2024",
    "critic": "A Closer Listen",
    "year": 2024,
    "albums": [
      {
        "rank": 1,
        "artist": "Rafael Toral",
        "title": "Spectral Evolution",
        "artist_url": "https://www.yearendlists.com/artists/rafael-toral-...",
        "album_url": "https://www.yearendlists.com/albums/spectral-evolution-..."
      }
    ]
  }
]
```

**Field Details**:

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | Source URL of the critic's list |
| `title` | string | Title of the list (e.g., "Top 50 Albums of 2024") |
| `critic` | string | Publication or critic name |
| `year` | integer | Year the list covers |
| `albums` | array | Ordered list of album entries |
| `albums[].rank` | integer | Position on the list (1 = top) |
| `albums[].artist` | string | Artist name |
| `albums[].title` | string | Album title |
| `albums[].artist_url` | string | Optional link to artist page |
| `albums[].album_url` | string | Optional link to album page |

**Acquisition**:
- `lastfm critics fetch --year YYYY` scrapes yearendlists.com
- Two-phase crawl: discover list URLs → parse each list
- Filters out non-music lists (films, TV, podcasts, books)
- Rate-limited (0.5s default delay)

---

### MusicBrainz Metadata

**Source**: MusicBrainz JSON data dumps (https://data.metabrainz.org)

#### Raw Dump Format

**URL**: `https://data.metabrainz.org/pub/musicbrainz/data/json-dumps/{date}/release.tar.xz`

**Size**: ~2-3GB compressed, contains one JSON object per line

**Sample Release Object** (simplified):
```json
{
  "id": "66dbe2bd-afa3-4d24-a19b-bd61900035c9",
  "title": "The Sun Is Often Out",
  "date": "1996-06-17",
  "country": "GB",
  "artist-credit": [
    {
      "artist": {
        "id": "a7378d57-a9a5-408a-80e0-a14d7203df77",
        "name": "Longpigs"
      }
    }
  ],
  "release-group": {
    "primary-type": "Album",
    "secondary-types": []
  },
  "text-representation": {
    "language": "eng"
  },
  "tags": [
    {"name": "britpop", "count": 5},
    {"name": "rock", "count": 3}
  ],
  "label-info": [
    {
      "label": {"name": "Mother Records"},
      "catalog-number": "MUMCD9604"
    }
  ]
}
```

#### Local SQLite Database

**Location**: `~/.cache/lastfm-analysis/musicbrainz_releases.db`

**Size**: ~1-2GB

**Tables**:

**`releases`** - Main release table
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary key |
| `artist_credit` | TEXT | Full artist name(s) |
| `title` | TEXT | Album/release title |
| `year` | INTEGER | Release year (extracted from date) |
| `artist_norm` | TEXT | Lowercase artist for matching |
| `title_norm` | TEXT | Lowercase title for matching |
| `artist_mbid` | TEXT | MusicBrainz artist UUID |
| `release_type` | TEXT | album, ep, single, compilation, etc. |
| `country` | TEXT | ISO country code (GB, US, JP, etc.) |
| `language` | TEXT | ISO language code (eng, jpn, etc.) |
| `genres` | TEXT | Top 5 genres, comma-separated |
| `labels` | TEXT | Top 3 labels, comma-separated |

**`release_genres`** - Genre lookup table
| Column | Type | Description |
|--------|------|-------------|
| `release_id` | INTEGER | Foreign key to releases |
| `genre` | TEXT | Genre name (lowercase) |
| `count` | INTEGER | Tag count from MusicBrainz |

**`release_labels`** - Label lookup table
| Column | Type | Description |
|--------|------|-------------|
| `release_id` | INTEGER | Foreign key to releases |
| `label_name` | TEXT | Record label name |
| `catalog_number` | TEXT | Catalog number (optional) |

**Indexes**: `artist_norm+title_norm`, `title_norm`, `artist_mbid`, `release_type`, `country`, `year`, `genre`, `label_name`

---

### Cache Files

**Location**: `~/.cache/lastfm-analysis/`

| File | Purpose |
|------|---------|
| `musicbrainz-release-{date}.tar.xz` | Cached MusicBrainz dump (~3GB) |
| `musicbrainz_releases.db` | SQLite database (~1-2GB) |
| `release_years.json` | Incremental year cache (API + DB lookups) |
| `lastfm_api_key.txt` | Last.fm API key |
| `spotify_credentials.json` | Spotify OAuth credentials |
| `.spotify_token_cache` | Spotify access/refresh tokens |
| `critics_embeddings/` | Critics embedding pickle files |
| `{csv_hash}/` | Per-user embedding cache directory |

**`release_years.json`** schema:
```json
{
  "mbid-uuid": 2024,
  "artist|||album": 2020
}
```
Keys are either MusicBrainz UUIDs or `"artist|||album"` (lowercase, triple-pipe separator).

**Embedding pickle files** contain:
- `embeddings`: numpy array (n_artists × 50)
- `artist_to_idx`: dict mapping artist name → row index
- `idx_to_artist`: dict mapping row index → artist name
- `explained_variance_ratio`: variance per dimension

---

## 2. Critic Matching and Overlap

Cross-referencing personal listening with music critics' picks.

### String Normalization

Matching albums across sources requires robust normalization:

```python
def normalize_for_matching(s: str) -> str:
    s = s.lower().strip()
    if s.startswith("the "):
        s = s[4:]                           # Remove "the " prefix
    s = re.sub(r'\s*\([^)]*\)', '', s)      # Remove parentheticals "(Deluxe Edition)"
    s = re.sub(r'[^\w\s]', '', s)           # Remove punctuation
    return ' '.join(s.split())              # Collapse whitespace
```

For edge cases, fuzzy matching via `rapidfuzz` Levenshtein distance:
- **Threshold**: 85% similarity on both artist and album
- **Weighting**: Album 70%, artist 30% (albums vary more in naming)

### Album Listening Criteria (5x5 Rule)

An album is "heard" only if:
- At least **5 different tracks** played
- Each track played at least **5 times**

This prevents counting albums where only one track appeared on a playlist:

```python
def get_albums_listened_to(df, min_unique_tracks=5, min_plays_per_track=5):
    track_plays = df.groupby(["artist", "album", "track"]).size()
    qualified = track_plays[track_plays >= min_plays_per_track]
    albums = qualified.groupby(["artist", "album"]).size()
    return albums[albums >= min_unique_tracks].index
```

### Critic Overlap Calculation

Uses the **Szymkiewicz-Simpson coefficient** (overlap coefficient):

```
overlap = |A ∩ B| / min(|A|, |B|)
```

This is more meaningful than Jaccard for comparing sets of very different sizes (your library vs. a critic's 50-album list):

```python
overlap_pct = overlap_count / min(len(your_albums), critic_total) * 100
```

### Multi-Year Aggregation

Critics commands search **all years (2011-2025)** by default:
- Great music doesn't expire
- Historical picks reveal long-term critical consensus
- More data = better critic alignment calculation

Use `--year YYYY` to filter to a specific year.

---

## 3. Embedding Spaces

Two complementary embedding spaces capture different notions of artist similarity.

### User Embeddings (Co-Listening Patterns)

**Source**: Personal scrobble history

**Algorithm**:
1. Group scrobbles into weekly time windows
2. Build artist × artist co-occurrence matrix:
   - `matrix[i,j]` = number of weeks where both artists played
3. Normalize by geometric mean (reduces popularity bias):
   ```python
   matrix = matrix / np.outer(np.sqrt(np.diag(matrix)), np.sqrt(np.diag(matrix)))
   ```
4. L2 row normalization
5. Truncated SVD to 50 dimensions
6. L2 normalize resulting vectors

**Interpretation**: Artists you listen to in the same periods are similar. Captures personal taste associations.

**Cache**: `~/.cache/lastfm-analysis/{csv_hash}/artist_embeddings_cooccurrence_minplays5.pkl`

### Critics Embeddings (Co-Listing Patterns)

**Source**: 15 years of critics' year-end lists (2011-2025)

**Algorithm**:
1. For each critic's list, all artists on that list co-occur
2. Build artist × artist matrix:
   - `matrix[i,j]` = number of critics who listed both artists
3. Same normalization and SVD as user embeddings
4. Require `min_critics=2` to filter noise

**Interpretation**: Artists that critics group together represent "critical consensus" similarity.

**Cache**: `~/.cache/lastfm-analysis/critics_embeddings/critics_embeddings_2011-2025_min2.pkl`

### Similarity Search

Both spaces support identical interfaces:

```python
embeddings.find_similar(artist, top_n=10) -> [(artist, similarity), ...]
```

Similarity via cosine distance on L2-normalized vectors (scores 0-1).

### Dimension Interpretation

Each SVD dimension captures a latent factor. Interpret by examining pole artists:

```
Dimension 3 (4.2% variance):
  +: Radiohead, The National, Arcade Fire
  -: Kendrick Lamar, Drake, Kanye West
```

The `listen dimensions` command shows top dimensions by explained variance. Users interpret semantic meaning (e.g., "indie rock vs. hip-hop").

---

## 4. Trend and Pattern Detection

### Discovery and Abandonment

**Discovery**: Artists whose first-ever scrobble was in year Y
```python
first_plays = df.groupby("artist").timestamp.min()
discovered = first_plays[first_plays.dt.year == target_year]
```

**Abandonment**: Artists whose last-ever scrobble was in year Y
```python
last_plays = df.groupby("artist").timestamp.max()
abandoned = last_plays[last_plays.dt.year == target_year]
```

Together, these map entry and exit points of your musical journey.

### Gateway Artist Detection

For newly discovered artists, find which existing artists led there:

1. Get discovery date for the new artist
2. Filter to artists played *before* that date
3. Find most similar (in user embedding space) from that prior set
4. Top matches are "gateways" that likely led to discovery

### Loyalty Patterns

| Pattern | Definition |
|---------|------------|
| **Long-term favorites** | Artists played in 5+ different years |
| **Rediscoveries** | Artists with 3+ year gap who returned |
| **Abandoned** | 30+ total plays, last play 3+ years ago |

Gap detection:
```python
for i in range(len(years) - 1):
    if years[i+1] - years[i] >= 3:  # 3+ year gap
        gaps.append((years[i], years[i+1]))
```

### Statistical Trend Analysis

**Mann-Kendall test** detects monotonic trends over time:

```python
from scipy.stats import kendalltau

tau, p_value = kendalltau(years, concentrations)
if p_value < 0.05:
    trend = "narrowing" if tau > 0 else "broadening"
```

Applied to:
- **Taste concentration**: Focusing on fewer artists over time?
- **Artist diversity**: Discovering more or fewer unique artists?
- **Listening volume**: Total listening increasing or decreasing?

### Discovery Funnel

Tracks conversion across engagement stages:

| Stage | Threshold | Meaning |
|-------|-----------|---------|
| Discovery | 1+ plays | First encounter |
| Curiosity | 5+ plays | Gave them a chance |
| Fan | 50+ plays | Regular listener |
| Superfan | 200+ plays | Obsessed |

```python
conversion_rate = (next_stage_count / current_stage_count) * 100
```

---

## 5. Clustering and Segmentation

### Musical Eras (Hierarchical Clustering)

Detect periods of similar listening:

1. Build year × top-artists feature matrix (normalized play counts)
2. Find optimal cluster count via **silhouette analysis** (2-10 clusters)
3. Apply **Agglomerative Clustering** with Ward linkage
4. Group years into eras, identify defining artists

```
Era 1: 2008-2012
Defined by: Radiohead, Arcade Fire, The National
Total plays: 45,231
```

### Artist Clustering (HDBSCAN)

For genome visualization, cluster artists in 2D:

```python
clusterer = hdbscan.HDBSCAN(
    min_cluster_size=max(10, n_artists // 25),
    min_samples=max(3, n_artists // 100)
)
```

HDBSCAN chosen because:
- Handles varying cluster densities
- Marks noise points as -1 (doesn't force everything)
- No need to prespecify cluster count

### Bridge Artist Detection

Bridge artists connect different taste regions:

1. For each artist, get top-10 similar artists
2. Get embedding vectors for those neighbors
3. Calculate pairwise similarity within neighborhood
4. **Bridge artists have low internal similarity** (diverse neighborhoods)

```python
sim_matrix = cosine_similarity(neighbor_embeddings)
avg_internal_sim = np.mean(upper_triangle(sim_matrix))
if avg_internal_sim < 0.6:
    # This artist bridges different taste regions
```

---

## 6. Visualization Techniques

### Calendar Heatmaps

GitHub-style contribution graphs:
- Daily aggregation of play counts
- Color scale based on percentiles (0th, 20th, 40th, 60th, 80th, 100th)
- Interactive tooltips with date, plays, top artists

### UMAP Dimensionality Reduction

Reduce 50D embeddings to 2D for visualization:

```python
reducer = UMAP(
    n_components=2,
    n_neighbors=min(max(int(n_artists * 0.12), 15), 200),
    min_dist=0.3 if n_artists < 500 else 0.1,
    metric="cosine"
)
```

Parameters scale with dataset size:
- **n_neighbors**: 10-15% of dataset (min 15, max 200)
- **min_dist**: Larger for small datasets

### Interactive Scatter (DataMapPlot)

Musical Genome uses DataMapPlot for interactive HTML:
- Points sized by play count (power transform for visual spread)
- Cluster labels from dominant listening years
- Search for specific artists
- Hover tooltips with play counts

```python
marker_size = np.power(normalized_plays, 0.3) * 15 + 0.5
```

### Sparklines

20-year patterns in single lines:

```python
blocks = " ▁▂▃▄▅▆▇█"
def sparkline(values):
    max_v = max(values)
    return "".join(blocks[int(v/max_v * 8)] for v in values)

# Output: "▁▂▃▅█▇▃▂▁▁" (peak in middle years)
```

---

## 7. Cross-Space Analysis

### Taste Gaps

Compare your perception vs. critical consensus:

1. For each artist in both spaces:
   - Get top-20 neighbors in user space
   - Get top-20 neighbors in critics space
2. Calculate **Jaccard similarity** between neighbor sets:
   ```
   jaccard = |intersection| / |union|
   ```
3. **Divergent**: Low Jaccard (< 0.15) — different views
4. **Aligned**: High Jaccard (> 0.3) — agreement

Example:
```
Burial
  You group with: Autechre, Aphex Twin, Boards of Canada
  Critics group with: Jamie xx, Joy Orbison, Four Tet
  → You hear IDM; critics hear UK bass/garage
```

### Dual Similarity Display

The `artist` command shows both perspectives:

```
━━━ Similar in Your Library (Co-Listening) ━━━
  1. Autechre (89%) - 156 plays

━━━ Critics Also Group With ━━━
  1. Four Tet (91%) - 456 plays
  2. Aphex Twin (85%) - 0 plays [!] ← Explore!
```

---

## 8. MusicBrainz Metadata Enrichment

### How MusicBrainz Data Is Used

MusicBrainz provides rich metadata for albums in your listening history that Last.fm doesn't have: release year, genres, labels, country, language, and release type.

#### Data Acquisition Pipeline

1. **Download dump** (`metadata download`):
   - Fetches latest `release.tar.xz` from data.metabrainz.org (~3GB)
   - Caches locally to avoid re-downloading
   - Stream-processes JSON (one release per line)

2. **Build SQLite database**:
   - Extracts: artist, title, year, genres, labels, country, language, type
   - Normalizes artist/title for fast lookups
   - Creates indexes for all query patterns
   - Batch inserts (10,000 rows) with periodic commits

3. **Incremental cache** (`release_years.json`):
   - Stores MBID → year and "artist|||album" → year mappings
   - Persists across sessions
   - Falls back to MusicBrainz API (rate-limited 1 req/sec) if not in DB

#### Usage in Analysis

**Release Year Lookups**:
```python
info = musicbrainz_db.lookup_release("Radiohead", "OK Computer")
# Returns: ReleaseInfo(year=1997, genres=["alternative rock", ...], ...)
```

Used by:
- `metadata catalog` — New vs back catalog breakdown
- `review` — Hidden gems (only artists with releases in review year)
- Year-over-year analysis

**Genre Analysis** (`metadata genres`):
```python
# Aggregate genres weighted by play count
for album in listened_albums:
    info = lookup_release(artist, album)
    if info and info.genres:
        for genre in info.genres:
            genre_plays[genre] += album_plays
```

Results show what genres you actually listen to most, not just what you've tried.

**Label Analysis** (`metadata labels`):
- Identifies label affinities (e.g., "You listen to a lot of Warp Records")
- Groups by label, weighted by plays

**Country/Language Analysis** (`metadata countries`):
- Geographic distribution of your listening
- ISO country codes (GB, US, JP, DE, etc.)

**Release Type Analysis** (`metadata types`):
- Breakdown of albums vs EPs vs singles vs compilations
- Shows listening preferences by format

### Lookup Strategy

```python
def lookup_release(artist, album):
    # 1. Exact match on normalized artist + title
    result = query("WHERE artist_norm = ? AND title_norm = ?")

    # 2. Partial artist match (handles "feat." variations)
    if not result:
        result = query("WHERE artist_norm LIKE ? AND title_norm = ?",
                       f"%{artist_norm}%")

    return result
```

### ReleaseInfo Dataclass

```python
@dataclass
class ReleaseInfo:
    artist: str           # "Radiohead"
    title: str            # "OK Computer"
    year: int             # 1997
    artist_mbid: str      # "a74b1b7f-71a5-4011-..." (UUID)
    release_type: str     # "album", "ep", "single", "compilation"
    country: str          # "GB" (ISO code)
    language: str         # "eng" (ISO code)
    genres: list[str]     # ["alternative rock", "art rock", ...]
    labels: list[str]     # ["Parlophone", "Capitol Records"]
```

---

## 9. Recommendation Algorithms

### Weighted Recommendations

Critics who share your taste get higher weight:

```python
weighted_score = sum(
    critic_overlap_percentage[critic]
    for critic in album.recommenders
)
```

Albums from aligned critics surface higher.

### MMR Diversification

**Maximal Marginal Relevance** balances relevance and diversity:

```python
mmr_score = lambda * relevance + (1 - lambda) * diversity

# Where:
# - relevance = normalized critic count
# - diversity = penalty if same artist already selected
# - lambda = 0.7 (favor relevance)
```

Prevents recommendation lists dominated by single artists:

```python
def apply_mmr_diversification(candidates, limit, lambda_param=0.7):
    selected = []
    while len(selected) < limit:
        best = max(
            remaining,
            key=lambda c: mmr_score(c, selected)
        )
        selected.append(best)
    return selected
```

### Similarity-Enhanced Recommendations

`critics unheard --show-similar` adds context:

```
Four Tet - Three
  Critics: 38 | Similar to: Floating Points, Caribou
```

For each recommendation, find which of YOUR artists are similar in critics-space:

```python
for your_artist in your_top_100:
    sim = cosine_similarity(rec_embedding, your_artist_embedding)
    if sim > 0.3:
        similar_from_yours.append(your_artist)
```

---

## 10. Prediction and Validation

### Critic Accuracy Tracking

Did old recommendations become favorites?

```python
# Load critic picks from reference_year
# Check your plays of those albums in subsequent years

you_played = albums with post_recommendation_plays > 0
you_loved = albums with post_recommendation_plays >= 10
```

### Critic Tracker

Follow aligned critics across years:

1. Find critics with overlap in **reference year**
2. Get their picks for **target year**
3. Score by sum of critic overlap percentages
4. Show unheard albums from your most aligned critics

```python
for pick in target_year_picks:
    pick["score"] = sum(
        critic_overlap[c]
        for c in pick["critics"]
        if c in aligned_critics
    )
```

### Regret Analysis

Albums you've been ignoring for years:

```python
years_ignored = current_year - first_recommendation_year

# Filter: known artist + high critic count + never heard
regrets = [
    album for album in critic_albums
    if not heard(album)
    and heard_artist(album.artist)
    and years_ignored >= 5
]
```

---

## Summary of Key Metrics

| Metric | Formula | Use Case |
|--------|---------|----------|
| Cosine Similarity | `dot(a,b) / (‖a‖ × ‖b‖)` | Artist similarity |
| Jaccard Similarity | `‖A ∩ B‖ / ‖A ∪ B‖` | Neighborhood overlap |
| Szymkiewicz-Simpson | `‖A ∩ B‖ / min(‖A‖, ‖B‖)` | Critic overlap |
| Explained Variance | `σ²_component / σ²_total` | Dimension importance |
| Kendall's Tau | Rank correlation | Trend detection |
| Silhouette Score | Cluster cohesion vs separation | Optimal cluster count |
| MMR Score | `λ × relevance + (1-λ) × diversity` | Diversified recommendations |

---

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                               │
└─────────────────────────────────────────────────────────────────────┘
       │                        │                        │
       ▼                        ▼                        ▼
┌─────────────┐        ┌─────────────────┐      ┌─────────────────┐
│  Last.fm    │        │  yearendlists   │      │   MusicBrainz   │
│  API        │        │  .com           │      │   Data Dumps    │
│  (scrobbles)│        │  (critics)      │      │   (metadata)    │
└─────────────┘        └─────────────────┘      └─────────────────┘
       │                        │                        │
       ▼                        ▼                        ▼
┌─────────────┐        ┌─────────────────┐      ┌─────────────────┐
│ recenttracks│        │ critics-YYYY    │      │ musicbrainz_    │
│ -*.csv      │        │ .json           │      │ releases.db     │
└─────────────┘        └─────────────────┘      └─────────────────┘
       │                        │                        │
       └────────────┬───────────┴────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         CORE PROCESSING                              │
└─────────────────────────────────────────────────────────────────────┘
       │                        │                        │
       ▼                        ▼                        ▼
┌─────────────┐        ┌─────────────────┐      ┌─────────────────┐
│ Co-listening│        │ Co-listing      │      │ Normalization   │
│ Matrix      │        │ Matrix          │      │ + Matching      │
└─────────────┘        └─────────────────┘      └─────────────────┘
       │                        │                        │
       ▼                        ▼                        │
┌─────────────┐        ┌─────────────────┐              │
│ User        │        │ Critics         │              │
│ Embeddings  │        │ Embeddings      │              │
│ (50-dim)    │        │ (50-dim)        │              │
└─────────────┘        └─────────────────┘              │
       │                        │                        │
       └────────────┬───────────┴────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           ANALYSIS                                   │
└─────────────────────────────────────────────────────────────────────┘
       │              │              │              │              │
       ▼              ▼              ▼              ▼              ▼
┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐
│ Similarity│  │ Clustering│  │ Trend     │  │ Cross-    │  │ Recommend │
│ Search    │  │ (HDBSCAN) │  │ Detection │  │ Reference │  │ (MMR)     │
└───────────┘  └───────────┘  └───────────┘  └───────────┘  └───────────┘
       │              │              │              │              │
       └──────────────┴──────────────┴──────────────┴──────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           OUTPUTS                                    │
│  Console Reports | HTML Visualizations | Spotify Playlists          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Next Generation Roadmap

Based on critique analysis and priority interviews, the following phases are planned:

### Phase A: Critics-as-Vectors (2-3 sessions)

Embed each critic as a vector in the same space as artists for better taste matching.

```
├── Add rank weighting to critics data loading
│   Weight = 1 / log2(rank + 1)
├── Create CriticVectorEmbeddings class
│   critic_vector = weighted_average(artist_vectors on their lists)
├── Modify critics matching to use vector similarity
│   Find critics nearest to your taste-vector
├── Add "critic drift" detection to `critics list`
│   Track alignment over time: "aligned in 2018, drifted by 2023"
└── Enhance `critics unheard` with critic-neighborhood scoring
    Weight recommendations by critic-to-you vector similarity
```

**Unlocks**: More nuanced critic matching, drift detection, better-weighted recommendations.

---

### Phase B: Evaluation Harness (1-2 sessions)

Add lightweight validation to prove improvements before/after model changes.

```
├── Future holdout test
│   Train embeddings on history up to year Y
│   Measure if nearest-neighbors predict Y+1 discoveries
├── Critic follow-through test
│   From aligned critics' past unheard picks
│   How many did you later play 10+ times?
└── Baseline current SVD embeddings
    Run tests, record metrics for comparison
```

**Unlocks**: Principled model selection, evidence for changes.

---

### Phase C: Continuous Familiarity (1-2 sessions)

Replace binary 5x5 with smooth album familiarity scoring.

```
├── Design familiarity scoring function
│   f(unique_tracks, total_plays, play_dispersion, recency) → 0.0-1.0
├── Replace get_albums_listened_to() with get_album_familiarity()
│   Returns dict of (artist, album) → familiarity score
├── Add --familiarity-threshold flag to relevant commands
│   critics matched --familiarity 0.6
│   critics taste-gaps --familiarity 0.2
└── Keep 5x5 as default threshold (familiarity ≥ 0.6)
    Backwards compatible, but now configurable
```

**Unlocks**: Nuanced album states, better taste-gap detection, configurable thresholds.

---

### Phase D: Session-Level Analysis (1-2 sessions)

Add session detection for more causal co-occurrence signals.

```
├── Add session detection to data.py
│   Session boundary = 30-60 minute gap between plays
├── Build session co-occurrence matrix option
│   Artists played in same session → stronger signal
├── Compare gateway artists at session vs week level
│   Session-level may be more "intentional"
└── Evaluate with holdout harness
    Which granularity predicts discoveries better?
```

**Unlocks**: More causal gateway detection, session-aware similarity.

---

### Phase E: Knowledge Graph (2-3 sessions)

Build a local knowledge graph for path explanations and multi-hop reasoning.

```
├── Create MusicKnowledgeGraph class (NetworkX)
│   Nodes: Artist, Album, Label, Genre, Critic, Year
│   Edges: PLAYED, SIMILAR_USER, SIMILAR_CRITICS, LISTED, ON_LABEL, HAS_GENRE
│
├── Graph construction from all data sources
│   Listening data → PLAYED edges with weight=log(plays+1)
│   Embeddings → SIMILAR_* edges (threshold=0.4 for sparsity)
│   Critics → LISTED edges with weight=1/log2(rank+1)
│   MusicBrainz → ON_LABEL, HAS_GENRE, RELEASED edges
│
├── Path finding algorithms
│   find_paths(source, target, max_hops=4)
│   explain(target_artist) → paths from your top artists
│   Path strength = product of edge weights
│
├── CLI commands
│   lastfm graph build [--force]
│   lastfm graph explain "Moor Mother"
│   lastfm graph path "Radiohead" "Burial"
│   lastfm graph neighborhood "Four Tet" [--hops 2]
│
└── Cache graph to ~/.cache/lastfm-analysis/{csv_hash}/knowledge_graph.pkl
```

**Sample output:**
```
$ lastfm graph explain "Moor Mother"

═══ Why Moor Mother? ═══

Path 1 (strength: 0.82)
  Shabaka ──SIMILAR_CRITICS(0.71)──> Moor Mother
  └── You played Shabaka 234 times

Path 2 (strength: 0.76)
  JPEGMAFIA ──SIMILAR_USER(0.65)──> Moor Mother
  └── You played JPEGMAFIA 189 times

Path 3 (strength: 0.68)
  Warp Records ──ON_LABEL──> Moor Mother
  └── You play 8 Warp artists (412 total plays)

Verdict: Strong connection via experimental adjacency and label affinity.
```

**Unlocks**: Explainable recommendations, multi-hop discovery, true bridge detection via betweenness centrality, label/genre exploration.

---

### Phase Dependencies

```
Phase A (Critics-as-Vectors)
    │
    ├──→ Phase B (Evaluation Harness) ←── can validate A
    │         │
    │         └──→ Phase D (Session-Level) ←── needs harness to compare
    │
    └──→ Phase C (Continuous Familiarity) ←── improves A's scoring
              │
              └──→ Phase E (Knowledge Graph) ←── uses all prior phases
```

**Recommended order**: A → B → C → D → E

Each phase is independently valuable, but later phases benefit from earlier ones.

---

## File Reference

| File | Purpose |
|------|---------|
| `data.py` | DataFrame loading, filtering, 5x5 criteria |
| `crossref.py` | Critic matching, normalization, overlap |
| `embeddings.py` | User + Critics embedding spaces |
| `musicbrainz_db.py` | Local MusicBrainz SQLite database |
| `release_years.py` | MusicBrainz API fallback + cache |
| `crawler.py` | yearendlists.com web scraper |
| `lastfm_api.py` | Last.fm API client |
| `spotify.py` | Spotify playlist integration |
| `commands/listen.py` | Listening analysis commands |
| `commands/critics.py` | Critic cross-reference commands |
| `commands/history.py` | Long-term pattern analysis |
| `commands/metadata.py` | MusicBrainz enrichment commands |
| `commands/visualize.py` | Calendar + genome visualizations |
