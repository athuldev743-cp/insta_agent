import asyncio
import os
import sys
sys.path.insert(0, os.path.dirname(__file__))
from token_manager import auto_refresh_if_needed
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import atexit

from engine import run_engine
from social import post_reel_full_pipeline

load_dotenv()

app = FastAPI(
    title="Instagram AI Agent",
    version="1.0.0"
)

scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

THEMES = [
    "Morning motivation and productivity",
    "Technology and the future of AI",
    "Nature and mindfulness",
    "Success mindset and hustle",
    "Night time reflection and gratitude",
]
theme_index = 0


def run_post_cycle():
    global theme_index
    theme = THEMES[theme_index % len(THEMES)]
    theme_index += 1
    print(f"\n[AGENT] Starting cycle → Theme: {theme}")
    
    try:
        result = asyncio.run(run_engine(theme))
        post_id = post_reel_full_pipeline(
            video_path=result['video_path'],
            caption=result['caption']
        )
        print(f"[AGENT] ✅ Posted! ID: {post_id}")
    except Exception as e:
        print(f"[AGENT] ❌ Failed: {e}")


@app.on_event("startup")
async def startup():
    auto_refresh_if_needed()        # ✅ 1. Check/refresh token FIRST
    
    scheduler.add_job(run_post_cycle, CronTrigger(hour=9,  minute=0, timezone="Asia/Kolkata"), id="morning")
    scheduler.add_job(run_post_cycle, CronTrigger(hour=21, minute=0, timezone="Asia/Kolkata"), id="evening")
    scheduler.start()               # ✅ 2. Then start scheduler
    
    atexit.register(lambda: scheduler.shutdown())
    print("[AGENT] ✅ Scheduler live — posting at 9AM & 9PM IST")


@app.get("/")
def root():
    return {"status": "running", "agent": "Instagram AI Agent"}


@app.get("/health")
def health():
    checks = {
        "instagram_user_id":    bool(os.getenv("INSTAGRAM_USER_ID")),
        "instagram_token":      bool(os.getenv("INSTAGRAM_ACCESS_TOKEN")),
        "gemini_api_key":       bool(os.getenv("GEMINI_API_KEY")),
        "cloudinary":           bool(os.getenv("CLOUDINARY_CLOUD_NAME")),
    }
    all_ok = all(checks.values())
    return JSONResponse(
        content={"status": "healthy" if all_ok else "missing env vars", "checks": checks},
        status_code=200 if all_ok else 500
    )


@app.post("/post-now")
async def post_now(background_tasks: BackgroundTasks, theme: str = "Daily motivation"):
    background_tasks.add_task(run_post_cycle)
    return {"status": "started", "theme": theme}


@app.get("/test-engine")
async def test_engine(theme: str = "AI and the future"):
    try:
        result = await run_engine(theme)
        return {"status": "success", "caption": result['caption'], "video": result['video_path']}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))