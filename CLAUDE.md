# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`bangumi-oped` is an open-source database of anime Opening (OP) and Ending (ED) timestamps indexed by [Bangumi](https://bgm.tv) Subject ID. It powers skip features in third-party media players, plugins (e.g., Jellyfin, Emby, Plex), and browser extensions.

## Execution & Environment Configuration

The repository uses Python 3 standard library exclusively (no external pip dependencies). There are no formal build, lint, or test suites.

- **Run Data Sync**: `python scripts/sync_oped.py`
- **Environment Variables (`scripts/sync_oped.py`)**:
  - `DATA_DIR`: Base directory where subject folders and `.state.json` are stored (Default: `.`).
  - `INITIAL_SWEEP`: Fast initial sweep mode flag (`1` or `0`, Default: `1`). Skips existing folders to complete initial database population.
  - `REQUEST_DELAY`: Inter-request delay in seconds (Default: `0.05`).
  - `MAX_PROCESS_REQUESTS`: Maximum API requests per execution batch before clean exit (Default: `1000`).
  - `MAX_RUN_TIME_SECONDS`: Runtime limit in seconds before clean exit (Default: `840` / 14 mins).
  - `ONGOING_CHECK_INTERVAL_HOURS`: Cooldown in hours between re-checking ongoing anime (Default: `24`).
  - `RECHECK_WINDOW`: Number of recent episodes to re-check during sweeps (Default: `6`).
  - `MAX_ANIME_AGE_DAYS`: Maximum age of anime in days based on start date (Default: `365` / 1 year, `0` to disable). Filters out older legacy shows and long-running continuous series.

## Dual-Branch Architecture

The repository operates across two git branches:
- **`master` branch**: Source code (`scripts/sync_oped.py`), GitHub Action workflow (`.github/workflows/sync-oped.yml`), and documentation.
- **`data` branch**: Contains all generated `<Subject_ID>/` data folders and `.state.json`.
- **CI Synchronization**: The GitHub Action checks out `master` and checks out `data` into `data_workspace/`, running `DATA_DIR=data_workspace python scripts/sync_oped.py` before committing changes back to `data`.

## Code Architecture & Sync Pipeline (`scripts/sync_oped.py`)

### External API Dependencies
1. **`bangumi-data` CDN** (`unpkg.com/bangumi-data`): Maps Bangumi Subject IDs to MyAnimeList (MAL) IDs.
2. **Bangumi API** (`api.bgm.tv/v0/episodes`): Fetches total episode count (`type=0` normal episodes).
3. **AniSkip API** (`api.aniskip.com/v2/skip-times`): Retrieves OP/ED timestamp intervals by MAL ID and episode number.

### Lifecycle & Caching Rules
- **Anime Age Filtering**: Automatically filters out anime whose start date (`begin`) is older than `MAX_ANIME_AGE_DAYS` (default: 365 days / 1 year), effectively excluding older legacy shows and multi-decade continuous series (e.g. Detective Conan, One Piece, Sazae-san). Obsolete ongoing state entries for filtered anime are purged automatically.
- **Initial Fast Sweep Mode**: When `INITIAL_SWEEP=1` and `initial_sweep_done` is `false` in `.state.json`, any existing subject folder is skipped to dedicate 100% of request quota to populating missing subjects. Once all subjects are scanned without hitting request limits, `initial_sweep_done` is automatically set to `true`.
- **Sealed (Completed) Anime**: Ended >90 days ago with data present; skipped entirely to eliminate redundant requests.
- **LRU Dynamic Queue Maintenance**: In maintenance mode (`initial_sweep_done=true`), ongoing subjects are sorted by `last_check` timestamp ascending (oldest checked first). Checked subjects update `last_check` and move to the end of the queue, ensuring all ongoing subjects are polled fairly without starvation.
- **Ongoing / Cooldown Anime**: Airing anime or ended <=90 days ago; re-checked if last check was prior to `ONGOING_CHECK_INTERVAL_HOURS`.
- **Smart 3-Tier Caching & Field-Level Merge**: Complete episodes (valid OP & ED) outside `RECHECK_WINDOW` are locked permanently. Recent episodes inside `RECHECK_WINDOW` (default: 6 eps) are always checked. Incomplete episodes (`-1` present) outside the window are re-checked during LRU ongoing sweeps. Field-level merging ensures existing valid OP or ED timestamps are preserved if AniSkip only has one segment.
- **State File (`.state.json`)**: Persists `bangumi-data_hash` and timestamps/metadata for active `ongoing` entries.
- **Fault Tolerance**: API calls retry up to 5 times with exponential backoff on HTTP 429/5xx errors. Unrecoverable failures invoke `sys.exit(1)` to abort without committing corrupted state.

## Repository Data Conventions

### Folder & File Layout (`<Subject_ID>/`)
- `<Subject_ID>/<Subject_ID>.txt`: Main semicolon-delimited timestamp file.
- `<Subject_ID>/<Anime Title>`: Empty marker file with Chinese title (or original title if Chinese is unavailable) for human readability.
- **Filename Sanitization**: Forbidden characters (`: / \ ? * " < > |`) are converted to full-width equivalents (`： ／ ＼ ？ ＊ ＂ ＜ ＞ ｜`) and control characters are stripped. Parsers must ignore this marker file.

### Timestamp Format (`<Subject_ID>.txt`)
- **Line Format**: `Episode;OP_Start;OP_End;ED_Start;ED_End` (5 semicolon-delimited fields, integer seconds).
- **Sentinel Value (`-1`)**: Missing OP or ED segments explicitly use `-1` (e.g. `2;-1;-1;2400;2500`). `0` is a valid timestamp (starts at 0s) and cannot represent missing segments.
- **Episode Numbering**: Numbering starts at `1` for each Subject ID, relative to that specific entry.
- **Parsing Rules**: Parsers evaluate by explicit episode number field and prioritize the first occurrence on duplicate lines.
