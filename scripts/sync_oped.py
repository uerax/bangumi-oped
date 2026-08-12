#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import hashlib
from datetime import datetime, timezone

BANGUMI_DATA_URL = "https://unpkg.com/bangumi-data@0.3/dist/data.json"
ANISKIP_URL = "https://api.aniskip.com/v2/skip-times/{mal_id}/{ep}?types=op&types=ed&episodeLength=0"
STATE_FILE = ".state.json"
USER_AGENT = "Mozilla/5.0 (compatible; bangumi-oped-sync/1.0)"

FORBIDDEN_CHARS = {
    ":": "：",
    "/": "／",
    "\\": "＼",
    "?": "？",
    "*": "＊",
    '"': "＂",
    "<": "＜",
    ">": "＞",
    "|": "｜",
}


def sanitize_filename(name: str) -> str:
    for char, replacement in FORBIDDEN_CHARS.items():
        name = name.replace(char, replacement)
    # Remove control characters and leading/trailing spaces or dots
    name = re.sub(r'[\x00-\x1f\x7f]', '', name).strip(' .')
    return name


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {STATE_FILE}: {e}")
    return {"bangumi_data_hash": "", "ongoing": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_bangumi_data() -> tuple[dict, str]:
    print("Fetching bangumi-data...")
    req = urllib.request.Request(BANGUMI_DATA_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req) as res:
        content = res.read()
        data_hash = hashlib.sha256(content).hexdigest()
        data = json.loads(content.decode("utf-8"))
        return data, data_hash


def is_anime_ended(end_str: str) -> bool:
    if not end_str:
        return False
    try:
        # ISO format e.g. "1943-04-14T15:16:00.000Z" or "2024-03-29"
        end_str_clean = end_str.replace("Z", "+00:00")
        end_date = datetime.fromisoformat(end_str_clean)
        now = datetime.now(timezone.utc)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)
        return end_date <= now
    except Exception:
        return True  # If parsing fails but end string exists, treat as ended


def fetch_aniskip_episode(mal_id: int, episode: int) -> dict | None:
    url = ANISKIP_URL.format(mal_id=mal_id, ep=episode)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req) as res:
            data = json.loads(res.read().decode("utf-8"))
            if data.get("found"):
                return data
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        print(f"HTTP error {e.code} for MAL {mal_id} ep {episode}")
    except Exception as e:
        print(f"Error fetching MAL {mal_id} ep {episode}: {e}")
    return None


def parse_skip_times(aniskip_data: dict) -> tuple[int, int, int, int]:
    op_start, op_end = -1, -1
    ed_start, ed_end = -1, -1
    if not aniskip_data or not aniskip_data.get("results"):
        return op_start, op_end, ed_start, ed_end

    for result in aniskip_data["results"]:
        skip_type = result.get("skipType")
        interval = result.get("interval", {})
        st = round(interval.get("startTime", -1))
        et = round(interval.get("endTime", -1))

        if skip_type == "op":
            op_start, op_end = st, et
        elif skip_type == "ed":
            ed_start, ed_end = st, et

    return op_start, op_end, ed_start, ed_end


def format_episode_line(ep: int, op_start: int, op_end: int, ed_start: int, ed_end: int) -> str:
    return f"{ep};{op_start};{op_end};{ed_start};{ed_end}"


def load_existing_episodes(filepath: str) -> dict[int, str]:
    episodes = {}
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(";")
                if parts and parts[0].isdigit():
                    ep = int(parts[0])
                    episodes[ep] = line
    return episodes


def write_subject_data(subject_id: str, title: str, episodes_dict: dict[int, str]):
    folder = subject_id
    os.makedirs(folder, exist_ok=True)
    data_file = os.path.join(folder, f"{subject_id}.txt")

    # Sort lines by episode number
    sorted_lines = [episodes_dict[ep] for ep in sorted(episodes_dict.keys())]
    with open(data_file, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted_lines) + "\n")

    # Ensure empty anime title file exists
    clean_title = sanitize_filename(title)
    if clean_title:
        title_file = os.path.join(folder, clean_title)
        if not os.path.exists(title_file):
            open(title_file, "a").close()


def process_anime(subject_id: str, mal_id: int, title: str, start_ep: int = 1) -> tuple[dict[int, str], int]:
    folder = subject_id
    data_file = os.path.join(folder, f"{subject_id}.txt")
    episodes = load_existing_episodes(data_file)

    current_ep = start_ep
    max_ep_found = max(episodes.keys(), default=0)

    while True:
        aniskip_data = fetch_aniskip_episode(mal_id, current_ep)
        time.sleep(0.1)  # Mild rate limiting

        if not aniskip_data:
            # If 404 or no data, stop probing
            break

        op_s, op_e, ed_s, ed_e = parse_skip_times(aniskip_data)
        if (op_s, op_e, ed_s, ed_e) != (-1, -1, -1, -1):
            episodes[current_ep] = format_episode_line(current_ep, op_s, op_e, ed_s, ed_e)
            max_ep_found = max(max_ep_found, current_ep)
        elif current_ep in episodes:
            # Keep existing entry if API returned empty result but local file had it
            pass

        current_ep += 1

    if episodes:
        write_subject_data(subject_id, title, episodes)

    return episodes, max_ep_found


def main():
    state = load_state()
    bangumi_data, new_hash = fetch_bangumi_data()
    state["bangumi_data_hash"] = new_hash

    items = bangumi_data.get("items", [])
    print(f"Total items in bangumi-data: {len(items)}")

    ongoing_state = state.get("ongoing", {})

    for item in items:
        sites = item.get("sites", [])
        bgm_id = None
        mal_id = None

        for site in sites:
            s_name = site.get("site")
            if s_name == "bangumi":
                bgm_id = site.get("id")
            elif s_name == "mal":
                try:
                    mal_id = int(site.get("id"))
                except (ValueError, TypeError):
                    pass

        if not bgm_id or not mal_id:
            continue

        title_trans = item.get("titleTranslate", {})
        zh_titles = title_trans.get("zh-Hans", [])
        title = zh_titles[0] if zh_titles else item.get("title", "")

        ended = is_anime_ended(item.get("end", ""))
        data_file = os.path.join(str(bgm_id), f"{bgm_id}.txt")
        file_exists = os.path.exists(data_file)

        if ended:
            if file_exists:
                # If finished and data file exists, skip entirely
                if str(bgm_id) in ongoing_state:
                    del ongoing_state[str(bgm_id)]
                continue
            else:
                print(f"[Completed] Syncing Subject {bgm_id} (MAL {mal_id}): {title}")
                episodes, _ = process_anime(str(bgm_id), mal_id, title, start_ep=1)
                if str(bgm_id) in ongoing_state:
                    del ongoing_state[str(bgm_id)]
        else:
            # Ongoing anime
            last_ep = ongoing_state.get(str(bgm_id), {}).get("last_ep", 0)
            start_ep = last_ep + 1 if last_ep > 0 else 1
            print(f"[Ongoing] Checking Subject {bgm_id} (MAL {mal_id}): {title} starting at ep {start_ep}")

            episodes, max_ep = process_anime(str(bgm_id), mal_id, title, start_ep=start_ep)
            if episodes:
                ongoing_state[str(bgm_id)] = {
                    "mal_id": mal_id,
                    "title": title,
                    "last_ep": max_ep,
                }

    state["ongoing"] = ongoing_state
    save_state(state)
    print("Sync complete.")


if __name__ == "__main__":
    main()
