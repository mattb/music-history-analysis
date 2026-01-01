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

**Two ways to get your scrobble data:**

**Option A: Built-in API Downloader (Recommended)**
```bash
# 1. Get API key from https://www.last.fm/api/account/create
lastfm fetch-api-key --key YOUR_API_KEY

# 2. Download all scrobbles
lastfm fetch YOUR_USERNAME

# 3. Or download only from a specific year onwards (useful for updates)
lastfm fetch YOUR_USERNAME --start-year 2024

# Creates: recenttracks-USERNAME-TIMESTAMP.csv
```
- Downloads complete scrobble history directly from Last.fm API
- Same CSV format as external export websites
- Shows progress bar during download
- Handles pagination automatically (200 tracks per page)
- Rate-limited to respect Last.fm API (0.2s between requests)
- Auto-retries on 500 errors (3 attempts with 10-second backoff)
- Supports `--start-year` to fetch only recent scrobbles (faster updates)

**Option B: External Export Website**
- Use lastfm-to-csv or similar third-party tool
- Place CSV in project root as `recenttracks-*.csv`

**CSV Format:**
- **Location**: `recenttracks-*.csv` in project root
- **Columns**: `uts`, `utc_time`, `artist`, `artist_mbid`, `album`, `album_mbid`, `track`, `track_mbid`
- **Loading**: `data.load_scrobbles()` returns pandas DataFrame with parsed timestamps and `year` column

### 2. Critics' Year-End Lists
- **Source**: Scraped from yearendlists.com (2011-2025 available)
- **Cache**: `critics-YYYY.json` in project root (per year)
- **Scraper**: `lastfm/crawler.py` - uses httpx + BeautifulSoup
- **Structure**: List of `{critic, publication, albums: [{artist, title, rank}]}`
- **Refresh**: `lastfm critics fetch --year YYYY`

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
- **Setup**: `lastfm spotify auth --client-id X --client-secret Y`

## Key Modules

| Module | Purpose |
|--------|---------|
| `lastfm/cli.py` | Main CLI entry point with root commands (stats, overview, review, artist, fetch, fetch-api-key) and global options (~2600 lines) |
| `lastfm/commands/listen.py` | Listen command group: top, plays, discovered, abandoned, first (~450 lines) |
| `lastfm/commands/critics.py` | Critics command group: fetch, matched, unheard, overlap, list, who-listed, blind-spots, accuracy, tracker (~1200 lines) |
| `lastfm/commands/history.py` | History command group: loyalty, evolution (~600 lines) |
| `lastfm/commands/metadata.py` | Metadata command group: download, enrich, catalog, genres, labels, countries, types (~800 lines) |
| `lastfm/commands/spotify.py` | Spotify command group: auth, playlist (~300 lines) |
| `lastfm/data.py` | DataFrame loading, filtering, aggregation, album listening criteria (5x5), discovery/abandonment detection |
| `lastfm/crossref.py` | Cross-reference critics with listening history, normalization functions, uses album listening criteria |
| `lastfm/crawler.py` | yearendlists.com scraper |
| `lastfm/lastfm_api.py` | Last.fm API client for downloading scrobble history (~180 lines) |
| `lastfm/spotify.py` | Spotify OAuth + playlist creation |
| `lastfm/release_years.py` | MusicBrainz API integration (rate-limited) |
| `lastfm/musicbrainz_db.py` | Local MusicBrainz SQLite database |

## Album Listening Criteria

**Definition**: An album is only considered "listened to" if you've played at least **5 different tracks** from it, each at least **5 times**.

This prevents albums from being counted as "heard" when you've only played one or two tracks from them once or twice. It ensures you've genuinely engaged with the album.

**Implementation**: All album-related analysis uses `data.get_albums_listened_to(df)`:

```python
from lastfm import data

# Get albums that meet the 5x5 criteria
listened_albums = data.get_albums_listened_to(df)
# Returns: set of (artist, album) tuples
```

**What this affects**:
- Critics matched/unheard commands
- Review and overview reports
- Artist command comparisons
- All album statistics

If you need different criteria, modify the parameters:
```python
listened_albums = data.get_albums_listened_to(
    df,
    min_unique_tracks=3,  # At least 3 tracks
    min_plays_per_track=3  # Each played 3+ times
)
```

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

**Important**: Always check for NaN values before normalization:
```python
import pandas as pd

if pd.notna(artist) and pd.notna(album) and artist and album:
    key = (crossref.normalize_for_matching(artist),
           crossref.normalize_for_matching(album))
```

## All-Years Aggregation

Several critics commands default to searching **all available years** (2011-2025) rather than a single year. This is because:
1. Great music doesn't expire - a 2019 album can be discovered in 2025
2. Critics' historical picks help identify aligned taste-makers
3. Cross-year patterns reveal long-term critical consensus

