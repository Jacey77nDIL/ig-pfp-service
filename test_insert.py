import os
import uuid
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")

if SUPABASE_URL.endswith('/rest/v1/'):
    SUPABASE_URL = SUPABASE_URL[:-9]
elif SUPABASE_URL.endswith('/rest/v1'):
    SUPABASE_URL = SUPABASE_URL[:-8]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

response = supabase.table("Creator").select("id").limit(1).execute()
creators = response.data

if not creators:
    print("No creators found in the DB. Please create one manually first.")
    exit(1)

creator_id = creators[0]["id"]
test_handle = "cristiano" # the user used this handle in their demo

data = {
    "id": str(uuid.uuid4()),
    "creatorId": creator_id,
    "platform": "INSTAGRAM",
    "handle": test_handle,
    "followers": 0,
    "verified": True,
    "profileUrl": f"https://instagram.com/{test_handle}"
}

print(f"Inserting test Instagram record for @{test_handle}...")
supabase.table("CreatorPlatform").insert(data).execute()
print("Success! The listener should now pick this up.")
