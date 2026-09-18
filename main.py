import os
import time
import threading
import queue
import tempfile
import asyncio
import re
from dotenv import load_dotenv
from supabase import create_client, create_async_client, Client
import requests
import json
import random

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials in .env")

if SUPABASE_URL.endswith('/rest/v1/'):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL.endswith('/rest/v1'):
    SUPABASE_URL = SUPABASE_URL[:-8]

# Sync client for database queries & storage uploads
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
task_queue = queue.Queue()
queued_creator_ids = set()

def log_failure(creator_id: str, handle: str, reason: str):
    try:
        supabase.table("Creator").update({"pfpError": reason}).eq("id", creator_id).execute()
        print(f"Logged failure in Supabase DB (pfpError) for Creator {creator_id} (@{handle}): {reason}")
    except Exception as e:
        print(f"Error logging failure to Supabase DB for Creator {creator_id}: {e}")

def extract_handle(raw_handle: str) -> str:
    if not raw_handle:
        return ""
    raw_handle = raw_handle.strip()
    if "instagram.com/" in raw_handle:
        parts = raw_handle.split("instagram.com/")[1]
        username = parts.split("/")[0].split("?")[0]
        return username
    return raw_handle.lstrip("@").strip()

def get_ig_pfp_mobile_html(handle: str):
    url = f"https://www.instagram.com/{handle}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document"
    }
    cookies = {}
    ig_session = os.environ.get("IG_SESSION_ID")
    if ig_session:
        cookies["sessionid"] = ig_session.strip()

    try:
        resp = requests.get(url, headers=headers, cookies=cookies, timeout=15)
        
        # Detect Instagram Login Wall / IP Rate Limit Challenge
        title_match = re.search(r'<title>(.*?)</title>', resp.text, re.IGNORECASE)
        page_title = title_match.group(1).strip() if title_match else ""
        is_login_wall = (
            resp.status_code == 429 or
            page_title.lower() == "instagram" or
            "/accounts/login" in resp.url or
            "/login/" in resp.url
        )
        if is_login_wall:
            return None, "LOGIN_WALL"

        if resp.status_code == 200:
            match = re.search(r'<meta property="og:image" content="([^"]+)"', resp.text)
            if match:
                img_url = match.group(1).replace("&amp;", "&")
                return img_url, None
            match_json = re.search(r'"profile_pic_url_hd":"([^"]+)"', resp.text) or re.search(r'"profile_pic_url":"([^"]+)"', resp.text)
            if match_json:
                return match_json.group(1).encode().decode('unicode-escape').replace('\\/', '/'), None
            return None, "Profile image meta tag not found"
        elif resp.status_code == 404:
            return None, "Account not found / deleted (404)"
        else:
            return None, f"HTTP {resp.status_code}"
    except Exception as e:
        return None, f"Request error: {e}"

