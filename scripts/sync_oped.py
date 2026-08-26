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
DATA_DIR = os.environ.get("DATA_DIR", ".")
STATE_FILE = os.path.join(DATA_DIR, ".state.json")
USER_AGENT = "Mozilla/5.0 (compatible; bangumi-oped-sync/1.0)"
REQUEST_DELAY = float(os.environ.get("REQUEST_DELAY", "0.05"))
MAX_RETRIES = 5
TIMEOUT_SECONDS = 15

# Set MAX_PROCESS_REQUESTS to batch initial full sync by API requests (default: 1000)
MAX_PROCESS_REQUESTS = int(os.environ.get("MAX_PROCESS_REQUESTS", "1000"))
# Set MAX_RUN_TIME_SECONDS to exit safely before GitHub Actions interval (default: 840 seconds / 14 minutes)
MAX_RUN_TIME_SECONDS = int(os.environ.get("MAX_RUN_TIME_SECONDS", "840"))
# Minimum hours between checking the same ongoing anime during catch-up sweeps
ONGOING_CHECK_INTERVAL_HOURS = int(os.environ.get("ONGOING_CHECK_INTERVAL_HOURS", "24"))
# Number of recent episodes to re-check during ongoing sweeps (default: 6 episodes)
RECHECK_WINDOW = int(os.environ.get("RECHECK_WINDOW", "6"))
# Maximum age of anime in days based on start date (default: 365 days / 1 year, set 0 to disable)
MAX_ANIME_AGE_DAYS = int(os.environ.get("MAX_ANIME_AGE_DAYS", "365"))

INITIAL_SWEEP_ENV = os.environ.get("INITIAL_SWEEP", "1") == "1"

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

global_api_requests = 0
start_time = time.time()


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
    return {"bangumi_data_hash": "", "initial_sweep_done": False, "ongoing": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")


def fetch_bangumi_data() -> tuple[dict, str]:
    global global_api_requests
    print("Fetching bangumi-data...")
    req = urllib.request.Request(BANGUMI_DATA_URL, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, MAX_RETRIES + 1):
        global_api_requests += 1
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


def parse_iso_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        clean = date_str.replace("Z", "+00:00").strip()
        if len(clean) == 4 and clean.isdigit():
            clean += "-01-01"
        elif len(clean) == 7 and re.match(r"^\d{4}-\d{2}$", clean):
            clean += "-01"

        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_anime_ended(end_str: str) -> bool:
    """
    Returns True ONLY if the anime ended more than 90 days ago.
    Otherwise (ongoing, ended recently, or unknown/unparseable date), returns False.
    """
    end_date = parse_iso_date(end_str)
    if not end_date:
        return False
    seal_date = datetime.now(timezone.utc) - timedelta(days=90)
    return end_date <= seal_date


def is_anime_too_old(begin_str: str, max_age_days: int) -> bool:
    """
    Returns True if the anime started more than max_age_days ago.
    If max_age_days <= 0 or begin_str is missing/unparseable, returns False.
    """
    if max_age_days <= 0:
        return False
    begin_date = parse_iso_date(begin_str)
    if not begin_date:
        return False
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    return begin_date < cutoff_date


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


def fetch_bangumi_total_episodes(bgm_id: str) -> int | None:
    """Fetches total number of normal episodes using Bangumi API."""
    global global_api_requests
    url = BANGUMI_EPISODES_API.format(bgm_id=bgm_id)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_RETRIES + 1):
        global_api_requests += 1
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

    print(f"Warning: Failed to fetch Bangumi total episodes for Subject {bgm_id} after {MAX_RETRIES} retries. Skipping subject.")
    return None


def fetch_aniskip_episode(mal_id: int, episode: int) -> dict | None:
    global global_api_requests
    url = ANISKIP_URL.format(mal_id=mal_id, ep=episode)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    for attempt in range(1, MAX_RETRIES + 1):
        global_api_requests += 1
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

    print(f"Warning: AniSkip API permanently failed for MAL {mal_id} ep {episode} after {MAX_RETRIES} retries. Skipping episode.")
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


def parse_episode_line(line: str) -> tuple[int, int, int, int]:
    """Parses line 'ep;op_s;op_e;ed_s;ed_e' into (op_s, op_e, ed_s, ed_e)."""
    parts = line.split(";")
    if len(parts) == 5:
        try:
            return int(parts[1]), int(parts[2]), int(parts[3]), int(parts[4])
        except ValueError:
            pass
    return -1, -1, -1, -1


