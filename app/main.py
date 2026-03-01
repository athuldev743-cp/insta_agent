import asyncio
import os
import sys
import atexit
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests as req
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.engine import run_engine
from app.social import post_reel_full_pipeline
from app.token_manager import auto_refresh_if_needed
from app.config import AGENT_CONFIG

# Ensure local imports work
sys.path.insert(0, os.path.dirname(__file__))

load_dotenv()

app = FastAPI(title="Instagram AI Agent", version="1.0.0")

# ✅ Single global scheduler
scheduler = BackgroundScheduler(timezone=AGENT_CONFIG["timezone"])


THEME_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'theme_index.txt')

def get_theme_index() -> int:
    try:
        with open(THEME_FILE, 'r') as f:
            return int(f.read().strip())
    except:
        return 0

def save_theme_index(index: int):
    os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
    with open(THEME_FILE, 'w') as f:
        f.write(str(index))



THEMES = AGENT_CONFIG["themes"]
theme_index = 0

# ---- JOB STATE (keep) ----
JOB_STATE = {
    "running": False,
    "last_start": None,
    "last_end": None,
    "last_error": None,
}

def run_post_cycle_sync(theme: str | None = None):
    """APScheduler-safe sync wrapper that also updates JOB_STATE."""
    tz = ZoneInfo(AGENT_CONFIG["timezone"])

    if JOB_STATE["running"]:
        print("[AGENT] ⛔ Job already running, skipping", flush=True)
        return

    JOB_STATE["running"] = True
    JOB_STATE["last_start"] = datetime.now(tz).isoformat()
    JOB_STATE["last_end"] = None
    JOB_STATE["last_error"] = None

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_post_cycle(theme))
        JOB_STATE["last_end"] = datetime.now(tz).isoformat()
    except Exception as e:
        JOB_STATE["last_error"] = str(e)
        JOB_STATE["last_end"] = datetime.now(tz).isoformat()
        raise
    finally:
        JOB_STATE["running"] = False
        loop.close()



async def run_post_cycle(theme: str | None = None):
    """Async pipeline runner."""
    global theme_index
    if theme is None:
        theme = THEMES[theme_index % len(THEMES)]
        theme_index += 1
        save_theme_index(theme_index)

    print(f"\n[AGENT] 🚀 Starting cycle → Theme: {theme}", flush=True)
    try:
        print("[AGENT] Step 1: Running engine...", flush=True)
        result = await run_engine(theme)

        print("[AGENT] Step 2: Posting to Instagram...", flush=True)
        post_id = post_reel_full_pipeline(
            video_path=result["video_path"],
            caption=result["caption"],
        )

        print(f"[AGENT] ✅ Posted! ID: {post_id}", flush=True)
        return post_id

    except Exception as e:
        import traceback
        print(f"[AGENT] ❌ Failed: {e}", flush=True)
        traceback.print_exc()
        raise




def keep_alive_ping():
    """
    NOTE: Self-ping does NOT prevent Render sleep.
    It only helps confirm the process is alive when it's already running.
    """
    try:
        app_url = os.getenv("RENDER_APP_URL", "http://localhost:8000").rstrip("/")
        req.get(f"{app_url}/health", timeout=10)
        print("[AGENT] 💓 Keep-alive ping sent", flush=True)
    except Exception as e:
        print(f"[AGENT] ⚠️ Keep-alive failed: {e}", flush=True)


def schedule_daily_jobs():
    """Schedules cron jobs from config (id-safe)."""
    tz_name = AGENT_CONFIG["timezone"]
    post_times = AGENT_CONFIG["post_times"]

    for pt in post_times:
        scheduler.add_job(
            run_post_cycle_sync,
            CronTrigger(hour=pt["hour"], minute=pt["minute"], timezone=tz_name),
            id=pt["label"],
            name=f"Cron {pt['label']} {pt['hour']:02d}:{pt['minute']:02d} {tz_name}",
            replace_existing=True,     # ✅ prevents ConflictingIdError on restarts
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,   # 30 min
        )
        print(
            f"[AGENT] ✅ Scheduled cron: {pt['label']} at "
            f"{pt['hour']:02d}:{pt['minute']:02d} {tz_name}",
            flush=True
        )





