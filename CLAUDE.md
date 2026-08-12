# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`bangumi-oped` is a data repository collecting anime Opening (OP) and Ending (ED) timestamps, indexed by [Bangumi](https://bgm.tv) Subject ID. It provides standardized timestamp data for third-party media players, plugins, and skipping extensions.

## Development & Execution Commands

There are no formal build, lint, or test suites. The sync pipeline uses standard Python 3.

- **Run Data Sync**: `python scripts/sync_oped.py`
- **Adjust Rate Limit Delay**: `REQUEST_DELAY=0.1 python scripts/sync_oped.py` (Default: `0.05` seconds between requests)

## Code Architecture & Sync Pipeline

The repository combines static data folders with an automated sync script (`scripts/sync_oped.py`) triggered weekly via GitHub Actions (`.github/workflows/sync-oped.yml`).

### External Data Flow
1. **`bangumi-data` CDN** (`unpkg.com`): Maps Bangumi Subject IDs to MyAnimeList (MAL) IDs.
2. **Bangumi API** (`api.bgm.tv`): Fetches total episode count per subject.
3. **AniSkip API** (`api.aniskip.com`): Retrieves OP/ED skip timestamp intervals by MAL ID and episode number.

### Lifecycle & State Logic (`scripts/sync_oped.py`)
- **Completed Anime (Sealed)**: Anime whose end date was over 90 days ago and already have a data file present are skipped entirely.
- **Ongoing / Cooldown Anime**: Anime currently airing or ended within the 90-day cooldown window are swept (`1` to `total_eps`) on each run to pick up newly added skip times.
- **State Persistence**: Active ongoing items and `bangumi-data` hashes are tracked in `.state.json`.
- **Error Recovery**: API calls retry up to 5 times with exponential backoff on HTTP 429/5xx errors. Unrecoverable API failures trigger `sys.exit(1)` to prevent corrupting state or making partial commits.

## Repository Data Conventions

### Directory & File Structure
Each anime entry is stored in `<Subject_ID>/`:
- `<Subject_ID>/<Subject_ID>.txt`: Semicolon-delimited timestamp data file.
- `<Subject_ID>/<Anime Title>`: Empty marker file using Chinese title (or original title if Chinese is unavailable) for human readability. Forbidden path characters (`: / \ ? * " < > |`) are converted to full-width characters or omitted. Parsers must ignore this marker file.

### Timestamp Format (`<Subject_ID>.txt`)
- Line format: `Episode;OP_Start;OP_End;ED_Start;ED_End` (5 semicolon-delimited fields).
- All timestamps are rounded integer seconds.
- **Sentinel Value (`-1`)**: Missing OP or ED fields explicitly use `-1` (e.g. `2;-1;-1;2400;2500`). `0` is a valid timestamp and cannot be used for missing segments.
- **Episode Numbering**: Numbering must start from `1` for each Subject ID, regardless of franchise-wide episode counts.
- **Parsing Rules**: Evaluated by explicit episode number; duplicate episode entries prioritize the first occurrence.