def read_existing_episodes(subject_id: str) -> dict[int, str]:
    folder = os.path.join(DATA_DIR, subject_id)
    data_file = os.path.join(folder, f"{subject_id}.txt")
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
    folder = os.path.join(DATA_DIR, subject_id)
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


def process_anime_sweep(subject_id: str, mal_id: int, title: str, total_eps: int, is_ongoing_recheck: bool = False) -> bool:
    """
    Sweeps from episode 1 to total_eps using hybrid 3-tier caching with field-level merging.
    - Tier 1: Complete episodes (valid OP & ED) outside RECHECK_WINDOW -> Cached permanently.
    - Tier 2: Recent episodes (last RECHECK_WINDOW eps) -> Always checked via AniSkip.
    - Tier 3: Incomplete episodes (-1 present) outside window -> Checked during ongoing rechecks.
    Field-level merge ensures existing valid OP or ED is never lost if AniSkip only has one segment.
    """
    existing_episodes = read_existing_episodes(subject_id)
    episodes = {}

    if total_eps == 0:
        write_subject_data(subject_id, title, episodes)
        return True

    # Recent re-check window: last RECHECK_WINDOW episodes of total_eps
    recheck_start_ep = max(1, total_eps - RECHECK_WINDOW + 1)

    for current_ep in range(1, total_eps + 1):
        old_op_s, old_op_e, old_ed_s, old_ed_e = -1, -1, -1, -1
        has_existing = current_ep in existing_episodes
        if has_existing:
            old_op_s, old_op_e, old_ed_s, old_ed_e = parse_episode_line(existing_episodes[current_ep])

        is_outside_window = current_ep < recheck_start_ep
        is_complete = (old_op_s != -1 and old_ed_s != -1)

        # Tier 1: Complete episode outside window -> Reuse immediately
        if is_outside_window and has_existing and is_complete:
            episodes[current_ep] = existing_episodes[current_ep]
            continue

        # Tier 3: Incomplete episode outside window on non-recheck runs -> Reuse existing
        if is_outside_window and has_existing and not is_ongoing_recheck:
            episodes[current_ep] = existing_episodes[current_ep]
            continue

        # Tier 2 (Recent window) OR Tier 3 (Ongoing recheck) OR New episode -> Fetch AniSkip
        aniskip_data = fetch_aniskip_episode(mal_id, current_ep)
        time.sleep(REQUEST_DELAY)

        if aniskip_data:
            new_op_s, new_op_e, new_ed_s, new_ed_e = parse_skip_times(aniskip_data)

            # Field-level merge: Prefer new valid timestamp, fallback to old timestamp
            final_op_s = new_op_s if new_op_s != -1 else old_op_s
            final_op_e = new_op_e if new_op_e != -1 else old_op_e
            final_ed_s = new_ed_s if new_ed_s != -1 else old_ed_s
            final_ed_e = new_ed_e if new_ed_e != -1 else old_ed_e

            if (final_op_s, final_op_e, final_ed_s, final_ed_e) != (-1, -1, -1, -1):
                episodes[current_ep] = format_episode_line(current_ep, final_op_s, final_op_e, final_ed_s, final_ed_e)
            elif has_existing:
                episodes[current_ep] = existing_episodes[current_ep]
        elif has_existing:
            episodes[current_ep] = existing_episodes[current_ep]

    write_subject_data(subject_id, title, episodes)
    return True


