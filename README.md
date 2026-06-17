# Music History

Music History is a Codex plugin for analyzing listening history with a local Python CLI. It helps Codex turn Last.fm scrobbles, Spotify exports, critics lists, and MusicBrainz metadata into evidence-backed music analysis, playlists, taste maps, and narrative drafts.

This README is for colleagues who want to install the plugin in Codex and run it against their own data.

## Install

Clone this repository into `~/plugins/music-history`, then run the bundled installer. It registers the standard user-wide `local-plugins` marketplace, adds this checkout to that marketplace, and installs the plugin into Codex's active cache.

```bash
git clone https://github.com/mattb/music-history-analysis.git ~/plugins/music-history
python3 ~/plugins/music-history/scripts/install_codex_plugin.py
```

The installer is idempotent and preserves other entries in `~/.agents/plugins/marketplace.json`. To refresh after pulling repository updates, run it again:

```bash
git -C ~/plugins/music-history pull
python3 ~/plugins/music-history/scripts/install_codex_plugin.py
```

Quit and reopen Codex after a first install or after skill metadata changes. The installed plugin key is `music-history@local-plugins`.

The plugin exposes the `$music-history-cli-journalism` skill and the `music-history` command-line tool.

To verify the Codex surface after reloading, use:

```text
$music-history-cli-journalism First confirm this skill is available. Then walk me through a demo showing the realistic user experience of this skill, including me in the loop where needed.
```

## Local Setup

Install the Python environment with `uv` from the repo root:

```bash
uv sync
music-history --help
```

You need at least one listening-history input:

- A Last.fm-compatible CSV named `recenttracks-*.csv` in the working directory, or passed with `--csv`.
- A Spotify Extended Streaming History export converted with `music-history spotify convert <dir>`.

Optional setup:

- Last.fm API key for fetching scrobbles directly: `music-history fetch-api-key --key YOUR_API_KEY`.
- Spotify credentials for playlist creation: `music-history spotify auth --client-id X --client-secret Y`.
- MusicBrainz metadata cache for release years, labels, countries, and release types: `music-history metadata download`.

Music History stores generated caches under `~/.cache/music-history-analysis/`. Some MusicBrainz and embedding caches are large; do not delete them casually unless you intend to rebuild them.

## Quick Start

```bash
music-history --help
music-history stats
music-history --csv recenttracks-USER-TIMESTAMP.csv stats
music-history session-start --session-id music-2025 --csv recenttracks-USER-TIMESTAMP.csv --json
music-history listening-stats --session music-2025 --json
```

In Codex, try:

```text
Use $music-history-cli-journalism to analyze my listening history and find critic-list blind spots.
```

The skill will inspect the live CLI help before choosing commands, then use JSON output and session workflows when they fit the task.

## Data Notes

The expected scrobble CSV columns are:

```text
uts, utc_time, artist, artist_mbid, album, album_mbid, track, track_mbid
```

Critics lists live as `critics-YYYY.json` files in the repo root. The CLI defaults to all available critics years unless you pass `--year`.
