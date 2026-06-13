# AGENTS.md - Repository Guide for Coding Agents

## Overview
This repo contains a Python CLI (`lastfm`) for analyzing Last.fm listening history and cross-referencing it with critics' year-end lists. It also supports metadata enrichment via MusicBrainz, optional Spotify integration, and agent-native command/session workflows.

## Key Entry Points
- CLI entry point: `lastfm` (defined in `pyproject.toml`)
- Primary CLI implementation: `lastfm/cli.py`
- Command groups live in: `lastfm/commands/`
- Agent-native CLI: use top-level `lastfm` commands such as `lastfm session-start`, `lastfm listening-stats`, and `lastfm blind-spots`.
- Long-lived agent sessions use daemon metadata and sockets under `~/.cache/lastfm-analysis/sessions/<session-id>/`.

## Agent Sessions
- Named session metadata persists after its daemon exits. The daemon exits after 30 minutes without an active request.
- Analysis commands that use `--session` transparently restart an exited daemon from the persisted absolute CSV path and retry the request once.
- `session-list` and `session-status` are passive: they report whether the daemon is running without waking it.
- If the persisted source CSV is missing or has moved, start a new session with `session-start` and the current CSV path.

## Data Files and Formats
- Scrobbles CSV: `recenttracks-*.csv` in repo root (auto-detected)
  - Columns: `uts`, `utc_time`, `artist`, `artist_mbid`, `album`, `album_mbid`, `track`, `track_mbid`
- Critics lists: `critics-YYYY.json` in repo root
- MusicBrainz cache and DB: `~/.cache/lastfm-analysis/` (large, do not delete)
- Embedding caches: `~/.cache/lastfm-analysis/{csv_hash}/` and `~/.cache/lastfm-analysis/critics_embeddings/`
- Spotify credentials: `~/.cache/lastfm-analysis/spotify_credentials.json`
- Release-year cache: `~/.cache/lastfm-analysis/release_years.json`
- Spotify Extended Streaming History can be converted via `lastfm spotify convert <dir>`

## Domain Conventions
- Album familiarity uses a continuous score (0.0-1.0); default CLI threshold is `--familiarity 0.4`.
- Matching across sources uses normalization and fuzzy matching; MusicBrainz IDs are preferred when available.
- Critics data is scraped from yearendlists.com via `lastfm/crawler.py`.
- Critics commands default to all available years (2011-2025) unless `--year` is provided.

## How to Run
- The CLI is exposed as `lastfm` from the project script entry.
- Typical usage:
  - `lastfm stats`
  - `lastfm critics matched`
  - `lastfm --csv recenttracks-USER-TIMESTAMP.csv stats`
  - `lastfm session-start --session-id music-2025 --csv recenttracks-USER-TIMESTAMP.csv --json`
  - `lastfm listening-stats --session music-2025 --json`
- The CLI auto-detects the newest `recenttracks-*.csv` in the current directory.

## Writing Style (when generating narrative content)
- Follow `WRITING-STYLE.md` (first-person, concise, hack-diary tone).

## Testing
- Automated tests use pytest. Run `uv run --extra dev python -m pytest` before committing agent CLI or analysis changes.

## Areas You Might Edit
- CLI behavior and commands: `lastfm/cli.py`, `lastfm/commands/*.py`
- Data modeling and scoring: `lastfm/data.py`, `lastfm/crossref.py`
- Metadata integration: `lastfm/musicbrainz_db.py`, `lastfm/release_years.py`
- Critics scraping: `lastfm/crawler.py`
- Embeddings and evaluation: `lastfm/embeddings.py`, `lastfm/evaluation.py`

## Guardrails
- Avoid deleting or regenerating large cached datasets unless explicitly requested.
- Preserve existing data files in the repo root (CSV/JSON/HTML).