**Commands with all-years by default:**
- `critics matched` - Shows all critic-approved albums you've heard from any year
- `critics unheard` - Recommendations from any year's lists
- `critics who-listed <artist>` - Shows artist's full critical history (2011-2025)
- `critics blind-spots` - Acclaimed albums from any year you've never heard

**How it works:**
```python
# Determine which years to search
if year is not None:
    years_to_search = [year]  # User specified --year
else:
    # Search all available years
    years_to_search = []
    for y in range(2011, 2026):
        json_path = get_critics_path(y)
        if json_path.exists():
            years_to_search.append(y)

# Aggregate results across years
all_results = []
for y in years_to_search:
    # Load and process each year...
    all_results.extend(year_results)
```

**Filtering to a specific year:**
```bash
# All years (default)
lastfm critics who-listed "Charli XCX"  # Shows 2013-2024

# Single year
lastfm --year 2024 critics who-listed "Charli XCX"  # Only 2024
```

## CLI Commands Reference

**Global Options**: `lastfm [--csv PATH] [--year YYYY] COMMAND`
- `--csv, -c`: Path to Last.fm CSV export (auto-detects `recenttracks-*.csv` if not specified)
- `--year, -y`: Filter to specific year (defaults to 2025)
- `--verbose, -v`: Verbose output

### Root Commands
- `lastfm fetch-api-key [--key KEY]` - Set up Last.fm API key for downloading scrobbles
- `lastfm fetch <username> [--output PATH] [--start-year YEAR]` - Download scrobble history via Last.fm API
- `lastfm stats` - Basic listening statistics for the year
- `lastfm overview [--html FILE]` - Comprehensive all-time listening overview (console or HTML export)
- `lastfm review [--html FILE]` - Comprehensive year-in-review (console or HTML export)
- `lastfm artist "Name"` - Deep dive on single artist across all years with critics' selections

### Listen Group (`lastfm listen ...`)
Basic listening analysis commands:
- `listen top [artists|albums|tracks] [-n NUM] [--unselected] [--new-album]` - Top plays with optional filters
- `listen plays [--artist NAME] [--days N]` - List recent plays with filters
- `listen discovered` - Artists first played in the specified year
- `listen abandoned` - Artists last played in the specified year (shows what you stopped listening to)
- `listen first <artist>` - When you first played an artist

### Critics Group (`lastfm critics ...`)
Cross-reference with music critics' year-end lists (defaults to all years 2011-2025 unless `--year` specified):
- `critics fetch [--year Y]` - Scrape critics' lists from yearendlists.com for a year
- `critics matched [-n NUM]` - Albums you've heard that critics loved (all years)
- `critics unheard [--weighted] [--known ARTIST]` - Recommended albums you haven't heard (all years)
- `critics overlap` - Summary stats of your alignment with critics
- `critics list [--sort overlap|albums|name]` - Per-critic breakdown
- `critics who-listed <artist>` - Which critics listed this artist (all years)
- `critics blind-spots [--min-critics N]` - Acclaimed albums you've never heard (all years)
- `critics accuracy [--year Y]` - Did old recommendations become favorites?
- `critics tracker [--ref-year Y] [--target-year Y]` - Follow aligned critics across years

### History Group (`lastfm history ...`)
Long-term listening pattern analysis:
- `history loyalty [--min-years N]` - Longtime fans, abandoned artists, rediscoveries
- `history evolution` - Detect musical eras and taste shifts with concentration analysis

### Metadata Group (`lastfm metadata ...`)
MusicBrainz enrichment (requires `metadata download` first):
- `metadata download [--force]` - Download full MusicBrainz DB (~3GB download)
- `metadata enrich [--limit N]` - Populate release year cache from local DB
- `metadata catalog` - New vs catalog analysis (backlist vs current releases)
- `metadata genres [-n NUM]` - Genre breakdown with evolution
- `metadata labels [-n NUM]` - Label breakdown
- `metadata countries [-n NUM]` - Release country breakdown
- `metadata types` - Album vs EP vs single breakdown

### Spotify Group (`lastfm spotify ...`)
Spotify playlist integration:
- `spotify auth [--client-id X] [--client-secret Y]` - Set up Spotify API credentials
- `spotify playlist [--type matched|missing|both]` - Create playlists from year-in-review data

## Key Analysis Patterns

### 1. Cross-Reference with Critics
```python
# Match your listening with critics' picks
results = crossref.match_with_history(critics_data, df_full, year=2024)
# Returns: {matched: [...], unheard: [...], stats: {...}}
```

### 2. Artist Discovery and Abandonment Detection
```python
# Find artists first played in a given year
discovered = data.artists_discovered_in_year(df_full, 2024)
# Returns DataFrame with first_play date, plays_in_year, etc.

# Find artists last played in a given year (what you stopped listening to)
abandoned = data.artists_abandoned_in_year(df_full, 2010)
# Returns DataFrame with last_play date, total_plays, plays_in_year, etc.
```

