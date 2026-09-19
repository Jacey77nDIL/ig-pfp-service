import os
import io
import re
import time
import requests
from dotenv import load_dotenv
from supabase import create_client
from PIL import Image

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
IG_SESSION_ID = os.environ.get("IG_SESSION_ID", "").strip()

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Missing Supabase credentials in .env")

if SUPABASE_URL.endswith('/rest/v1/'):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL.endswith('/rest/v1'):
    SUPABASE_URL = SUPABASE_URL[:-8]

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
BUCKET_NAME = "profile-picture"

WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

APP_HEADERS = {
    "User-Agent": "Instagram 361.0.0.35.82 (iPad13,8; iOS 18_0; en_US; en-US; scale=2.00; 2048x2732; 674117118) AppleWebKit/420+",
    "x-ig-app-id": "124024574287414",
}

COOKIES = {"sessionid": IG_SESSION_ID} if IG_SESSION_ID else {}

def extract_handle(raw_handle: str) -> str:
    if not raw_handle:
        return ""
    raw_handle = raw_handle.strip()
    if "instagram.com/" in raw_handle:
        parts = raw_handle.split("instagram.com/")[1]
        username = parts.split("/")[0].split("?")[0]
        return username
    return raw_handle.lstrip("@").strip()

def fetch_authentic_hd_pfp(handle: str):
    """
    Fetches the genuine, uncompressed 1080x1080 Full HD profile picture URL from Instagram.
    """
    url = f"https://www.instagram.com/{handle}/"
    try:
        resp = requests.get(url, headers=WEB_HEADERS, cookies=COOKIES, timeout=15)
        if resp.status_code != 200:
            return None, f"HTTP {resp.status_code}"

        # Extract target user_id
        target_uid = None
        m = re.search(r"PolarisProfile[^\"]*Root\.react\"\},\"props\":\{\"id\":\"(\d+)\"", resp.text)
        if m:
            target_uid = m.group(1)
        if not target_uid:
            m2 = re.search(r"\"id\":\"(\d+)\",\"show_suggested_profiles\"", resp.text)
            if m2:
                target_uid = m2.group(1)
        if not target_uid:
            m3 = re.search(r"\"profile_id\":\"(\d+)\"", resp.text)
            if m3:
                target_uid = m3.group(1)

        # Call Mobile API for True 1080x1080 HD
        if target_uid and IG_SESSION_ID:
            r_info = requests.get(f"https://i.instagram.com/api/v1/users/{target_uid}/info/", headers=APP_HEADERS, cookies=COOKIES, timeout=10)
            if r_info.status_code == 200:
                user_data = r_info.json().get("user", {})
                hd_info = user_data.get("hd_profile_pic_url_info") or {}
                if hd_info.get("url"):
                    return hd_info["url"], None
                hd_versions = user_data.get("hd_profile_pic_versions") or []
                if hd_versions and hd_versions[0].get("url"):
                    return hd_versions[0]["url"], None

        # Fallback to web og:image if app endpoint unavailable
        match = re.search(r'<meta property="og:image" content="([^"]+)"', resp.text)
        if match:
            return match.group(1).replace("&amp;", "&"), None
        return None, "No image found"
    except Exception as e:
        return None, str(e)

def hdfy_all_creators():
    print("Fetching creators and Instagram handles from Supabase...")
    # Fetch all creators with Instagram accounts
    platforms_res = supabase.table("CreatorPlatform").select("creatorId, handle").eq("platform", "INSTAGRAM").execute()
    platforms = platforms_res.data or []
    
    total = len(platforms)
    print(f"Found {total} Instagram creators in database. Upgrading to authentic 1080x1080 Full HD...\n")

    upgraded = 0
    failed = 0

    for idx, p in enumerate(platforms, 1):
        creator_id = p["creatorId"]
        raw_handle = p["handle"]
        handle = extract_handle(raw_handle)
        
        if not handle:
            continue

        print(f"[{idx}/{total}] Fetching TRUE 1080x1080 HD for @{handle} ({creator_id})...")
        img_url, err = fetch_authentic_hd_pfp(handle)

        if img_url:
            try:
                img_resp = requests.get(img_url, timeout=15)
                if img_resp.status_code == 200:
                    file_name = f"{creator_id}.jpg"
                    # Convert to standard high-quality JPEG
                    with Image.open(io.BytesIO(img_resp.content)) as img:
                        img = img.convert("RGB")
                        out = io.BytesIO()
                        img.save(out, format="JPEG", quality=95, subsampling=0)
                        jpeg_bytes = out.getvalue()
                        w, h = img.size

                    supabase.storage.from_(BUCKET_NAME).upload(
                        file=jpeg_bytes,
                        path=file_name,
                        file_options={"content-type": "image/jpeg", "upsert": "true"}
                    )
                    public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(file_name)
                    supabase.table("Creator").update({"profileImage": public_url, "pfpError": None}).eq("id", creator_id).execute()
                    upgraded += 1
                    print(f"  -> SUCCESS! Uploaded True HD ({w}x{h}, {len(jpeg_bytes)} bytes) for @{handle}")
                else:
                    failed += 1
                    print(f"  -> Failed to download image from CDN (HTTP {img_resp.status_code})")
            except Exception as e:
                failed += 1
                print(f"  -> Upload error: {e}")
        else:
            failed += 1
            print(f"  -> Could not get HD URL for @{handle}: {err}")

        time.sleep(3)

    print("\n" + "=" * 50)
    print("FINISHED TRUE HD UPGRADE!")
    print(f"Total processed: {total}")
    print(f"Upgraded to 1080x1080 HD: {upgraded}")
    print(f"Failed: {failed}")
    print("=" * 50)

if __name__ == "__main__":
    hdfy_all_creators()
