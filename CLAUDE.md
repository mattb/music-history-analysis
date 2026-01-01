# CLAUDE.md - Project Guide for AI Assistants

## Project Overview

This is a CLI tool for deep analysis of Last.fm listening history, cross-referenced with music critics' year-end album lists. It answers questions like:
- Which critically-acclaimed albums have I never explored?
- Which critics share my taste? What are they recommending now?
- How has my taste evolved over 20 years?
- What genres/labels/countries dominate my listening?
- Am I keeping up with new releases or living in the past?

## Data Sources & Strategy

### 1. Last.fm Scrobbles (Primary)
- **Source**: CSV export from Last.fm (use lastfm-to-csv or similar tool)
- **Location**: `recenttracks-*.csv` in project root
- **Key columns**: `timestamp`, `artist`, `album`, `track`, `album_mbid`
- **Loading**: `data.load_scrobbles()` returns pandas DataFrame with parsed timestamps and `year` column

### 2. Critics' Year-End Lists
- **Source**: Scraped from yearendlists.com (2011-2025 available)
- **Cache**: `critics/YYYY.json` per year
- **Scraper**: `lastfm/crawler.py` - uses httpx + BeautifulSoup
- **Structure**: List of `{critic, publication, albums: [{artist, title, rank}]}`
- **Refresh**: `lastfm crawl --year YYYY`

