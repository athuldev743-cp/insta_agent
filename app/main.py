import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import requests as req
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import atexit

from engine import run_engine
from social import post_reel_full_pipeline
from token_manager import auto_refresh_if_needed
from config import AGENT_CONFIG  # ← import from config

load_dotenv()

app = FastAPI(title="Instagram AI Agent", version="1.0.0")

scheduler   = BackgroundScheduler(timezone="Asia/Kolkata")
THEMES      = AGENT_CONFIG["themes"]     # ← from config (30 themes)
theme_index = 0


def run_post_cycle():
    global theme_index
    theme       = THEMES[theme_index % len(THEMES)]
    theme_index += 1
    print(f"\n[AGENT] 🚀 Starting cycle → Theme: {theme}")

    try:
        result  = asyncio.run(run_engine(theme))
        post_id = post_reel_full_pipeline(
            video_path=result['video_path'],
            caption=result['caption']
        )
        print(f"[AGENT] ✅ Posted! ID: {post_id}")
    except Exception as e:
        print(f"[AGENT] ❌ Failed: {e}")


def keep_alive_ping():
    """Ping self every 14 minutes to prevent Render from sleeping"""
    try:
        app_url = os.getenv("RENDER_APP_URL", "http://localhost:8000")
        req.get(f"{app_url}/health", timeout=10)
        print("[AGENT] 💓 Keep-alive ping sent")
    except Exception as e:
        print(f"[AGENT] ⚠️  Keep-alive ping failed: {e}")


@app.on_event("startup")
async def startup():
    auto_refresh_if_needed()

    post_times = AGENT_CONFIG["post_times"]
    tz         = AGENT_CONFIG["timezone"]

    # Add post jobs from config
    for pt in post_times:
        scheduler.add_job(
            run_post_cycle,
            CronTrigger(hour=pt["hour"], minute=pt["minute"], timezone=tz),
            id=pt["label"],
            name=f"Post at {pt['hour']:02d}:{pt['minute']:02d}"
        )
        print(f"[AGENT] ✅ Scheduled: {pt['label']} at {pt['hour']:02d}:{pt['minute']:02d} {tz}")

    # Keep-alive ping every 14 minutes (prevents Render sleep)
    scheduler.add_job(
        keep_alive_ping,
        CronTrigger(minute="*/14"),   # every 14 minutes
        id="keep_alive",
        name="Keep Alive Ping"
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    print(f"[AGENT] ✅ Agent live — {len(THEMES)} themes loaded")


@app.get("/")
def root():
    return {"status": "running", "agent": "Instagram AI Agent - Web Dev"}


@app.get("/health")
def health():
    checks = {
        "instagram_user_id": bool(os.getenv("INSTAGRAM_USER_ID")),
        "instagram_token":   bool(os.getenv("INSTAGRAM_ACCESS_TOKEN")),
        "openrouter_key":    bool(os.getenv("OPENROUTER_API_KEY")),
        "hf_key":            bool(os.getenv("HF_API_KEY")),
        "cloudinary":        bool(os.getenv("CLOUDINARY_CLOUD_NAME")),
    }
    all_ok = all(checks.values())
    return JSONResponse(
        content={"status": "healthy" if all_ok else "missing env vars", "checks": checks},
        status_code=200 if all_ok else 500
    )


@app.post("/post-now")
async def post_now(background_tasks: BackgroundTasks, theme: str = "HTML basics"):
    background_tasks.add_task(run_post_cycle)
    return {"status": "started", "theme": theme}


@app.get("/schedule-status")
def schedule_status():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id":       job.id,
            "name":     job.name,
            "next_run": str(job.next_run_time),
        })
    return {
        "status":       "running",
        "timezone":     AGENT_CONFIG["timezone"],
        "scheduled":    jobs,
        "themes_count": len(THEMES),
        "next_theme":   THEMES[theme_index % len(THEMES)]
    }


@app.get("/test-engine")
async def test_engine(theme: str = "HTML basics for beginners"):
    try:
        result = await run_engine(theme)
        return {
            "status":  "success",
            "caption": result['caption'],
            "video":   result['video_path']
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))