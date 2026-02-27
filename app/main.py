import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))

import requests as req
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import pytz
import atexit

from engine import run_engine
from social import post_reel_full_pipeline
from token_manager import auto_refresh_if_needed
from config import AGENT_CONFIG

load_dotenv()

app = FastAPI(title="Instagram AI Agent", version="1.0.0")

scheduler   = BackgroundScheduler(timezone="Asia/Kolkata")
THEMES      = AGENT_CONFIG["themes"]
theme_index = 0


async def run_post_cycle():
    global theme_index
    theme       = THEMES[theme_index % len(THEMES)]
    theme_index += 1
    print(f"\n[AGENT] 🚀 Starting cycle → Theme: {theme}", flush=True)
    try:
        print("[AGENT] Step 1: Running engine...", flush=True)
        result = await run_engine(theme)
        print(f"[AGENT] Step 2: Posting to Instagram...", flush=True)
        post_id = post_reel_full_pipeline(
            video_path=result['video_path'],
            caption=result['caption']
        )
        print(f"[AGENT] ✅ Posted! ID: {post_id}", flush=True)
    except Exception as e:
        import traceback
        print(f"[AGENT] ❌ Failed: {e}", flush=True)
        traceback.print_exc()


def run_post_cycle_sync():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_post_cycle())
    finally:
        loop.close()


def keep_alive_ping():
    try:
        app_url = os.getenv("RENDER_APP_URL", "http://localhost:8000")
        req.get(f"{app_url}/health", timeout=10)
        print("[AGENT] 💓 Keep-alive ping sent", flush=True)
    except Exception as e:
        print(f"[AGENT] ⚠️  Keep-alive failed: {e}", flush=True)


@app.on_event("startup")
async def startup():
    auto_refresh_if_needed()

    post_times = AGENT_CONFIG["post_times"]
    tz_name    = AGENT_CONFIG["timezone"]
    tz         = pytz.timezone(tz_name)
    now        = datetime.now(tz)

    for pt in post_times:
        scheduler.add_job(
            run_post_cycle_sync,
            CronTrigger(hour=pt["hour"], minute=pt["minute"], timezone=tz_name),
            id=pt["label"],
            name=f"Post at {pt['hour']:02d}:{pt['minute']:02d}"
        )
        print(f"[AGENT] ✅ Scheduled: {pt['label']} at {pt['hour']:02d}:{pt['minute']:02d} {tz_name}", flush=True)

        # Missed post detection — fires if restarted within 30 min of scheduled time
        scheduled_time = now.replace(hour=pt["hour"], minute=pt["minute"], second=0, microsecond=0)
        time_diff      = (now - scheduled_time).total_seconds()

        if 0 < time_diff < 1800:
            print(f"[AGENT] ⚠️  Missed {pt['label']} by {int(time_diff/60)} mins — firing now!", flush=True)
            scheduler.add_job(
                run_post_cycle_sync,
                'date',
                run_date=datetime.now(tz),
                id=f"{pt['label']}_catchup",
                name=f"Catchup {pt['label']}"
            )

    scheduler.add_job(
        keep_alive_ping,
        CronTrigger(minute="*/14"),
        id="keep_alive",
        name="Keep Alive Ping"
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    print(f"[AGENT] ✅ Agent live — {len(THEMES)} themes loaded", flush=True)


@app.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    return JSONResponse({"status": "running", "agent": "Instagram AI Agent - Web Dev"})


@app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
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


@app.get("/post-debug")
async def post_debug(theme: str = "HTML basics for beginners"):
    global theme_index
    print(f"[DEBUG] Starting: {theme}", flush=True)
    try:
        result  = await run_engine(theme)
        post_id = post_reel_full_pipeline(
            video_path=result['video_path'],
            caption=result['caption']
        )
        print(f"[DEBUG] ✅ Post ID: {post_id}", flush=True)
        return {"status": "success", "post_id": post_id, "caption": result['caption']}
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[DEBUG] ❌ {tb}", flush=True)
        raise HTTPException(status_code=500, detail=tb)


@app.get("/schedule-status")
def schedule_status():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({"id": job.id, "name": job.name, "next_run": str(job.next_run_time)})
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
        return {"status": "success", "caption": result['caption'], "video": result['video_path']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))