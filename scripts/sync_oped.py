#!/usr/bin/env python3
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import hashlib
from datetime import datetime, timezone, timedelta

BANGUMI_DATA_URL = "https://unpkg.com/bangumi-data@0.3/dist/data.json"
BANGUMI_EPISODES_API = "https://api.bgm.tv/v0/episodes?subject_id={bgm_id}&type=0"
ANISKIP_URL = "https://api.aniskip.com/v2/skip-times/{mal_id}/{ep}?types=op&types=ed&episodeLength=0"
STATE_FILE = ".state.json"
USER_AGENT = "Mozilla/5.0 (compatible; bangumi-oped-sync/1.0)"
# Default to 0.05 for fast initial sync; can be overridden in env
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.05"))
MAX_RETRIES = 5

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
    """
    Returns True ONLY if the anime ended more than 90 days ago.
    Otherwise (ongoing, or ended recently), returns False (treat as ongoing/cooling down).
    """
    if not end_str:
        return False
    try:
        end_str_clean = end_str.replace("Z", "+00:00")
        end_date = datetime.fromisoformat(end_str_clean)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        # Calculate the sealing threshold: 90 days ago
        seal_date = datetime.now(timezone.utc) - timedelta(days=90)
        return end_date <= seal_date
    except Exception:
        return True


def fetch_bangumi_total_episodes(bgm_id: str) -> int:
    """Fetches total number of normal episodes using Bangumi API."""
    url = BANGUMI_EPISODES_API.format(bgm_id=bgm_id)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req) as res:
                data = json.loads(res.read().decode("utf-8"))
                return int(data.get("total", 0))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return 0
            elif e.code == 429 or e.code >= 500:
                wait_time = (2 ** attempt) + (attempt * 0.5)
                print(f"Bangumi API HTTP {e.code} for Subject {bgm_id}. Retrying in {wait_time:.1f}s...")
                time.sleep(wait_time)
            else:
                print(f"Bangumi API HTTP error {e.code} for Subject {bgm_id}")
                return 0
        except Exception as e:
            print(f"Error fetching Bangumi episodes for Subject {bgm_id}: {e}")
            return 0

    print(f"Fatal: Failed to fetch Bangumi total episodes for Subject {bgm_id} after {MAX_RETRIES} retries. Aborting action.")
    sys.exit(1)


def fetch_aniskip_episode(mal_id: int, episode: int) -> dict | None:
    url = ANISKIP_URL.format(mal_id=mal_id, ep=episode)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req) as res:
                data = json.loads(res.read().decode("utf-8"))
                if data.get("found"):
                    return data
                return None
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            elif e.code == 429 or e.code >= 500:
                wait_time = (2 ** attempt) + (attempt * 0.5)
                print(f"HTTP {e.code} for MAL {mal_id} ep {episode}. Retrying in {wait_time:.1f}s (attempt {attempt}/{MAX_RETRIES})...")
                time.sleep(wait_time)
            else:
                print(f"HTTP error {e.code} for MAL {mal_id} ep {episode}")
                return None
        except Exception as e:
            print(f"Error fetching MAL {mal_id} ep {episode}: {e}")
            return None

    print(f"Fatal: AniSkip API permanently failed for MAL {mal_id} ep {episode} after {MAX_RETRIES} retries. Aborting action to preserve state.")
    sys.exit(1)


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


def write_subject_data(subject_id: str, title: str, episodes_dict: dict[int, str]):
    folder = subject_id
    os.makedirs(folder, exist_ok=True)
    data_file = os.path.join(folder, f"{subject_id}.txt")

    sorted_lines = [episodes_dict[ep] for ep in sorted(episodes_dict.keys())]
    with open(data_file, "w", encoding="utf-8") as f:
        if sorted_lines:
            f.write("\n".join(sorted_lines) + "\n")
        else:
            f.write("")

    clean_title = sanitize_filename(title)
    if clean_title:
        title_file = os.path.join(folder, clean_title)
        if not os.path.exists(title_file):
            open(title_file, "a").close()


def process_anime_sweep(subject_id: str, mal_id: int, title: str, total_eps: int) -> bool:
    """
    Sweeps from episode 1 to total_eps. Used for ongoing anime and first-time setups.
    Returns True if we actually fetched any data (or successfully confirmed 0 episodes).
    """
    episodes = {}

    # If there are no episodes (e.g. unreleased), we still want to create the empty file
    if total_eps == 0:
        write_subject_data(subject_id, title, episodes)
        return True

    for current_ep in range(1, total_eps + 1):
        aniskip_data = fetch_aniskip_episode(mal_id, current_ep)
        time.sleep(REQUEST_DELAY)

        if aniskip_data:
            op_s, op_e, ed_s, ed_e = parse_skip_times(aniskip_data)
            if (op_s, op_e, ed_s, ed_e) != (-1, -1, -1, -1):
                episodes[current_ep] = format_episode_line(current_ep, op_s, op_e, ed_s, ed_e)

    write_subject_data(subject_id, title, episodes)
    return True


def main():
    state = load_state()
    bangumi_data, new_hash = fetch_bangumi_data()
    state["bangumi_data_hash"] = new_hash

    items = bangumi_data.get("items", [])
    print(f"Total items in bangumi-data: {len(items)}")

    # We still track ongoing state so that if it transitions to completed,
    # we know to clean it up.
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

        bgm_id_str = str(bgm_id)

        title_trans = item.get("titleTranslate", {})
        zh_titles = title_trans.get("zh-Hans", [])
        title = zh_titles[0] if zh_titles else item.get("title", "")

        is_strictly_ended = is_anime_ended(item.get("end", ""))
        data_file = os.path.join(bgm_id_str, f"{bgm_id_str}.txt")
        file_exists = os.path.exists(data_file)

        if is_strictly_ended:
            if file_exists:
                # If strictly finished (passed 90 days) and data file exists, skip entirely
                if bgm_id_str in ongoing_state:
                    del ongoing_state[bgm_id_str]
                    state["ongoing"] = ongoing_state
                    save_state(state)
                continue
            else:
                # First time seeing this completed anime (or folder was manually deleted)
                total_eps = fetch_bangumi_total_episodes(bgm_id_str)
                time.sleep(REQUEST_DELAY) # Rate limit for bgm api too
                print(f"[Completed Setup] Subject {bgm_id_str} (MAL {mal_id}): {title} (Total: {total_eps})")

                process_anime_sweep(bgm_id_str, mal_id, title, total_eps)

                if bgm_id_str in ongoing_state:
                    del ongoing_state[bgm_id_str]
                    state["ongoing"] = ongoing_state
                    save_state(state)
        else:
            # Ongoing anime OR in 90-day cooldown
            total_eps = fetch_bangumi_total_episodes(bgm_id_str)
            time.sleep(REQUEST_DELAY)

            end_date_str = item.get("end", "Unknown")
            print(f"[Ongoing/Cooldown] Subject {bgm_id_str} (MAL {mal_id}): {title} (Total: {total_eps}, End: {end_date_str})")

            process_anime_sweep(bgm_id_str, mal_id, title, total_eps)

            ongoing_state[bgm_id_str] = {
                "mal_id": mal_id,
                "title": title
            }
            state["ongoing"] = ongoing_state
            save_state(state)

    state["ongoing"] = ongoing_state
    save_state(state)
    print("Sync complete.")


if __name__ == "__main__":
    main()
