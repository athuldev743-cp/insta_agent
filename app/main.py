import asyncio
import os
import sys
import atexit
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from qstash import Receiver
import requests as req
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from starlette.requests import Request

from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.engine import run_engine
from app.social import post_reel_full_pipeline
from app.token_manager import auto_refresh_if_needed
from app.config import AGENT_CONFIG
from app.sports_fetcher import (
    get_top_sports_story,
    build_sports_theme,
    is_sports_theme,
    mark_as_posted,
    fetch_all_sports_news,
    ALL_FEEDS,
)

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

app       = FastAPI(title="Instagram Sports AI Agent", version="4.0.0")
scheduler = BackgroundScheduler(timezone=AGENT_CONFIG["timezone"])

# Initialize QStash Receiver for secure Render triggers
receiver = Receiver(
    current_signing_key=os.getenv("QSTASH_CURRENT_SIGNING_KEY"),
    next_signing_key=os.getenv("QSTASH_NEXT_SIGNING_KEY"),
)

# ═══════════════════════════════════════════════════════════════════════════
#  JOB STATE
# ═══════════════════════════════════════════════════════════════════════════

JOB_STATE = {
    "running":    False,
    "last_start": None,
    "last_end":   None,
    "last_error": None,
    "last_type":  None,    # Always "sports" or "realtime_sports" now
    "last_theme": None,
    "last_score": None,
}

# ═══════════════════════════════════════════════════════════════════════════
#  CORE CYCLE (Consolidated for Sports)
# ═══════════════════════════════════════════════════════════════════════════

def run_post_cycle_sync(theme: str | None = None, story_slot: int = 1, is_realtime: bool = False):
    """APScheduler wrapper to run the async post cycle."""
    tz = ZoneInfo(AGENT_CONFIG["timezone"])

    if JOB_STATE["running"]:
        print("[AGENT] ⛔ Busy — skipping slot", flush=True)
        return

    JOB_STATE["running"]    = True
    JOB_STATE["last_start"] = datetime.now(tz).isoformat()
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_post_cycle(theme=theme, story_slot=story_slot, is_realtime=is_realtime)
        )
        JOB_STATE["last_end"] = datetime.now(tz).isoformat()
    except Exception as e:
        JOB_STATE["last_error"] = str(e)
        print(f"[AGENT] ❌ Critical Error: {e}", flush=True)
    finally:
        JOB_STATE["running"] = False
        loop.close()

async def run_post_cycle(theme: str | None = None, story_slot: int = 1, is_realtime: bool = False):
    """The main logic to fetch news, generate video, and post."""
    
    # 1. Fetch the Story
    if theme:
        resolved_theme = theme
    else:
        # If is_realtime=True, it only returns a story if a match just ended
        article = get_top_sports_story(prefer_match_end=is_realtime, story_slot=story_slot)
        
        if not article:
            print(f"[AGENT] No fresh news for slot {story_slot}. Standing by.", flush=True)
            return
            
        resolved_theme = build_sports_theme(article)
        JOB_STATE["last_score"] = article.get("relevance_score", 0)
        mark_as_posted(article["url"])

    JOB_STATE["last_theme"] = resolved_theme
    JOB_STATE["last_type"] = "realtime_sports" if is_realtime else "sports"

    # 2. Run Pipeline
    try:
        print(f"[AGENT] 🚀 Starting Engine for: {resolved_theme[:70]}...", flush=True)
        result = await run_engine(resolved_theme)
        
        print("[AGENT] 📲 Uploading to Instagram...", flush=True)
        post_id = post_reel_full_pipeline(
            video_path=result["video_path"],
            caption=result["caption"],
        )
        print(f"[AGENT] ✅ Success! Post ID: {post_id}", flush=True)
    except Exception as e:
        raise e

# ═══════════════════════════════════════════════════════════════════════════
#  API ROUTES & SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    auto_refresh_if_needed()
    # Schedule daily slots (9AM, 2PM, 8PM) and the 15-min Watcher
    if not scheduler.running:
        for pt in AGENT_CONFIG["post_times"]:
            scheduler.add_job(
                run_post_cycle_sync,
                CronTrigger(hour=pt["hour"], minute=pt["minute"]),
                kwargs={"story_slot": pt.get("story_slot", 1)},
                id=pt["label"]
            )
        
        # Real-time watcher
        scheduler.add_job(run_post_cycle_sync, IntervalTrigger(minutes=15), kwargs={"is_realtime": True}, id="watcher")
        scheduler.start()
        print(f"[AGENT] ⚽ Sports Agent Live | {len(ALL_FEEDS)} Feeds active", flush=True)

@app.get("/health")
def health():
    return {"status": "healthy", "jobs": len(scheduler.get_jobs())}


@app.post("/run-engine")
async def trigger_engine(request: Request, background_tasks: BackgroundTasks):
    signature = request.headers.get("upstash-signature")
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    
    # --- UPDATE THIS BLOCK ---
    # Allow bypass if RENDER env var is not set OR if testing locally
    is_render = os.getenv("RENDER_APP_URL") == "true"
    
    if is_render:
        if not signature:
            print("[SECURITY] Missing signature on Render - Rejecting")
            raise HTTPException(status_code=401, detail="Unauthorized")
        try:
            receiver.verify(body=body_str, signature=signature)
        except Exception as e:
            print(f"[SECURITY] Signature verification failed: {e}")
            raise HTTPException(status_code=401, detail="Unauthorized")
    else:
        print("[DEBUG] Signature check bypassed (Local/Manual Test)")
    # -------------------------

    background_tasks.add_task(run_post_cycle_sync, story_slot=1)
    return {"status": "accepted", "message": "Engine starting in background..."}