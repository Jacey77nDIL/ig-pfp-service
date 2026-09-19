import os
import sys
import time
import json
import re
import argparse
import uuid
import requests
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
IG_SESSION_ID = os.environ.get("IG_SESSION_ID")

if SUPABASE_URL and SUPABASE_URL.endswith("/rest/v1/"):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL and SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[:-8]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

IG_HEADERS = {
    "User-Agent": "Instagram 278.0.0.19.115 (iPhone14,2; iOS 16_5; en_US; scale=3.00; 1170x2532)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": f"sessionid={IG_SESSION_ID}" if IG_SESSION_ID else ""
}

def get_existing_handles():
    """
    Collects all handles already in the Creator table or CreatorSuggestion table.
    """
    existing = set()
    
    # Existing creators
    try:
        res_creators = supabase.table("CreatorPlatform").select("handle").execute()
        for r in (res_creators.data or []):
            h = r.get("handle")
            if h:
                existing.add(h.lower().replace("@", "").strip())
    except Exception as e:
        print(f"Error fetching existing creator handles: {e}")

    # Existing suggestions
    try:
        res_sugg = supabase.table("CreatorSuggestion").select("username").execute()
        for r in (res_sugg.data or []):
            u = r.get("username")
            if u:
                existing.add(u.lower().replace("@", "").strip())
    except Exception as e:
        print(f"Error fetching existing suggestions: {e}")

    return existing

def get_instagram_user_pk(handle):
    """
    Extracts the numeric user ID (pk) for an Instagram username from public HTML.
    """
    clean_handle = handle.replace("@", "").strip()
    try:
        resp = requests.get(
            f"https://www.instagram.com/{clean_handle}/",
            headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X)"},
            timeout=15
        )
        if resp.status_code == 200:
            m = re.search(r'"id":"(\d+)"', resp.text)
            if m:
                return m.group(1)
    except Exception as e:
        print(f"Error getting pk for @{clean_handle}: {e}")
    return None

def fetch_suggested_creators(target_pk):
    """
    Queries Instagram's official discover/chaining API for a given creator ID.
    Returns list of suggested user dictionaries.
    """
    if not IG_SESSION_ID:
        print("ERROR: IG_SESSION_ID is required in .env for discover/chaining crawler.")
        return []

    url = f"https://i.instagram.com/api/v1/discover/chaining/?target_id={target_pk}"
    try:
        resp = requests.get(url, headers=IG_HEADERS, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("users", [])
        else:
            print(f"Chaining request failed with HTTP {resp.status_code}")
    except Exception as e:
        print(f"Chaining API exception: {e}")
    return []

def run_crawler(seed_handles=None, max_new_suggestions=30):
    """
    Crawls suggested users from seed creators and inserts new ones into CreatorSuggestion.
    """
    print(f"[{time.strftime('%X')}] Starting Caskayd Discovery Crawler Bot...")
    
    existing_handles = get_existing_handles()
    print(f"[{time.strftime('%X')}] Loaded {len(existing_handles)} already known/suggested handles.")

    seeds = seed_handles or ["hildabaci", "brodashagi", "taaooma", "fisayofosudo"]
    new_discovered_count = 0

    for seed in seeds:
        if new_discovered_count >= max_new_suggestions:
            break
            
        print(f"\n[{time.strftime('%X')}] Exploring seed creator: @{seed}...")
        pk = get_instagram_user_pk(seed)
        if not pk:
            print(f"Could not resolve pk for @{seed}, skipping.")
            continue

        suggested_users = fetch_suggested_creators(pk)
        print(f"[{time.strftime('%X')}] Discovered {len(suggested_users)} chained accounts from @{seed}.")

        for u in suggested_users:
            if new_discovered_count >= max_new_suggestions:
                break

            username = (u.get("username") or "").lower().strip()
            if not username or username in existing_handles:
                continue

            # Skip private accounts
            if u.get("is_private", False):
                continue

            name = u.get("full_name") or username
            
            # Prepare suggestion record
            suggestion = {
                "id": str(uuid.uuid4()),
                "name": name,
                "username": username,
                "platform": "INSTAGRAM",
                "link": f"https://www.instagram.com/{username}/",
                "status": "PENDING"
            }

            try:
                supabase.table("CreatorSuggestion").insert(suggestion).execute()
                existing_handles.add(username)
                new_discovered_count += 1
                print(f"  [+] Ingested new suggestion: @{username} ({name})")
            except Exception as insert_err:
                print(f"  [!] Failed to insert @{username}: {insert_err}")

        time.sleep(2) # Polite pacing between seeds

    print(f"\n[{time.strftime('%X')}] Crawler complete! Ingested {new_discovered_count} new creator suggestions.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Caskayd Creator Discovery Crawler Bot.")
    parser.add_argument("--seeds", nargs="+", help="Seed Instagram handles to start from")
    parser.add_argument("--max", type=int, default=20, help="Maximum new suggestions to discover")
    args = parser.parse_args()

    run_crawler(seed_handles=args.seeds, max_new_suggestions=args.max)
