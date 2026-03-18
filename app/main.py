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

# QStash is optional — only used if keys are present
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

    # ── 1. Resolve theme ──────────────────────────────────────────────────
    if theme:
        resolved_theme = theme
    else:
        article = get_top_sports_story(
            prefer_match_end=is_realtime,
            story_slot=story_slot,
        )
        if not article:
            mode = "realtime match-end" if is_realtime else f"slot {story_slot}"
            print(f"[AGENT] ℹ️  No fresh story ({mode}) — standing by.", flush=True)
            return

        resolved_theme          = build_sports_theme(article)
        JOB_STATE["last_score"] = article.get("relevance_score", 0)
        mark_as_posted(article["url"])

    JOB_STATE["last_theme"] = resolved_theme
    JOB_STATE["last_type"]  = "realtime_sports" if is_realtime else "sports"

    # ── 2. Engine + post ──────────────────────────────────────────────────
    print(f"[AGENT] 🚀 Engine: {resolved_theme[:80]}", flush=True)
    result = await run_engine(resolved_theme)

    print("[AGENT] 📲 Posting to Instagram...", flush=True)
    post_id = post_reel_full_pipeline(
        video_path=result["video_path"],
        caption=result["caption"],
    )
    print(f"[AGENT] ✅ Posted! ID: {post_id}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
#  KEEP-ALIVE
# ═══════════════════════════════════════════════════════════════════════════

def keep_alive_ping():
    try:
        url = os.getenv("RENDER_APP_URL", "http://localhost:8000").rstrip("/")
        req.get(f"{url}/health", timeout=10)
        print("[AGENT] 💓 Keep-alive ping", flush=True)
    except Exception as e:
        print(f"[AGENT] ⚠️  Keep-alive failed: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    auto_refresh_if_needed()

    if scheduler.running:
        print("[AGENT] Scheduler already running", flush=True)
        return

    tz_name    = AGENT_CONFIG["timezone"]
    post_times = AGENT_CONFIG["post_times"]

    # ── Scheduled post slots ───────────────────────────────────────────────
    for pt in post_times:
        slot = pt.get("story_slot", 1)
        scheduler.add_job(
            run_post_cycle_sync,
            # ← KEY FIX: pass timezone so AWS UTC doesn't shift your IST schedule
            CronTrigger(hour=pt["hour"], minute=pt["minute"], timezone=tz_name),
            kwargs={"story_slot": slot},
            id=pt["label"],
            name=f"[sports slot={slot}] {pt['hour']:02d}:{pt['minute']:02d} IST",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
        )
        print(
            f"[AGENT] ✅ Scheduled: {pt['label']} at "
            f"{pt['hour']:02d}:{pt['minute']:02d} {tz_name}",
            flush=True,
        )

    # ── Real-time match watcher every 15 mins ─────────────────────────────
    scheduler.add_job(
        run_post_cycle_sync,
        IntervalTrigger(minutes=15, timezone=tz_name),
        kwargs={"is_realtime": True},
        id="watcher",
        name="Real-Time Match Watcher",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    print("[AGENT] ✅ Real-time watcher: every 15 mins", flush=True)

    # ── Keep-alive ping every 14 mins ─────────────────────────────────────
    scheduler.add_job(
        keep_alive_ping,
        CronTrigger(minute="*/14", timezone=tz_name),
        id="keep_alive",
        name="Keep Alive",
        replace_existing=True,
    )

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())

    print(f"[AGENT] ⚽ Sports Agent v4 Live | {len(ALL_FEEDS)} feeds | {len(post_times)} slots", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
#  ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    return {
        "service":          "Instagram Sports AI Agent",
        "version":          "4.0.0",
        "status":           "running",
        "endpoints": {
            "/health":          "Health check + scheduler status",
            "/sports-preview":  "Preview next sports story",
            "/scores-preview":  "Top 10 scored articles",
            "/schedule-status": "All scheduled jobs",
            "/job-status":      "Current job state",
            "/post-now":        "Trigger immediate post (POST)",
            "/schedule-test":   "Schedule test post (POST)",
            "/run-engine":      "QStash-secured engine trigger (POST)",
        },
        "feeds_active":     len(ALL_FEEDS),
        "scheduler_running": scheduler.running,
        "last_run":         JOB_STATE.get("last_end"),
        "current_state":    JOB_STATE.get("running", False),
    }


@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request):
    return {
        "status": "healthy",
        "scheduler": {
            "running":     scheduler.running,
            "jobs":        len(scheduler.get_jobs()),
            "job_details": [
                {
                    "id":       job.id,
                    "name":     job.name,
                    "next_run": str(job.next_run_time) if job.next_run_time else None,
                }
                for job in scheduler.get_jobs()
            ],
        },
        "env_checks": {
            "instagram_token": bool(os.getenv("INSTAGRAM_ACCESS_TOKEN")),
            "openrouter_key":  bool(os.getenv("OPENROUTER_API_KEY")),
            "hf_key":          bool(os.getenv("HF_API_KEY")),
            "nvidia_key":      bool(os.getenv("NVIDIA_API_KEY")),
            "cloudinary":      bool(os.getenv("CLOUDINARY_CLOUD_NAME")),
        },
        "agent_state": JOB_STATE,
        "timestamp":   datetime.now(TZ).isoformat(),
    }


@app.get("/time-now")
async def time_now():
    return {
        "timezone": AGENT_CONFIG["timezone"],
        "now_ist":  datetime.now(TZ).isoformat(),
        "now_utc":  datetime.utcnow().isoformat() + "Z",
    }


@app.get("/job-status")
async def job_status():
    return JOB_STATE


@app.get("/schedule-status")
async def schedule_status():
    jobs = [
        {
            "id":       j.id,
            "name":     j.name,
            "next_run": str(j.next_run_time),
        }
        for j in scheduler.get_jobs()
    ]
    return {
        "status":       "running" if scheduler.running else "stopped",
        "timezone":     AGENT_CONFIG["timezone"],
        "scheduled":    jobs,
        "rss_feeds":    len(ALL_FEEDS),
    }


@app.get("/sports-preview")
async def sports_preview(story_slot: int = 1, match_end_only: bool = False):
    """Preview what the next sports post would pick — no posting."""
    article = get_top_sports_story(
        prefer_match_end=match_end_only,
        story_slot=story_slot,
    )
    if not article:
        return {
            "status":  "no_news",
            "message": "No qualifying story found (or all on cooldown).",
        }
    return {
        "status":          "found",
        "story_slot":      story_slot,
        "relevance_score": article.get("relevance_score", 0),
        "title":           article["title"],
        "summary":         article.get("summary", "")[:300],
        "source":          article["source"],
        "category":        article["category"],
        "region":          article.get("region", ""),
        "is_match_end":    article.get("is_match_end", False),
        "pub_date":        str(article.get("pub_date", "")),
        "image_url":       article.get("image_url", ""),
        "url":             article["url"],
    }


@app.get("/scores-preview")
async def scores_preview():
    """Top 10 scored articles right now — for debugging the scorer."""
    articles = fetch_all_sports_news(max_age_hours=24)
    return {
        "total_fetched": len(articles),
        "top_10": [
            {
                "rank":         i + 1,
                "score":        a.get("relevance_score", 0),
                "title":        a["title"][:80],
                "source":       a["source"],
                "category":     a["category"],
                "region":       a.get("region", ""),
                "is_match_end": a.get("is_match_end", False),
                "pub_date":     str(a.get("pub_date", "")),
            }
            for i, a in enumerate(articles[:10])
        ],
    }


@app.api_route("/post-now", methods=["GET", "POST"])
async def post_now(
    background_tasks: BackgroundTasks,
    story_slot: int = 1,
    theme: str | None = None,
):
    """Trigger an immediate post. Accessible via GET or POST for easy browser testing."""
    if JOB_STATE["running"]:
        return {"status": "busy", "message": "A job is already running."}

    background_tasks.add_task(
        run_post_cycle_sync,
        theme=theme,
        story_slot=story_slot,
        is_realtime=False,
    )
    return {
        "status":     "accepted",
        "story_slot": story_slot,
        "theme":      theme or "auto",
        "message":    "Post starting in background — poll /job-status to track.",
    }


@app.api_route("/schedule-test", methods=["GET", "POST"])
async def schedule_test(
    background_tasks: BackgroundTasks,
    minutes: int = 1,
    story_slot: int = 1,
    theme: str | None = None,
):
    """Schedule a one-off test post N minutes from now."""
    tz     = TZ
    run_at = datetime.now(tz) + timedelta(minutes=minutes)
    job_id = f"test_{int(run_at.timestamp())}"

    scheduler.add_job(
        run_post_cycle_sync,
        trigger="date",
        run_date=run_at,
        id=job_id,
        name=f"TEST slot={story_slot} at {run_at.strftime('%H:%M')} IST",
        replace_existing=True,
        kwargs={"theme": theme, "story_slot": story_slot},
    )
    return {
        "status":     "scheduled",
        "job_id":     job_id,
        "run_at_ist": run_at.isoformat(),
        "story_slot": story_slot,
        "theme":      theme or "auto",
    }


@app.post("/run-engine")
async def trigger_engine(request: Request, background_tasks: BackgroundTasks):
    """
    QStash-secured endpoint for automated triggers.
    Signature verification only enforced when QSTASH keys are set.
    """
    body_bytes = await request.body()
    body_str   = body_bytes.decode("utf-8")
    signature  = request.headers.get("upstash-signature", "")

    use_qstash = bool(
        os.getenv("QSTASH_CURRENT_SIGNING_KEY")
        and os.getenv("QSTASH_NEXT_SIGNING_KEY")
        and _qstash_receiver
    )

    if use_qstash:
        if not signature:
            raise HTTPException(status_code=401, detail="Missing QStash signature")
        try:
            _qstash_receiver.verify(body=body_str, signature=signature)
        except Exception as e:
            print(f"[SECURITY] QStash verification failed: {e}", flush=True)
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        print("[DEBUG] QStash verification skipped (keys not configured)", flush=True)

    background_tasks.add_task(run_post_cycle_sync, story_slot=1)
    return {"status": "accepted", "message": "Engine starting in background."}