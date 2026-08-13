# IG-PFP Service (Instagram Profile Picture Downloader)

An automated Python background service that fetches profile pictures for Instagram creators, uploads them to a Supabase Storage bucket (`profile-picture`), and updates the `profileImage` field in the database `Creator` table.

## Features

- **Hybrid Listener & Polling**: Uses Supabase Realtime to listen for new `INSTAGRAM` creator rows added to `CreatorPlatform`, while running a periodic 30-second poll to ensure no inserts are missed.
- **Initial Backfill Sweep**: Scans the database on startup and automatically queues all existing creators who are missing a profile picture and haven't previously errored out (`profileImage IS NULL AND pfpError IS NULL`).
- **Rate Limit & Cooldown**: Throttles Instagram requests to 1 handle per 60 seconds. Automatically detects 401/429 rate limit responses and pauses the worker for 15 minutes to let the IP cool down.
- **HTML Fallback**: Includes a fallback parser that extracts public profile pictures directly from Instagram's HTML meta tags (`og:image`) if `instaloader` hits schema or API errors on business/creator accounts.
- **Database Failure Reporting (`pfpError`)**: If a creator fails to process (e.g. 404 account not found, 400 schema error, invalid handle format), the failure reason is recorded directly into the `pfpError` column in Supabase, leaving `profileImage` untouched (`NULL`). This ensures remote execution writes no local files and allows polling to skip failed handles automatically.

## Prerequisites

- Python 3.11+
- Virtual environment set up in `venv/`
- Environment variables configured in a `.env` file

## Environment Variables (`.env`)

Create a `.env` file in the project root:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-supabase-service-role-key
```

## Setup & Running

1. **Activate the Virtual Environment**:
   ```bash
   source venv/bin/activate
   ```

2. **Install Dependencies** (if needed):
   ```bash
   pip install -r requirements.txt
   ```
   *(Main packages required: `supabase`, `instaloader`, `requests`, `python-dotenv`)*

3. **Run the Service**:
   ```bash
   python -u main.py
   ```

The `-u` flag ensures log output is unbuffered so you can see live progress in real-time.
