# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

`bangumi-oped` is a data repository collecting anime Opening (OP) and Ending (ED) timestamps, indexed by [Bangumi](https://bgm.tv) Subject ID. It is designed for consumption by third-party players, plugins, and tools to automate OP/ED skipping.

## Repository Structure & Data Conventions

This repository contains no build, lint, or test scripts. It consists strictly of data folders organized by Bangumi Subject ID.

### Directory Organization
Each anime entry is stored in a folder named after its Bangumi Subject ID (e.g. `622206/`):
- `<Subject_ID>/<Subject_ID>.txt`: Data file containing episode timestamps.
- `<Subject_ID>/<Anime Title>`: An **empty** file named with the anime title (preferably Chinese title from bgm.tv, or original title if no Chinese title exists) for human identification. Parsers must ignore this file. Forbidden filesystem characters (`: / \ ? * " < > |`) should be replaced with full-width characters or omitted.

### Timestamp Format (`<Subject_ID>.txt`)
- Line-separated entries with 5 semicolon-delimited (`;`) fields:
  `Episode;OP_Start;OP_End;ED_Start;ED_End`
- All timestamps are in seconds (integer).
- **Sentinel Value (`-1`)**: Missing OP or ED fields must explicitly use `-1` (e.g., `2;-1;-1;2400;2500`). Empty fields or using `0` for missing segments are not permitted (`0` is a valid timestamp).
- **Episode Numbering**: Episodes must start from `1` for each Subject ID, even if bgm.tv lists continuous episode numbers for sequel seasons.
- **Parsing Rules**: Parsers evaluate lines by explicit episode number regardless of line order. If duplicate episode numbers exist in a file, the first entry takes precedence.
