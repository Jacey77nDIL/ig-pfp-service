# Caskayd Services

An automated Python micro-services suite for Caskayd powering True HD avatar ingestion, avatar refreshes, and automated creator discovery crawling.

## Services Overview

### 1. New Creator HD Avatar Daemon (`main.py`)
- Automatically listens for newly registered creators who do **not** yet have an avatar (`profileImage IS NULL AND pfpError IS NULL`).
- Never redownloads or overwrites existing creator profile pictures.
- **4-Tier Waterfall Scraper**:
  1. Instagram True HD mobile uncompressed extraction.
  2. TikTok 1080x1080 True HD avatar extraction.
  3. Instaloader query.
  4. Web HTML metadata fallback.
- **HDfy Engine**: Upscales low-res profile pictures via Lanczos high-order filter and applies subtle unsharp masking for studio-grade clarity.
- Uploads directly to Supabase Storage (`profile-picture/{creatorId}.jpg`) and updates `Creator.profileImage`.

Run daemon:
```bash
python -u main.py
```

---

### 2. Explicit Profile Picture Updater (`update_pfps.py`)
Explicitly refreshes profile pictures for creators who **already have an image**. Only runs when explicitly triggered.

- Refresh a specific creator:
  ```bash
  python update_pfps.py --id <creator_uuid>
  ```
- Batch refresh existing creators:
  ```bash
  python update_pfps.py --all --limit 50
  ```

---

### 3. Automated Discovery Crawler Bot (`crawler.py`)
An intelligent graph-walking discovery crawler that automatically expands Caskayd's creator database.

- **How it works**:
  - Seeds from top verified creators in the database (or custom seeds).
  - Queries Instagram's official chaining endpoint (`discover/chaining/?target_id={pk}`) using `IG_SESSION_ID` to discover 70–80 similar creators per seed.
  - Automatically filters out private accounts, creators already in `Creator`, and handles already in `CreatorSuggestion`.
  - Ingests new creators directly into the backend `CreatorSuggestion` queue with status `PENDING` for 1-click admin approval.

Run crawler:
```bash
# Crawl 50 new creators starting from default verified seeds
python crawler.py --max 50

# Crawl from custom seed creators
python crawler.py --seeds hildabaci brodashagi taaooma --max 100
```

---

## Environment Variables (`.env`)

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=your-supabase-service-role-key
IG_SESSION_ID=your-instagram-session-cookie
```

## Setup

```bash
source venv/bin/activate
pip install -r requirements.txt
```
