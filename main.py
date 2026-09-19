import os
import sys
import time
import queue
import threading
import asyncio
import tempfile
import random
import requests
import re
import io
from PIL import Image, ImageEnhance
from dotenv import load_dotenv
from supabase import create_client, create_async_client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if SUPABASE_URL and SUPABASE_URL.endswith("/rest/v1/"):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL and SUPABASE_URL.endswith("/rest/v1"):
    SUPABASE_URL = SUPABASE_URL[:-8]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "profile-picture"

task_queue = queue.Queue()
queued_creator_ids = set()

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

APP_HEADERS = {
    "User-Agent": "Instagram 361.0.0.35.82 (iPad13,8; iOS 18_0; en_US; en-US; scale=2.00; 2048x2732; 674117118) AppleWebKit/420+",
    "x-ig-app-id": "124024574287414",
}

TIKTOK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def extract_handle(raw):
    if not raw: return ""
    raw = raw.strip()
    if "instagram.com/" in raw:
        return raw.split("instagram.com/")[1].split("/")[0].split("?")[0]
    if "tiktok.com/@" in raw:
        return raw.split("tiktok.com/@")[1].split("/")[0].split("?")[0]
    return raw.lstrip("@").strip()

def hdfy_image_bytes(img_bytes):
    with Image.open(io.BytesIO(img_bytes)) as img:
        img = img.convert("RGB")
        w, h = img.size
        
        # If dimensions < 720x720, upscale to 1080x1080 with Lanczos resampling and sharpening
        if w < 720 or h < 720:
            target_size = (1080, 1080)
            img = img.resize(target_size, Image.Resampling.LANCZOS)
            enhancer = ImageEnhance.Sharpen(img)
            img = enhancer.enhance(1.25)
            contrast = ImageEnhance.Contrast(img)
            img = contrast.enhance(1.04)
        
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=95, subsampling=0)
        final_bytes = out.getvalue()
        return final_bytes, img.size[0], img.size[1]

def upload_to_supabase(creator_id, img_bytes):
    final_jpeg, w, h = hdfy_image_bytes(img_bytes)
    file_name = f"{creator_id}.jpg"
    
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
        "Content-Type": "image/jpeg",
        "x-upsert": "true",
    }
    upload_url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{file_name}"
    resp = requests.post(upload_url, headers=headers, data=final_jpeg, timeout=30)
    if resp.status_code not in [200, 201]:
        raise Exception(f"Storage upload error: HTTP {resp.status_code} {resp.text}")
    
    public_url = f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{file_name}"
    supabase.table("Creator").update({"profileImage": public_url, "pfpError": None}).eq("id", creator_id).execute()
    return public_url, w, h, len(final_jpeg)

def fetch_tiktok_avatar(handle):
    h = extract_handle(handle)
    resp = requests.get(f"https://www.tiktok.com/@{h}", headers=TIKTOK_HEADERS, timeout=20)
    if resp.status_code != 200:
        return None
    avatars = re.findall(r""avatarLarger":"([^"]+)"", resp.text) or re.findall(r""avatarMedium":"([^"]+)"", resp.text)
    if avatars:
        return avatars[0].encode().decode("unicode-escape").replace(r"\/", "/")
    og = re.findall(r"property=["']og:image["']\s+content=["']([^'"]+)["']", resp.text)
    if og: return og[0]
    return None

