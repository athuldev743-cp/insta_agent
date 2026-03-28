# main.py
# =====================================================
# INSTAGRAM SPORTS AI AGENT v4 — IPL SCHEDULE EDITION
# + Strict 9 AM, 2 PM, 9 PM IST Slots
# + Match-Aware Logic (Noon Previews vs. Night Results)
# + No AI Image Dependencies
# =====================================================

import asyncio
import os
import sys
import atexit
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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

# QStash is optional
try:
    from qstash import Receiver
    _qstash_receiver = Receiver(
        current_signing_key=os.getenv("QSTASH_CURRENT_SIGNING_KEY", ""),
        next_signing_key=os.getenv("QSTASH_NEXT_SIGNING_KEY", ""),
    )
except Exception:
    _qstash_receiver = None

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

app       = FastAPI(title="Instagram Sports AI Agent", version="4.0.0")
scheduler = BackgroundScheduler(timezone=AGENT_CONFIG["timezone"])

TZ = ZoneInfo(AGENT_CONFIG["timezone"])

# ═══════════════════════════════════════════════════════════════════════════
#  JOB STATE
# ═══════════════════════════════════════════════════════════════════════════

JOB_STATE = {
    "running":    False,
    "last_start": None,
    "last_end":   None,
    "last_error": None,
    "last_type":  None,
    "last_theme": None,
    "last_score": None,
}

# ═══════════════════════════════════════════════════════════════════════════
#  CORE CYCLE
# ═══════════════════════════════════════════════════════════════════════════

def run_post_cycle_sync(
    theme: str | None = None,
    story_slot: int = 1,
    is_realtime: bool = False,
):
    """APScheduler-safe sync wrapper."""
    if JOB_STATE["running"]:
        print("[AGENT] ⛔ Busy — skipping", flush=True)
        return

    JOB_STATE["running"]    = True
    JOB_STATE["last_start"] = datetime.now(TZ).isoformat()
    JOB_STATE["last_end"]   = None
    JOB_STATE["last_error"] = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_post_cycle(theme=theme, story_slot=story_slot, is_realtime=is_realtime)
        )
        JOB_STATE["last_end"] = datetime.now(TZ).isoformat()
    except Exception as e:
        import traceback
        JOB_STATE["last_error"] = str(e)
        JOB_STATE["last_end"]   = datetime.now(TZ).isoformat()
        print(f"[AGENT] ❌ Error: {e}", flush=True)
        traceback.print_exc()
    finally:
        JOB_STATE["running"] = False
        loop.close()

async def run_post_cycle(
    theme: str | None = None,
    story_slot: int = 1,
    is_realtime: bool = False,
):
    """Fetch news → generate video → post to Instagram."""

    # ── 1. Resolve theme (IPL Match Aware) ────────────────────────────────
    if theme:
        resolved_theme = theme
    else:
        # If it's the 9 PM slot or Real-time, we prefer Match Results
        current_hour = datetime.now(TZ).hour
        is_night_slot = (current_hour >= 20 or current_hour <= 1)
        
        article = get_top_sports_story(
            prefer_match_end=(is_realtime or is_night_slot),
            story_slot=story_slot,
        )
        
        if not article:
            print(f"[AGENT] ℹ️ No fresh IPL story found right now. Standing by.", flush=True)
            return

        resolved_theme          = build_sports_theme(article)
        JOB_STATE["last_score"] = article.get("relevance_score", 0)
        mark_as_posted(article["url"])

    JOB_STATE["last_theme"] = resolved_theme
    JOB_STATE["last_type"]  = "realtime_sports" if is_realtime else "sports"

    # ── 2. Engine (8-Slot Web Scrape) + Post ─────────────────────────────
    print(f"[AGENT] 🚀 Engine Starting: {resolved_theme[:80]}", flush=True)
    result = await run_engine(resolved_theme)

    print("[AGENT] 📲 Posting Reel to Instagram...", flush=True)
    post_id = post_reel_full_pipeline(
        video_path=result["video_path"],
        caption=result["caption"],
    )
    print(f"[AGENT] ✅ POST SUCCESS! ID: {post_id}", flush=True)

