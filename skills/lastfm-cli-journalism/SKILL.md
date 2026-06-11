---
name: lastfm-cli-journalism
description: Use when turning a Last.fm listening-history repo and its agent-native lastfm CLI into narrative music analysis, playlist drafts, critic-alignment stories, release-era investigations, or evidence-based interview questions.
---

# Last.fm CLI Journalism

Use this skill to investigate listening history as source material for music-data journalism. The goal is not to memorize commands or dump statistics; it is to use the repo's `lastfm` CLI to gather evidence, form a story, and deliver the artifact the user asked for.

## Start With The CLI Contract

1. Run `lastfm --help` from the repo context.
2. Run command-specific `--help` for the small set of commands that match the question.
3. Prefer the live help output over remembered syntax.
4. Use the newest auto-detected `recenttracks-*.csv` unless the user names a CSV or time window that requires another source.

For multi-step work, start or reuse a daemon session so repeated probes share the same CSV, cache, and context. For a small lookup, a one-shot command with `--csv` is enough.

## Evidence Discipline

Prefer JSON or NDJSON output when available. Treat structured stdout as evidence: inspect it, keep compact snippets or summaries, and cite the command-derived facts behind narrative claims.

Keep the evidence trail small but real:

- command purpose
- time window or CSV used
- key fields or aggregates inspected
- uncertainty, missing metadata, or scrobble gaps

Batch work when the CLI supports it. Do not delete or regenerate large cache/source data unless the user explicitly asks.

## Investigation Workflow

1. Restate the user's artifact: narrative, playlist, interview guide, blind-spot map, or ranked findings.
2. Identify hard constraints first: years, months, release years, artists, tracks, critic sources, or "only listened to" rules.
3. Build a timeline of personal listening before writing conclusions.
4. Compare play-time with release-time. "Played in 2025" and "released in 2025" are different evidence sets.
5. Add critic context: acclaimed releases heard, acclaimed releases absent, late discoveries, and alignment changes.
6. Look for anomalies and absences: sudden spikes, collapses, dormant returns, skipped months, ignored canonical records, or aliases/matching gaps.
7. Convert the findings into the requested form with statistics used as support, not as the main event.

## Output Patterns

For a year or era narrative, organize around chronology and turning points. Name the months or years where the listening changed, then support those claims with personal plays, release-era context, critic overlap, and notable absences.

For playlist requests, respect hard constraints exactly. If the user says only tracks listened to in a period, do not include tracks outside it. Prefer release-year matches when requested, but label exceptions or uncertain release years. Draft a running order with flow logic, not just a top-played list.

For interview prompts, use tool results to motivate revealing questions. Ask about contradictions, sudden changes, critic-aligned records the user ignored, artists that returned after dormancy, months that look emotionally or socially specific, and gaps that may reflect non-scrobbled life.

For blind spots or recommendations, triangulate personal affinity, critic/release context, and actual absence. Label each suggestion as a likely bridge, a deliberate challenge, or a historical gap.

## Judgment Rules

- Lead with story, then cite the smallest useful statistics.
- Treat listening data as biography, but never as complete biography.
- Name absences as evidence while acknowledging metadata and scrobble limitations.
- Avoid generic taste questions or generic music criticism.
- Use the repo's writing style when producing narrative prose: concise, first-person-compatible, and evidence-led.
