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
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.05"))
MAX_RETRIES = 5
TIMEOUT_SECONDS = 15
# Set MAX_PROCESS_ITEMS to batch initial full sync and avoid Action execution timeout (0 means no limit)
MAX_PROCESS_ITEMS = int(os.environ.get("MAX_PROCESS_ITEMS", "300"))
# Minimum hours between checking the same ongoing anime during catch-up sweeps
ONGOING_CHECK_INTERVAL_HOURS = int(os.environ.get("ONGOING_CHECK_INTERVAL_HOURS", "24"))

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
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
                content = res.read()
                data_hash = hashlib.sha256(content).hexdigest()
                data = json.loads(content.decode("utf-8"))
                return data, data_hash
        except Exception as e:
            wait_time = (2 ** attempt) + (attempt * 0.5)
            print(f"Error fetching bangumi-data (attempt {attempt}/{MAX_RETRIES}): {e}. Retrying in {wait_time:.1f}s...")
            time.sleep(wait_time)

    print(f"Fatal: Failed to fetch bangumi-data after {MAX_RETRIES} retries. Aborting action.")
    sys.exit(1)


def is_anime_ended(end_str: str) -> bool:
    """
    Returns True ONLY if the anime ended more than 90 days ago.
    Otherwise (ongoing, ended recently, or unknown/unparseable date), returns False.
    """
    if not end_str:
        return False
    try:
        end_clean = end_str.replace("Z", "+00:00").strip()
        if len(end_clean) == 4 and end_clean.isdigit():
            end_clean += "-01-01"
        elif len(end_clean) == 7 and re.match(r"^\d{4}-\d{2}$", end_clean):
            end_clean += "-01"

        end_date = datetime.fromisoformat(end_clean)
        if end_date.tzinfo is None:
            end_date = end_date.replace(tzinfo=timezone.utc)

        seal_date = datetime.now(timezone.utc) - timedelta(days=90)
        return end_date <= seal_date
    except Exception as e:
        print(f"Warning: Failed to parse end date '{end_str}': {e}. Treating as ongoing/cooldown.")
        return False


def is_ongoing_recently_checked(ongoing_info: dict, max_hours: int) -> bool:
    if not ongoing_info or "last_check" not in ongoing_info:
        return False
    try:
        last_check_str = ongoing_info["last_check"]
        last_check_dt = datetime.fromisoformat(last_check_str)
        if last_check_dt.tzinfo is None:
            last_check_dt = last_check_dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last_check_dt) < timedelta(hours=max_hours)
    except Exception:
        return False


def fetch_bangumi_total_episodes(bgm_id: str) -> int:
    """Fetches total number of normal episodes using Bangumi API."""
    url = BANGUMI_EPISODES_API.format(bgm_id=bgm_id)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
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
        except (urllib.error.URLError, TimeoutError, Exception) as e:
            wait_time = (2 ** attempt) + (attempt * 0.5)
            print(f"Error fetching Bangumi episodes for Subject {bgm_id} (attempt {attempt}/{MAX_RETRIES}): {e}. Retrying in {wait_time:.1f}s...")
            time.sleep(wait_time)

    print(f"Fatal: Failed to fetch Bangumi total episodes for Subject {bgm_id} after {MAX_RETRIES} retries. Aborting action.")
    sys.exit(1)


def fetch_aniskip_episode(mal_id: int, episode: int) -> dict | None:
    url = ANISKIP_URL.format(mal_id=mal_id, ep=episode)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as res:
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
        except (urllib.error.URLError, TimeoutError, Exception) as e:
            wait_time = (2 ** attempt) + (attempt * 0.5)
            print(f"Error fetching MAL {mal_id} ep {episode} (attempt {attempt}/{MAX_RETRIES}): {e}. Retrying in {wait_time:.1f}s...")
            time.sleep(wait_time)

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


