import os
import sys
import time
import argparse
import requests
from dotenv import load_dotenv
from supabase import create_client
from main import (
    SUPABASE_URL,
    SUPABASE_KEY,
    supabase,
    BUCKET_NAME,
    process_creator,
    fetch_instagram_avatar,
    fetch_tiktok_avatar,
    upload_to_supabase
)

def update_creator_pfp(creator_id, force=False):
    """
    Explicitly forces a refresh of a creator's profile picture.
    """
    print(f"[{time.strftime('%X')}] Refreshing profile picture for Creator {creator_id}...")
    success = process_creator(creator_id)
    return success

def update_all_existing_pfps(limit=50):
    """
    Batch updates profile pictures for creators that ALREADY have an image.
    Only run when explicitly requested.
    """
    print(f"[{time.strftime('%X')}] Scanning for existing creators to refresh (limit={limit})...")
    res = supabase.table("Creator").select("id, profileImage").not_.is_("profileImage", "null").limit(limit).execute()
    creators = res.data or []
    print(f"[{time.strftime('%X')}] Found {len(creators)} creators with existing avatars.")
    
    updated = 0
    for c in creators:
        cid = c["id"]
        if process_creator(cid):
            updated += 1
        time.sleep(1) # respectful pacing
    
    print(f"[{time.strftime('%X')}] Done! Successfully refreshed {updated}/{len(creators)} profile pictures.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explicitly update creator profile pictures.")
    parser.add_argument("--id", type=str, help="Specific Creator ID to refresh")
    parser.add_argument("--all", action="store_true", help="Refresh all existing creators with avatars")
    parser.add_argument("--limit", type=int, default=50, help="Max creators to process when using --all")
    
    args = parser.parse_args()
    
    if args.id:
        update_creator_pfp(args.id)
    elif args.all:
        update_all_existing_pfps(args.limit)
    else:
        parser.print_help()