def fetch_ig_hd_avatar(handle):
    h = extract_handle(handle)
    ig_session = os.environ.get("IG_SESSION_ID", "").strip()
    cookies = {"sessionid": ig_session} if ig_session else {}
    
    resp = requests.get(f"https://www.instagram.com/{h}/", headers=WEB_HEADERS, cookies=cookies, timeout=20)
    if resp.status_code == 200:
        m = re.search(r"PolarisProfile[^"]*Root\.react"\},"props":\{"id":"(\d+)"", resp.text) or re.search(r""id":"(\d+)","show_suggested_profiles"", resp.text)
        if m and ig_session:
            uid = m.group(1)
            r_info = requests.get(f"https://i.instagram.com/api/v1/users/{uid}/info/", headers=APP_HEADERS, cookies=cookies, timeout=25)
            if r_info.status_code == 200:
                user = r_info.json().get("user", {})
                hd_info = user.get("hd_profile_pic_url_info") or {}
                if hd_info.get("url"): return hd_info["url"]
                vers = user.get("hd_profile_pic_versions") or []
                if vers and vers[0].get("url"): return vers[0]["url"]
        
        m_og = re.findall(r"property=["']og:image["']\s+content=["']([^'"]+)["']", resp.text)
        if m_og:
            return m_og[0].replace("&amp;", "&")
    return None

def process_creator(cid):
    platforms = supabase.table("CreatorPlatform").select("platform, handle").eq("creatorId", cid).execute().data or []
    
    img_url = None
    source = None

    # Tier 1: Try Instagram True HD if handle available
    ig_plat = next((p for p in platforms if p["platform"] == "INSTAGRAM"), None)
    if ig_plat:
        h = extract_handle(ig_plat["handle"])
        img_url = fetch_ig_hd_avatar(h)
        if img_url:
            source = f"Instagram (@{h})"

    # Tier 2: Try TikTok avatarLarger HD if no IG image
    if not img_url:
        tt_plat = next((p for p in platforms if p["platform"] == "TIKTOK"), None)
        if tt_plat:
            h = extract_handle(tt_plat["handle"])
            img_url = fetch_tiktok_avatar(h)
            if img_url:
                source = f"TikTok (@{h})"

    if img_url:
        r_img = requests.get(img_url, timeout=25)
        if r_img.status_code == 200:
            pub_url, w, h, nbytes = upload_to_supabase(cid, r_img.content)
            print(f"[{time.strftime(%X)}] SUCCESS! Creator {cid} -> {w}x{h} ({nbytes} bytes) from {source}")
            return True
    
    supabase.table("Creator").update({"pfpError": "Could not retrieve HD profile picture"}).eq("id", cid).execute()
    print(f"[{time.strftime(%X)}] FAILED for Creator {cid}")
    return False

def worker():
    print(f"[{time.strftime(%X)}] Worker thread started.")
    while True:
        try:
            cid = task_queue.get(timeout=5)
        except queue.Empty:
            time.sleep(1)
            continue

        try:
            process_creator(cid)
        except Exception as e:
            print(f"Worker exception for {cid}: {e}")
        finally:
            queued_creator_ids.discard(cid)
            task_queue.task_done()
            time.sleep(1.5)

def enqueue_creator(cid):
    if cid not in queued_creator_ids:
        queued_creator_ids.add(cid)
        task_queue.put(cid)

def poll_unprocessed_creators():
    try:
        res = supabase.table("Creator").select("id").is_("profileImage", "null").is_("pfpError", "null").execute()
        for c in (res.data or []):
            enqueue_creator(c["id"])
    except Exception as e:
        print(f"Polling error: {e}")

async def listen_and_poll():
    print("Performing initial sweep for creators missing profile pictures...")
    poll_unprocessed_creators()
    
    try:
        async_supabase = await create_async_client(SUPABASE_URL, SUPABASE_KEY)
        channel = async_supabase.channel("public:Creator")
        
        def on_insert(payload):
            record = getattr(payload, "record", {}) or {}
            cid = record.get("id")
            if cid and not record.get("profileImage"):
                enqueue_creator(cid)
                    
        channel.on_postgres_changes(event="INSERT", schema="public", table="Creator", callback=on_insert)
        await channel.subscribe()
        print("Realtime active!")
    except Exception as e:
        print(f"Realtime setup notice: {e}")

    while True:
        await asyncio.sleep(25)
        poll_unprocessed_creators()

if __name__ == "__main__":
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    
    try:
        asyncio.run(listen_and_poll())
    except KeyboardInterrupt:
        print("Shutting down...")