# ═══════════════════════════════════════════════════════════════════════════
#  KEEP-ALIVE
# ═══════════════════════════════════════════════════════════════════════════

def keep_alive_ping():
    try:
        url = os.getenv("RENDER_APP_URL", "http://localhost:8000").rstrip("/")
        req.get(f"{url}/health", timeout=10)
        print("[AGENT] 💓 Keep-alive ping", flush=True)
    except Exception as e:
        print(f"[AGENT] ⚠️ Keep-alive failed: {e}", flush=True)

# ═══════════════════════════════════════════════════════════════════════════
#  STARTUP & IPL SCHEDULER
# ═══════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    auto_refresh_if_needed()

    if scheduler.running:
        return

    tz_name = AGENT_CONFIG["timezone"]

    # ── IPL SHIFT SCHEDULE (9 AM, 2 PM, 9 PM IST) ────────────────────────
    ipl_schedule = [
        {"hour": 9,  "minute": 0,  "label": "Morning_Review", "slot": 1}, # Last night review
        {"hour": 14, "minute": 0,  "label": "Noon_Lineups",   "slot": 1}, # Today's match preview
        {"hour": 21, "minute": 0,  "label": "Night_Results",  "slot": 1}, # Mid/End match update
    ]

    for pt in ipl_schedule:
        scheduler.add_job(
            run_post_cycle_sync,
            CronTrigger(hour=pt["hour"], minute=pt["minute"], timezone=tz_name),
            kwargs={"story_slot": pt["slot"]},
            id=pt["label"],
            name=f"IPL {pt['label']} Slot",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
        )
        print(f"[AGENT] ✅ IPL Scheduled: {pt['label']} at {pt['hour']:02d}:{pt['minute']:02d} IST", flush=True)

    # ── Real-time match watcher every 20 mins ─────────────────────────────
    # Increased to 20 mins to save memory during heavy IPL traffic
    scheduler.add_job(
        run_post_cycle_sync,
        IntervalTrigger(minutes=20, timezone=tz_name),
        kwargs={"is_realtime": True},
        id="watcher",
        name="Real-Time IPL Watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # ── Keep-alive ping ──────────────────────────────────────────────────
    scheduler.add_job(
        keep_alive_ping,
        CronTrigger(minute="*/14", timezone=tz_name),
        id="keep_alive",
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    print(f"[AGENT] 🏏 IPL Agent v4.1 Live | {tz_name} | 3-Post Cycle Active", flush=True)

# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    return {
        "service": "IPL Instagram Agent",
        "status": "running",
        "schedule": "9:00, 14:00, 21:00 IST",
        "last_run": JOB_STATE.get("last_end"),
    }

@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request):
    return {
        "status": "healthy",
        "jobs_active": len(scheduler.get_jobs()),
        "agent_state": JOB_STATE,
        "timestamp": datetime.now(TZ).isoformat(),
    }

@app.get("/sports-preview")
async def sports_preview(story_slot: int = 1, match_end_only: bool = False):
    article = get_top_sports_story(prefer_match_end=match_end_only, story_slot=story_slot)
    if not article: return {"status": "no_news"}
    return {
        "title": article["title"],
        "score": article.get("relevance_score", 0),
        "source": article["source"],
        "is_match_end": article.get("is_match_end", False),
        "img": article.get("image_url", "")
    }

@app.api_route("/post-now", methods=["GET", "POST"])
async def post_now(background_tasks: BackgroundTasks, story_slot: int = 1):
    if JOB_STATE["running"]: return {"status": "busy"}
    background_tasks.add_task(run_post_cycle_sync, story_slot=story_slot)
    return {"status": "started"}