def main():
    state = load_state()
    bangumi_data, new_hash = fetch_bangumi_data()
    state["bangumi_data_hash"] = new_hash

    items = bangumi_data.get("items", [])
    print(f"Total items in bangumi-data: {len(items)}")

    # Filter items by MAX_ANIME_AGE_DAYS (default: 730 days / 2 years)
    if MAX_ANIME_AGE_DAYS > 0:
        filtered_items = [
            it for it in items
            if not is_anime_too_old(it.get("begin", ""), MAX_ANIME_AGE_DAYS)
        ]
        print(f"Filtered to {len(filtered_items)} items within the last {MAX_ANIME_AGE_DAYS} days ({MAX_ANIME_AGE_DAYS / 365:.1f} years).")
        items = filtered_items

    ongoing_state = state.get("ongoing", {})

    # Purge ongoing entries that are no longer in filtered item list
    valid_bgm_ids = set()
    for it in items:
        for s in it.get("sites", []):
            if s.get("site") == "bangumi":
                valid_bgm_ids.add(str(s.get("id")))

    purged_keys = [k for k in ongoing_state if k not in valid_bgm_ids]
    if purged_keys:
        print(f"Purging {len(purged_keys)} old/filtered ongoing entries from state tracking.")
        for k in purged_keys:
            del ongoing_state[k]
        state["ongoing"] = ongoing_state
        save_state(state)

    processed_count = 0

    initial_sweep_mode = INITIAL_SWEEP_ENV and not state.get("initial_sweep_done", False)
    if initial_sweep_mode:
        print("=== Initial Fast Sweep Mode Active: Existing subject folders will be skipped ===")
        items_to_process = list(reversed(items))
    else:
        # LRU Dynamic Queue: Sort subjects by last_check ASCENDING (oldest checked first)
        print("=== Maintenance Mode Active: Sorting ongoing subjects by LRU (oldest check first) ===")
        def get_last_check_time(item):
            sites = item.get("sites", [])
            for site in sites:
                if site.get("site") == "bangumi":
                    bgm_id_str = str(site.get("id"))
                    return ongoing_state.get(bgm_id_str, {}).get("last_check", "1970-01-01T00:00:00")
            return "1970-01-01T00:00:00"

        items_to_process = sorted(items, key=get_last_check_time)

    completed_full_loop = True

    for item in items_to_process:
        elapsed_seconds = time.time() - start_time
        if (MAX_PROCESS_REQUESTS > 0 and global_api_requests >= MAX_PROCESS_REQUESTS) or \
           (MAX_RUN_TIME_SECONDS > 0 and elapsed_seconds >= MAX_RUN_TIME_SECONDS):
            print(f"Limit reached ({global_api_requests}/{MAX_PROCESS_REQUESTS} requests, {elapsed_seconds:.1f}s/{MAX_RUN_TIME_SECONDS}s elapsed). Exiting cleanly to save progress.")
            completed_full_loop = False
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
        data_file = os.path.join(DATA_DIR, bgm_id_str, f"{bgm_id_str}.txt")
        file_exists = os.path.exists(data_file)

        if initial_sweep_mode and file_exists:
            continue

        title_trans = item.get("titleTranslate", {})
        zh_titles = title_trans.get("zh-Hans", [])
        title = zh_titles[0] if zh_titles else item.get("title", "")

        is_strictly_ended = is_anime_ended(item.get("end", ""))

        if is_strictly_ended:
            if file_exists:
                if bgm_id_str in ongoing_state:
                    del ongoing_state[bgm_id_str]
                    state["ongoing"] = ongoing_state
                    save_state(state)
                continue
            else:
                total_eps = fetch_bangumi_total_episodes(bgm_id_str)
                if total_eps is None:
                    continue
                time.sleep(REQUEST_DELAY)
                print(f"[Completed Setup] Subject {bgm_id_str} (MAL {mal_id}): {title} (Total: {total_eps})")

                process_anime_sweep(bgm_id_str, mal_id, title, total_eps, is_ongoing_recheck=False)
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
            if total_eps is None:
                continue
            time.sleep(REQUEST_DELAY)

            end_date_str = item.get("end", "Unknown")
            print(f"[Ongoing/Cooldown] Subject {bgm_id_str} (MAL {mal_id}): {title} (Total: {total_eps}, End: {end_date_str})")

            process_anime_sweep(bgm_id_str, mal_id, title, total_eps, is_ongoing_recheck=True)
            processed_count += 1

            ongoing_state[bgm_id_str] = {
                "mal_id": mal_id,
                "title": title,
                "last_check": datetime.now(timezone.utc).isoformat()
            }
            state["ongoing"] = ongoing_state
            save_state(state)

    state["ongoing"] = ongoing_state

    if initial_sweep_mode and completed_full_loop:
        state["initial_sweep_done"] = True
        print("🎉 Initial fast sweep complete! All subjects have local data files. Switching to standard maintenance mode.")

    save_state(state)
    total_elapsed = time.time() - start_time
    print(f"Sync batch complete. Processed {processed_count} subjects, made {global_api_requests} API requests in {total_elapsed:.1f}s.")


if __name__ == "__main__":
    main()