@app.on_event("startup")
async def startup():
    # Refresh token once at boot (your function)
    auto_refresh_if_needed()

    # Avoid double-start in weird server setups
    if scheduler.running:
        print("[AGENT] Scheduler already running; skipping init", flush=True)
        return

    try:
        schedule_daily_jobs()

        # ✅ your requested test: run at 11:40 (given now ~11:35)
        

        # Keep alive job (optional)
        scheduler.add_job(
            keep_alive_ping,
            CronTrigger(minute="*/14", timezone=AGENT_CONFIG["timezone"]),
            id="keep_alive",
            name="Keep Alive Ping",
            replace_existing=True,
        )

        scheduler.start()
        atexit.register(lambda: scheduler.shutdown())

        print(f"[AGENT] ✅ Agent live — {len(THEMES)} themes loaded", flush=True)

    except Exception as e:
        import traceback
        print(f"[AGENT] ❌ Startup scheduling failed: {e}", flush=True)
        traceback.print_exc()
        # Don't raise if you still want API to boot; but for debugging it's better to raise:
        raise


@app.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    return JSONResponse({"status": "running", "agent": "Instagram AI Agent"})


@app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    checks = {
        "instagram_user_id": bool(os.getenv("INSTAGRAM_USER_ID")),
        "instagram_token": bool(os.getenv("INSTAGRAM_ACCESS_TOKEN")),
        "openrouter_key": bool(os.getenv("OPENROUTER_API_KEY")),
        "hf_key": bool(os.getenv("HF_API_KEY")),
        "cloudinary": bool(os.getenv("CLOUDINARY_CLOUD_NAME")),
    }
    all_ok = all(checks.values())
    return JSONResponse(
        content={"status": "healthy" if all_ok else "missing env vars", "checks": checks},
        status_code=200 if all_ok else 500,
    )

@app.get("/time-now")
def time_now():
    tz = ZoneInfo(AGENT_CONFIG["timezone"])
    now = datetime.now(tz)
    return {"timezone": AGENT_CONFIG["timezone"], "now": now.isoformat()}

@app.post("/post-now")
def post_now(theme: str | None = None):
    # run in scheduler thread via a one-off job so it uses JOB_STATE wrapper
    tz = ZoneInfo(AGENT_CONFIG["timezone"])
    run_at = datetime.now(tz) + timedelta(seconds=1)

    job_id = f"post_now_{int(run_at.timestamp())}"
    scheduler.add_job(
        run_post_cycle_sync,
        trigger="date",
        run_date=run_at,
        id=job_id,
        name="POST NOW",
        replace_existing=True,
        kwargs={"theme": theme},
    )
    return {"status": "scheduled", "job_id": job_id, "run_at": run_at.isoformat(), "theme": theme or "auto"}


@app.get("/schedule-status")
def schedule_status():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {"id": job.id, "name": job.name, "next_run": str(job.next_run_time)}
        )
    return {
        "status": "running" if scheduler.running else "stopped",
        "timezone": AGENT_CONFIG["timezone"],
        "scheduled": jobs,
        "themes_count": len(THEMES),
        "next_theme": THEMES[theme_index % len(THEMES)],
    }


@app.post("/schedule-test")
def schedule_test(minutes: int = 2, theme: str | None = None):
    tz = ZoneInfo(AGENT_CONFIG["timezone"])
    run_at = datetime.now(tz) + timedelta(minutes=minutes)
    run_at = run_at.replace(second=0, microsecond=0)

    job_id = f"test_once_{int(run_at.timestamp())}"
    scheduler.add_job(
        run_post_cycle_sync,
        trigger="date",
        run_date=run_at,
        id=job_id,
        name=f"TEST once at {run_at.isoformat()}",
        replace_existing=True,
        kwargs={"theme": theme},
    )
    return {"status": "scheduled", "job_id": job_id, "run_at": run_at.isoformat(), "theme": theme or "auto"}


@app.get("/test-engine")
async def test_engine(theme: str = "HTML basics for beginners"):
    try:
        result = await run_engine(theme)
        return {"status": "success", "caption": result["caption"], "video": result["video_path"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    




@app.get("/job-status")
def job_status():
    return JOB_STATE    