def read_existing_episodes(subject_id: str) -> dict[int, str]:
    data_file = os.path.join(subject_id, f"{subject_id}.txt")
    episodes = {}
    if os.path.exists(data_file):
        try:
            with open(data_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(";")
                    if len(parts) == 5:
                        try:
                            ep = int(parts[0])
                            episodes[ep] = line
                        except ValueError:
                            pass
        except Exception as e:
            print(f"Warning: Error reading existing file {data_file}: {e}")
    return episodes


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
    Sweeps from episode 1 to total_eps using hybrid caching.
    Reuses existing valid lines for older episodes (except last 3 episodes which get re-checked).
    Fetches AniSkip data for missing episodes or recent episodes.
    """
    existing_episodes = read_existing_episodes(subject_id)
    episodes = {}

    if total_eps == 0:
        write_subject_data(subject_id, title, episodes)
        return True

    # Hybrid re-check window: last 3 episodes of total_eps
    recheck_start_ep = max(1, total_eps - 2)

    for current_ep in range(1, total_eps + 1):
        if current_ep < recheck_start_ep and current_ep in existing_episodes:
            episodes[current_ep] = existing_episodes[current_ep]
            continue

        aniskip_data = fetch_aniskip_episode(mal_id, current_ep)
        time.sleep(REQUEST_DELAY)

        if aniskip_data:
            op_s, op_e, ed_s, ed_e = parse_skip_times(aniskip_data)
            if (op_s, op_e, ed_s, ed_e) != (-1, -1, -1, -1):
                episodes[current_ep] = format_episode_line(current_ep, op_s, op_e, ed_s, ed_e)
            elif current_ep in existing_episodes:
                episodes[current_ep] = existing_episodes[current_ep]
        elif current_ep in existing_episodes:
            episodes[current_ep] = existing_episodes[current_ep]

    write_subject_data(subject_id, title, episodes)
    return True


def main():
    state = load_state()
    bangumi_data, new_hash = fetch_bangumi_data()
    state["bangumi_data_hash"] = new_hash

    items = bangumi_data.get("items", [])
    print(f"Total items in bangumi-data: {len(items)}")

    ongoing_state = state.get("ongoing", {})
    processed_count = 0

    for item in items:
        if MAX_PROCESS_ITEMS > 0 and processed_count >= MAX_PROCESS_ITEMS:
            print(f"Batch limit reached ({processed_count}/{MAX_PROCESS_ITEMS} processed). Exiting cleanly to save progress.")
            break

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
                if bgm_id_str in ongoing_state:
                    del ongoing_state[bgm_id_str]
                    state["ongoing"] = ongoing_state
                    save_state(state)
                continue
            else:
                total_eps = fetch_bangumi_total_episodes(bgm_id_str)
                time.sleep(REQUEST_DELAY)
                print(f"[Completed Setup] Subject {bgm_id_str} (MAL {mal_id}): {title} (Total: {total_eps})")

                process_anime_sweep(bgm_id_str, mal_id, title, total_eps)
                processed_count += 1

                if bgm_id_str in ongoing_state:
                    del ongoing_state[bgm_id_str]
                    state["ongoing"] = ongoing_state
                    save_state(state)
        else:
            existing_ongoing_info = ongoing_state.get(bgm_id_str, {})
            if file_exists and is_ongoing_recently_checked(existing_ongoing_info, max_hours=ONGOING_CHECK_INTERVAL_HOURS):
                continue

            total_eps = fetch_bangumi_total_episodes(bgm_id_str)
            time.sleep(REQUEST_DELAY)

            end_date_str = item.get("end", "Unknown")
            print(f"[Ongoing/Cooldown] Subject {bgm_id_str} (MAL {mal_id}): {title} (Total: {total_eps}, End: {end_date_str})")

            process_anime_sweep(bgm_id_str, mal_id, title, total_eps)
            processed_count += 1

            ongoing_state[bgm_id_str] = {
                "mal_id": mal_id,
                "title": title,
                "last_check": datetime.now(timezone.utc).isoformat()
            }
            state["ongoing"] = ongoing_state
            save_state(state)

    state["ongoing"] = ongoing_state
    save_state(state)
    print(f"Sync batch complete. Processed {processed_count} subjects in this run.")


if __name__ == "__main__":
    main()