def worker():
    while True:
        item = task_queue.get()
        if item is None:
            break
            
        creator_id = item['creatorId']
        raw_handle = item['handle']
        handle = extract_handle(raw_handle)
        
        if not handle:
            print(f"Invalid handle '{raw_handle}' for creator {creator_id}. Skipping.")
            log_failure(creator_id, raw_handle, "Invalid or missing handle format")
            task_queue.task_done()
            continue
            
        try:
            print(f"[{time.strftime('%X')}] Processing IG handle @{handle} for creator {creator_id}...")
            
            with tempfile.TemporaryDirectory() as tmpdirname:
                jpg_file = None
                failure_reason = None
                
                # 1. Fast mobile HTML scraper (og:image)
                img_url, html_err = get_ig_pfp_mobile_html(handle)
                
                # Check for Instagram rate challenge / login wall
                if html_err == "LOGIN_WALL":
                    print(f"[{time.strftime('%X')}] [Instagram Login Wall / Rate Challenge] Instagram served a login challenge for @{handle}.")
                    print(f"--> Temporary challenge detected on your IP. NOT logging failure in Supabase.")
                    print(f"--> Re-queueing @{handle} and entering 10-minute cooldown before retrying...")
                    queued_creator_ids.discard(creator_id)
                    task_queue.put(item)
                    task_queue.task_done()
                    time.sleep(600)  # 10 minutes cooldown
                    continue

                if img_url:
                    print(f"Found profile picture via mobile HTML for @{handle}. Downloading...")
                    img_resp = requests.get(img_url, headers={
                        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
                    }, timeout=15)
                    if img_resp.status_code == 200:
                        jpg_file = os.path.join(tmpdirname, f"{creator_id}.jpg")
                        with open(jpg_file, 'wb') as f:
                            f.write(img_resp.content)
                    else:
                        failure_reason = f"Failed to download image from CDN (HTTP {img_resp.status_code})"
                        print(f"@{handle}: {failure_reason}")
                else:
                    print(f"Mobile HTML scraper did not find picture for @{handle}: {html_err}")
                    failure_reason = html_err
                            
                if jpg_file:
                    file_name = f"{creator_id}.jpg"
                    print(f"Uploading {file_name} to Supabase bucket 'profile-picture'...")
                    
                    with open(jpg_file, 'rb') as f:
                        supabase.storage.from_("profile-picture").upload(
                            file=f,
                            path=file_name,
                            file_options={"content-type": "image/jpeg", "upsert": "true"}
                        )
                        
                    public_url = supabase.storage.from_("profile-picture").get_public_url(file_name)
                    print(f"Updating Creator {creator_id} profileImage URL: {public_url}")
                    supabase.table("Creator").update({"profileImage": public_url, "pfpError": None}).eq("id", creator_id).execute()
                    print(f"Successfully updated @{handle}.")
                else:
                    reason = failure_reason or "Profile picture not found"
                    log_failure(creator_id, handle, reason)
                    print(f"Could not retrieve profile picture for @{handle}. Logged failure: {reason}")
                    
        except Exception as e:
            log_failure(creator_id, handle, f"Processing Error: {e}")
            print(f"Error processing @{handle}: {e}. Logged failure and continuing.")
            
        task_queue.task_done()
        # Randomized pacing delay (10-18s) to avoid tripping Instagram bot filters
        delay = random.uniform(10, 18)
        time.sleep(delay)

def enqueue_creator(creator_id, handle):
    if creator_id not in queued_creator_ids:
        queued_creator_ids.add(creator_id)
        print(f"Queued IG handle @{handle} for creator {creator_id}")
        task_queue.put({'creatorId': creator_id, 'handle': handle})

def poll_unprocessed_creators():
    try:
        # Get creators without profileImage and without a previous pfpError
        creators_res = supabase.table("Creator").select("id").is_("profileImage", "null").is_("pfpError", "null").execute()
        creators_without_pic = creators_res.data or []
        
        if not creators_without_pic:
            return

        missing_ids = [c["id"] for c in creators_without_pic]
        
        # Query CreatorPlatform for INSTAGRAM handles of these creators
        platforms_res = supabase.table("CreatorPlatform").select("creatorId, handle").eq("platform", "INSTAGRAM").in_("creatorId", missing_ids).execute()
        
        for record in (platforms_res.data or []):
            enqueue_creator(record["creatorId"], record["handle"])
    except Exception as e:
        print(f"Polling error: {e}")

async def listen_and_poll():
    # 1. First sweep of existing creators
    print("Performing initial sweep for creators missing profile pictures...")
    poll_unprocessed_creators()
    
    # 2. Setup Realtime subscription
    try:
        print("Connecting to Supabase Realtime async client...")
        async_supabase = await create_async_client(SUPABASE_URL, SUPABASE_KEY)
        channel = async_supabase.channel("public:CreatorPlatform")
        
        def on_insert(payload):
            print("Received INSERT event payload:", payload)
            record = getattr(payload, 'record', {}) or {}
            platform = record.get('platform', '')
            if platform and platform.upper() == 'INSTAGRAM':
                cid = record.get('creatorId')
                handle = record.get('handle')
                if cid and handle:
                    enqueue_creator(cid, handle)
                    
        channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="CreatorPlatform",
            callback=on_insert
        )
        await channel.subscribe()
        print("Realtime subscribed successfully!")
    except Exception as e:
        print(f"Realtime setup warning: {e}")

    print("Pipeline active! Listening for new creators & polling periodically... (Press Ctrl+C to stop)")
    
    # Periodic polling loop every 30s as backstop
    while True:
        await asyncio.sleep(30)
        poll_unprocessed_creators()

if __name__ == "__main__":
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    
    try:
        asyncio.run(listen_and_poll())
    except KeyboardInterrupt:
        print("Shutting down...")