### 3. MusicBrainz Metadata (Enrichment)
- **Source**: MusicBrainz JSON data dumps (https://data.metabrainz.org)
- **Dump cache**: `~/.cache/lastfm-analysis/musicbrainz-release-YYYYMMDD-HHMMSS.tar.xz` (~2-3GB)
- **Local DB**: `~/.cache/lastfm-analysis/musicbrainz_releases.db` (SQLite, ~1GB)
- **Contains**: Release year, genres, labels, country, language, release type, artist MBID
- **Schema**:
  ```sql
  releases (id, artist_credit, title, year, artist_norm, title_norm,
            artist_mbid, release_type, country, language, genres, labels)
  release_genres (release_id, genre, count)
  release_labels (release_id, label_name, catalog_number)
  ```
- **Setup**: `lastfm download-musicbrainz` (first run downloads ~3GB, subsequent runs use cache)
- **Re-process**: Just re-run the command (uses cached dump, rebuilds DB)
- **Force re-download**: `lastfm download-musicbrainz --force`
- **Why local**: MusicBrainz API rate-limits to 1 req/sec; local DB is instant

### 4. Release Year Cache (Incremental)
- **Location**: `~/.cache/lastfm-analysis/release_years.json`
- **Purpose**: Cache release years from both local DB and API lookups
- **Keys**: Either MusicBrainz MBID or `"artist|||album"` lowercase

### 5. Spotify API (Optional)
- **Purpose**: Create playlists from recommendations
- **Credentials**: `~/.cache/lastfm-analysis/spotify_credentials.json`
- **Setup**: `lastfm spotify-auth --client-id X --client-secret Y`

## Key Modules

| Module | Purpose |
|--------|---------|
| `lastfm/cli.py` | All CLI commands (typer-based), ~3000 lines |
| `lastfm/data.py` | DataFrame loading, filtering, aggregation |
| `lastfm/crossref.py` | Cross-reference critics with listening history |
| `lastfm/crawler.py` | yearendlists.com scraper |
| `lastfm/spotify.py` | Spotify OAuth + playlist creation |
| `lastfm/release_years.py` | MusicBrainz API integration (rate-limited) |
| `lastfm/musicbrainz_db.py` | Local MusicBrainz SQLite database |

## Normalization Strategy

Matching albums across sources (Last.fm, critics, MusicBrainz) requires normalization:

```python
def normalize_for_matching(s: str) -> str:
    """Normalize string for fuzzy matching."""
    s = s.lower().strip()
    # Remove parentheticals like "(Deluxe Edition)"
    s = re.sub(r'\s*\([^)]*\)', '', s)
    # Remove special characters
    s = re.sub(r'[^\w\s]', '', s)
    return ' '.join(s.split())
```

This handles:
- Case differences: "Radiohead" vs "radiohead"
- Edition suffixes: "OK Computer (Remastered)" → "ok computer"
- Punctuation: "What's Going On" → "whats going on"

## CLI Commands Reference

### Core Analysis
- `lastfm stats [--year Y]` - Basic listening statistics
- `lastfm top artists|albums|tracks [--year Y] [-n N]` - Top plays
- `lastfm artist "Name"` - Deep dive on single artist across all years

### Critics Cross-Reference
- `lastfm crawl --year Y` - Fetch critics' lists for a year
- `lastfm critics --year Y` - Show critics' picks you've heard
- `lastfm blind-spots [--year Y]` - Acclaimed albums you've never played
- `lastfm critic-tracker --from-year Y --to-year Y` - Find aligned critics

### Historical Analysis
- `lastfm loyalty` - Long-term fans, abandoned artists, rediscoveries
- `lastfm evolution` - Detect musical eras and taste shifts
- `lastfm critic-accuracy` - Did past recommendations become favorites?

### MusicBrainz Metadata
- `lastfm download-musicbrainz` - Download full MusicBrainz DB (~3GB download)
- `lastfm enrich-releases [--limit N]` - Populate release year cache
- `lastfm genres [--year Y]` - Genre breakdown with evolution
- `lastfm labels [--year Y]` - Label breakdown
- `lastfm countries [--year Y]` - Release country breakdown
- `lastfm release-types [--year Y]` - Album vs EP vs single
- `lastfm catalog [--year Y]` - New vs catalog analysis

### Reports
- `lastfm review --year Y [--html FILE]` - Comprehensive year-in-review

### Spotify Integration
- `lastfm spotify-auth` - Set up Spotify credentials
- `lastfm spotify-playlist --year Y --type critics|recs` - Create playlist

## Key Analysis Patterns

### 1. Cross-Reference with Critics
```python
# Match your listening with critics' picks
results = crossref.match_with_history(critics_data, df_full, year=2024)
# Returns: {matched: [...], unheard: [...], stats: {...}}
```

### 2. Artist Discovery Detection
```python
# Find artists first played in a given year
discovered = data.artists_discovered_in_year(df_full, 2024)
# Returns DataFrame with first_play date, plays_in_year, etc.
```

### 3. Sparkline Visualization
Used for showing 20-year listening patterns:
```python
blocks = " ▁▂▃▄▅▆▇█"
sparkline = "".join(blocks[int(v/max_v * 8)] for v in values)
# Example: "▁▂▃▅█▇▃▂▁▁" shows a peak in middle years
```

### 4. Weighted Recommendations
Critics who share your taste get higher weight:
```python
for album in unheard_albums:
    score = sum(critic_alignment_pct[c] for c in album.critics)
# Higher score = recommended by critics who "get" you
```

### 5. Genre/Label Lookups from MusicBrainz
```python
from lastfm import musicbrainz_db
info = musicbrainz_db.lookup_release("Radiohead", "OK Computer")
# Returns: ReleaseInfo(year=1997, genres=["alternative rock", ...],
#                      labels=["Parlophone"], country="GB", ...)
```

## HTML Report Generation

The `review` command generates pure HTML+CSS reports (no JavaScript):
- Dark theme with gradient accents
- CSS Grid for responsive layouts
- Conic-gradient donut charts for percentages
- Bar charts using `width: N%` on colored divs
- Mobile-responsive with `@media` queries

## Performance Considerations

1. **MusicBrainz lookups**: Always use local SQLite DB, not API
2. **Large DataFrames**: Filter by year early to reduce memory
3. **Album grouping**: Use `groupby(["artist", "album"])` not row iteration
4. **Batch inserts**: SQLite uses 10,000-row batches with periodic commits
5. **Progress bars**: Use `rich.progress` for long operations

## Common Extension Patterns

### Adding a New Command
```python
@app.command()
def my_command(
    csv: Optional[Path] = typer.Option(None, "--csv", "-c"),
    year: Optional[int] = typer.Option(None, "--year", "-y"),
):
    """Description shown in --help."""
    df = data.load_scrobbles(get_csv_path(csv))
    if year:
        df = data.filter_by_year(df, year)
    # ... analysis ...
    console.print("[bold cyan]Results[/bold cyan]")
```

### Adding to HTML Review
1. Gather data in `review()` function after "GATHER ALL DATA" section
2. Add to `generate_review_html()` parameters
3. Build HTML fragment with f-string
4. Add CSS in the `<style>` block
5. Insert `{my_section}` in template

### New MusicBrainz Field
1. Add column to `releases` table in `init_database()`
2. Extract in `extract_release_info()`
3. Add to `ReleaseInfo` dataclass
4. Update `lookup_release()` SELECT query
5. Add index if needed for queries

## Testing Commands

```bash
# Basic stats
uv run lastfm stats --year 2024

# Critics analysis
uv run lastfm critics --year 2024

# Full review (console)
uv run lastfm review --year 2024

# Full review (HTML)
uv run lastfm review --year 2024 --html 2024-review.html

# Genre analysis (requires MusicBrainz DB)
uv run lastfm genres --year 2024
```

## Dependencies

Key packages (see pyproject.toml):
- `typer` - CLI framework
- `pandas` - Data manipulation
- `rich` - Console formatting, progress bars, tables
- `httpx` - HTTP client for crawling
- `beautifulsoup4` + `lxml` - HTML parsing
- `spotipy` - Spotify API
- `musicbrainzngs` - MusicBrainz API (fallback only)
