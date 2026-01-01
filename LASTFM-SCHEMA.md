# Last.fm Listening History Export Schema

## File Overview

- **Filename pattern:** `recenttracks-{username}-{export_timestamp}.csv`
- **Format:** CSV with header row
- **Encoding:** UTF-8 (with some legacy encoding artifacts in older entries)
- **Ordering:** Descending by timestamp (most recent plays first)

## Columns

| Column | Type | Required | Description |
|--------|------|----------|-------------|
| `uts` | integer | Yes | Unix timestamp (seconds since epoch) when the track was scrobbled |
| `utc_time` | string | Yes | Human-readable UTC timestamp in format `DD Mon YYYY, HH:MM` |
| `artist` | string | Yes | Artist name as submitted to Last.fm |
| `artist_mbid` | string | No | MusicBrainz artist ID (UUID format, may be empty) |
| `album` | string | No | Album name (may be empty if not provided by scrobbler) |
| `album_mbid` | string | No | MusicBrainz album/release ID (UUID format, may be empty) |
| `track` | string | Yes | Track name |
| `track_mbid` | string | No | MusicBrainz track/recording ID (UUID format, may be empty) |

## Field Details

### Timestamps
- `uts`: Primary timestamp for sorting and filtering. Integer seconds since Unix epoch (1970-01-01 00:00:00 UTC)
- `utc_time`: Formatted for human readability. Example: `31 Dec 2025, 21:38`

### MusicBrainz IDs
MusicBrainz IDs are optional enrichment data. Coverage varies:
- More recent scrobbles tend to have better MBID coverage
- Obscure or independent artists may lack MBIDs
- Empty values are represented as empty strings `""`

### Known Data Quirks
1. **Bulk imports:** The earliest entries (e.g., Feb 2005) may share identical timestamps if the user bulk-imported their listening history when joining Last.fm
2. **Encoding issues:** Some older entries may have encoding artifacts (e.g., `K�hncke` instead of `Köhncke`)
3. **Unicode in names:** Some artists use non-standard Unicode characters in their names (e.g., `⣎⡇ꉺლ༽இ•̛)ྀ◞`)
4. **Remixes/variants:** Track names may include remix info (e.g., `"Cloudy - Kelbin Remix"`)

## Example Entries

```csv
uts,utc_time,artist,artist_mbid,album,album_mbid,track,track_mbid
"1767217094","31 Dec 2025, 21:38","Oneohtrix Point Never","9cea062d-d476-447f-98b4-e67e14bfd1e4","Tranquilizer","dda7e98b-6d8d-4c95-9673-b5902e7043e5","Modern Lust","da5d357e-8807-479c-a69c-e839117e812a"
"1108334827","13 Feb 2005, 22:47","Bloc Party","8c538f11-c141-4588-8ecb-931083524186","","","Positive Tension","6e084ae1-abc3-4571-8d40-dc0e691de164"
```

## Statistics (as of export)

- **Total scrobbles:** ~141,311
- **Date range:** February 2005 - December 2025 (~20 years)
- **Export timestamp:** 1767217094 (31 Dec 2025, 21:38 UTC)
