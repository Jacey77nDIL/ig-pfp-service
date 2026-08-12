import os
import time
import threading
import queue
import tempfile
import asyncio
import re
from dotenv import load_dotenv
from supabase import create_client, create_async_client, Client
import instaloader
import requests

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

def extract_handle(raw_handle: str) -> str:
    if not raw_handle:
        return ""
    raw_handle = raw_handle.strip()
    if "instagram.com/" in raw_handle:
        parts = raw_handle.split("instagram.com/")[1]
        username = parts.split("/")[0].split("?")[0]
        return username
    return raw_handle.lstrip("@").strip()

def get_ig_pfp_fallback(handle: str) -> str:
    url = f"https://www.instagram.com/{handle}/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            match = re.search(r'<meta property="og:image" content="([^"]+)"', resp.text)
            if match:
                img_url = match.group(1).replace("&amp;", "&")
                return img_url
    except Exception as e:
        print(f"Fallback request failed for @{handle}: {e}")
    return None

def worker():
    L = instaloader.Instaloader()
    while True:
        item = task_queue.get()
        if item is None:
            break
            
        creator_id = item['creatorId']
        raw_handle = item['handle']
        handle = extract_handle(raw_handle)
        
        if not handle:
            print(f"Invalid handle '{raw_handle}' for creator {creator_id}. Skipping.")
            task_queue.task_done()
            continue
            
        try:
            print(f"[{time.strftime('%X')}] Processing IG handle @{handle} for creator {creator_id}...")
            
            with tempfile.TemporaryDirectory() as tmpdirname:
                jpg_file = None
                try:
                    L.dirname_pattern = tmpdirname
                    L.download_profile(handle, profile_pic_only=True)
                    
                    for root, dirs, files in os.walk(tmpdirname):
                        for file in files:
                            if file.endswith('.jpg'):
                                jpg_file = os.path.join(root, file)
                                break
                except Exception as inner_e:
                    error_msg = str(inner_e).lower()
                    if "401" in error_msg or "429" in error_msg or "rate" in error_msg or "too many" in error_msg or "please wait" in error_msg or "login" in error_msg:
                        raise inner_e  # Pass rate limits up to trigger cooldown
                    else:
                        print(f"Instaloader failed for @{handle} ({inner_e}). Trying HTML fallback...")
                        img_url = get_ig_pfp_fallback(handle)
                        if img_url:
                            print(f"Fallback successful! Downloading image from {img_url[:40]}...")
                            img_resp = requests.get(img_url, timeout=10)
                            if img_resp.status_code == 200:
                                jpg_file = os.path.join(tmpdirname, f"{creator_id}.jpg")
                                with open(jpg_file, 'wb') as f:
                                    f.write(img_resp.content)
                        else:
                            print(f"Fallback failed to find profile picture for @{handle}.")
                            
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
                    supabase.table("Creator").update({"profileImage": public_url}).eq("id", creator_id).execute()
                    print(f"Successfully updated @{handle}.")
                else:
                    print(f"Could not find a downloaded jpg for @{handle}.")
                    
        except Exception as e:
            error_msg = str(e).lower()
            if "401" in error_msg or "429" in error_msg or "rate" in error_msg or "too many" in error_msg or "please wait" in error_msg or "login" in error_msg:
                print(f"Rate limit or Auth error hit for @{handle}: {e}")
                print("Re-enqueueing handle and pausing worker for 15 minutes...")
                if creator_id in queued_creator_ids:
                    queued_creator_ids.remove(creator_id)
                enqueue_creator(creator_id, raw_handle)
                task_queue.task_done()
                time.sleep(900)  # 15 minutes cooldown
                continue
            else:
                print(f"Error processing @{handle}: {e}")
            
        task_queue.task_done()
        print("Sleeping for 60 seconds to avoid IG rate limits...")
        time.sleep(60)

def enqueue_creator(creator_id, handle):
    if creator_id not in queued_creator_ids:
        queued_creator_ids.add(creator_id)
        print(f"Queued IG handle @{handle} for creator {creator_id}")
        task_queue.put({'creatorId': creator_id, 'handle': handle})

def poll_unprocessed_creators():
    try:
        # Get creators without profileImage
        creators_res = supabase.table("Creator").select("id").is_("profileImage", "null").execute()
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