These complementary commands reveal:
- **discovered**: What you started listening to (entry points)
- **abandoned**: What you stopped listening to (exit points)
- Together they map your musical journey's beginning and end points

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

### 6. Hidden Gems (Review Command)
The "Hidden Gems" section shows artists you championed that critics missed, but only if they released albums in the review year:

```python
# Only include artists with albums actually released in review year
for ctx in artist_contexts:
    norm_name = crossref.normalize_for_matching(ctx["name"])
    if norm_name not in critics_artists and norm_name in new_album_artists:
        # Use MusicBrainz to verify release year
        info = musicbrainz_db.lookup_release(artist, album, conn)
        if info and info.year == year:
            overlooked_gems.append({...})
```

This prevents showing artists like David Bowie (no 2025 release) when generating a 2025 review, ensuring "Hidden Gems" are actual new releases that critics overlooked.

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

### Adding a New Command to a Command Group
```python
# In lastfm/commands/mygroup.py
import typer
from pathlib import Path
from typing import Optional
from rich.console import Console

from .. import data

app = typer.Typer(help="My command group description")
console = Console()

def get_csv_path(csv: Optional[Path] = None) -> Path:
    """Get CSV path from argument, glob, or error."""
    if csv and csv.exists():
        return csv

    # Auto-detect from glob
    csvs = list(Path.cwd().glob("recenttracks-*.csv"))
    if csvs:
        return sorted(csvs)[-1]  # Most recent

    console.print("[red]No CSV found. Provide --csv or place recenttracks-*.csv in current dir[/red]")
    raise typer.Exit(1)

@app.command(name="mycommand")
def mygroup_mycommand(
    ctx: typer.Context,
    limit: int = typer.Option(20, "--limit", "-n", help="Number of results"),
):
    """Description shown in --help."""
    # Get global options from context
    csv = ctx.obj.get("csv") if ctx.obj else None
    year = ctx.obj.get("year") if ctx.obj else None
    year = year if year is not None else 2025  # Default year

    # Load and filter data
    df = data.load_scrobbles(get_csv_path(csv))
    df = data.filter_by_year(df, year)

    # ... analysis ...
    console.print("[bold cyan]Results[/bold cyan]")
```

Then register it in `lastfm/cli.py`:
```python
from .commands import mygroup

app.add_typer(mygroup.app, name="mygroup")
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

## Overview vs Review

The CLI provides two comprehensive report commands:

**`overview`** - All-time listening analysis (entire history):
- Spans your complete listening history (e.g., 2005-2025)
- Top artists/albums all-time with 20-year sparklines
- Listening intensity timeline
- Discovery patterns over the years
- Longtime loyalty (artists played 10+ years)
- Peak obsessions (artist + their peak year)
- Critics alignment across all years (2011-2025)
- All-time genre/decade breakdown
- Produces both console and HTML output

**`review`** - Year-specific deep dive:
- Focuses on a single year (default: current year)
- Top artists/albums for that year
- New discoveries in that year
- Critics picks for that year
- Metadata breakdown for that year
- Produces both console and HTML output

## Testing Commands

```bash
# Download scrobbles from Last.fm API
uv run lastfm fetch-api-key --key YOUR_API_KEY
uv run lastfm fetch YOUR_USERNAME

# Download only from 2024 onwards (faster for updates)
uv run lastfm fetch YOUR_USERNAME --start-year 2024

# All-time overview (console)
uv run lastfm overview

# All-time overview (HTML)
uv run lastfm overview --html my-overview.html

# Basic stats with global --year option
uv run lastfm --year 2024 stats

# Listen commands
uv run lastfm --year 2024 listen top artists --limit 20
uv run lastfm --year 2024 listen discovered
uv run lastfm --year 2010 listen abandoned
uv run lastfm listen first "Radiohead"

# Critics analysis (all years by default)
uv run lastfm critics matched --limit 30
uv run lastfm critics who-listed "Charli XCX"
uv run lastfm critics unheard --weighted

# Critics for specific year
uv run lastfm --year 2024 critics matched

# Full review (console)
uv run lastfm --year 2024 review

# Full review (HTML)
uv run lastfm --year 2024 review --html 2024-review.html

# Metadata analysis (requires MusicBrainz DB)
uv run lastfm metadata download
uv run lastfm --year 2024 metadata genres
uv run lastfm --year 2024 metadata catalog

# History commands
uv run lastfm history loyalty --min-years 3
uv run lastfm history evolution

# Spotify integration
uv run lastfm spotify auth --client-id X --client-secret Y
uv run lastfm --year 2024 spotify playlist --type both
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
