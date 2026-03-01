import asyncio
import os
import sys
import atexit
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests as req
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger  # ← NEW: for 15-min watcher

from app.engine import run_engine
from app.social import post_reel_full_pipeline
from app.token_manager import auto_refresh_if_needed
from app.config import AGENT_CONFIG
from app.sports_fetcher import (
    get_top_sports_story,
    build_sports_theme,
    is_sports_theme,
    mark_as_posted,          # ← NEW: marks article after posting
    fetch_all_sports_news,   # ← NEW: used by real-time watcher
    ALL_FEEDS,               # ← NEW: used for startup log count
)

sys.path.insert(0, os.path.dirname(__file__))
load_dotenv()

app       = FastAPI(title="Instagram AI Agent", version="3.0.0")
scheduler = BackgroundScheduler(timezone=AGENT_CONFIG["timezone"])

# ── Theme rotation ─────────────────────────────────────────────────────────
THEME_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'theme_index.txt')
THEMES     = AGENT_CONFIG["themes"]

def get_theme_index() -> int:
    try:
        with open(THEME_FILE, 'r') as f:
            return int(f.read().strip())
    except Exception:
        return 0

def save_theme_index(index: int):
    os.makedirs(os.path.dirname(THEME_FILE), exist_ok=True)
    with open(THEME_FILE, 'w') as f:
        f.write(str(index))

theme_index = get_theme_index()  # loaded from disk at boot


# ═══════════════════════════════════════════════════════════════════════════
#  JOB STATE
# ═══════════════════════════════════════════════════════════════════════════

JOB_STATE = {
    "running":    False,
    "last_start": None,
    "last_end":   None,
    "last_error": None,
    "last_type":  None,    # "webdev" | "sports" | "realtime_sports"
    "last_theme": None,
    "last_score": None,    # relevance score of last sports article
}


# ═══════════════════════════════════════════════════════════════════════════
#  CORE CYCLE
# ═══════════════════════════════════════════════════════════════════════════

def run_post_cycle_sync(
    theme: str | None = None,
    content_type: str = "webdev",
    story_slot: int = 1,
):
    """
    APScheduler-safe sync wrapper.
    story_slot: 1 = highest scored story (2PM), 2 = second highest (8PM)
    """
    tz = ZoneInfo(AGENT_CONFIG["timezone"])

    if JOB_STATE["running"]:
        print("[AGENT] ⛔ Job already running — skipping", flush=True)
        return

    JOB_STATE["running"]    = True
    JOB_STATE["last_start"] = datetime.now(tz).isoformat()
    JOB_STATE["last_end"]   = None
    JOB_STATE["last_error"] = None
    JOB_STATE["last_type"]  = content_type

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            run_post_cycle(theme=theme, content_type=content_type, story_slot=story_slot)
        )
        JOB_STATE["last_end"] = datetime.now(tz).isoformat()
    except Exception as e:
        JOB_STATE["last_error"] = str(e)
        JOB_STATE["last_end"]   = datetime.now(tz).isoformat()
        raise
    finally:
        JOB_STATE["running"] = False
        loop.close()


async def run_post_cycle(
    theme: str | None = None,
    content_type: str = "webdev",
    story_slot: int = 1,
):
    """
    Async pipeline runner.

    content_type:
      "webdev"          → rotating THEMES list
      "sports"          → fetch best story by story_slot rank
      "realtime_sports" → only post if a high-score match result exists
    """
    global theme_index

    # ── Resolve theme ──────────────────────────────────────────────────────
    if theme is not None:
        # Manual override from /post-now endpoint
        resolved_theme = theme
        if is_sports_theme(resolved_theme):
            content_type = "sports"

    elif content_type == "realtime_sports":
        # Real-time watcher path — only fires if match ended + score >= threshold
        article = get_top_sports_story(prefer_match_end=True)
        if article is None:
            # No qualifying result yet — watcher will retry in 15 mins
            print("[AGENT] ℹ️  Match watcher: no new result — standing by.", flush=True)
            return
        resolved_theme             = build_sports_theme(article)
        JOB_STATE["last_score"]    = article.get("relevance_score", 0)
        mark_as_posted(article["url"])

    elif content_type == "sports":
        # Scheduled sports slot — pick Nth ranked story
        article = get_top_sports_story(story_slot=story_slot)
        if article is None:
            # Fallback to web dev if no sports news available
            print("[AGENT] ⚠️  No sports news — falling back to web dev.", flush=True)
            resolved_theme = THEMES[theme_index % len(THEMES)]
            theme_index   += 1
            save_theme_index(theme_index)
            content_type   = "webdev"
        else:
            resolved_theme          = build_sports_theme(article)
            JOB_STATE["last_score"] = article.get("relevance_score", 0)
            mark_as_posted(article["url"])

    else:
        # Web dev rotation
        resolved_theme = THEMES[theme_index % len(THEMES)]
        theme_index   += 1
        save_theme_index(theme_index)

    JOB_STATE["last_theme"] = resolved_theme

    print(
        f"\n[AGENT] 🚀 Posting | type={content_type} | slot={story_slot} | "
        f"theme={resolved_theme[:90]}",
        flush=True,
    )

    # ── Run engine + post ──────────────────────────────────────────────────
    try:
        print("[AGENT] Step 1: Running engine...", flush=True)
        result = await run_engine(resolved_theme)

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


# ═══════════════════════════════════════════════════════════════════════════
#  REAL-TIME MATCH WATCHER
# ═══════════════════════════════════════════════════════════════════════════

def match_watcher_job():
    """
    Runs every 15 minutes silently.
    Only triggers a post if a fresh match-end article scores above threshold.
    Has its own JOB_STATE guard — won't overlap with a running scheduled post.
    """
    print("[WATCHER] 🔍 Checking for match results...", flush=True)
    run_post_cycle_sync(content_type="realtime_sports")


# ═══════════════════════════════════════════════════════════════════════════
#  KEEP-ALIVE
# ═══════════════════════════════════════════════════════════════════════════

def keep_alive_ping():
    try:
        app_url = os.getenv("RENDER_APP_URL", "http://localhost:8000").rstrip("/")
        req.get(f"{app_url}/health", timeout=10)
        print("[AGENT] 💓 Keep-alive ping", flush=True)
    except Exception as e:
        print(f"[AGENT] ⚠️  Keep-alive failed: {e}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
#  SCHEDULING
# ═══════════════════════════════════════════════════════════════════════════

def schedule_all_jobs():
    tz_name    = AGENT_CONFIG["timezone"]
    post_times = AGENT_CONFIG["post_times"]

    # ── Daily post slots from config ───────────────────────────────────────
    for pt in post_times:
        ctype      = pt.get("content_type", "webdev")
        slot       = pt.get("story_slot", 1)           # which ranked story to use
        job_kwargs = {"content_type": ctype, "story_slot": slot}

        scheduler.add_job(
            run_post_cycle_sync,
            CronTrigger(hour=pt["hour"], minute=pt["minute"], timezone=tz_name),
            id=pt["label"],
            name=f"[{ctype.upper()} slot={slot}] {pt['hour']:02d}:{pt['minute']:02d}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=1800,
            kwargs=job_kwargs,
        )
        print(
            f"[AGENT] ✅ Scheduled [{ctype} story_slot={slot}]: "
            f"{pt['label']} at {pt['hour']:02d}:{pt['minute']:02d} {tz_name}",
            flush=True,
        )

    # ── Real-time match watcher — every 15 minutes ─────────────────────────
    scheduler.add_job(
        match_watcher_job,
        IntervalTrigger(minutes=15, timezone=tz_name),
        id="match_watcher",
        name="Real-Time Match Watcher (15 min)",
        replace_existing=True,
        max_instances=1,       # never run two watchers simultaneously
        coalesce=True,
        misfire_grace_time=300,
    )
    print("[AGENT] ✅ Real-time match watcher: every 15 mins", flush=True)

    # ── Keep-alive ping every 14 minutes ──────────────────────────────────
    scheduler.add_job(
        keep_alive_ping,
        CronTrigger(minute="*/14", timezone=tz_name),
        id="keep_alive",
        name="Keep Alive Ping",
        replace_existing=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  STARTUP
# ═══════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def startup():
    auto_refresh_if_needed()

    if scheduler.running:
        print("[AGENT] Scheduler already running — skipping init", flush=True)
        return

    try:
        schedule_all_jobs()
        scheduler.start()
        atexit.register(lambda: scheduler.shutdown())

        # Startup summary
        sports_slots = [pt for pt in AGENT_CONFIG["post_times"] if pt.get("content_type") == "sports"]
        webdev_slots = [pt for pt in AGENT_CONFIG["post_times"] if pt.get("content_type") == "webdev"]

        print(f"[AGENT] ✅ Agent v3 live", flush=True)
        print(f"[AGENT] 📚 Web dev: {len(webdev_slots)} slot(s), {len(THEMES)} themes", flush=True)
        print(f"[AGENT] ⚽ Sports:  {len(sports_slots)} slot(s), {len(ALL_FEEDS)} RSS feeds", flush=True)
        print(f"[AGENT] ⚡ Real-time match watcher: every 15 mins", flush=True)

    except Exception as e:
        import traceback
        print(f"[AGENT] ❌ Startup failed: {e}", flush=True)
        traceback.print_exc()
        raise


# ═══════════════════════════════════════════════════════════════════════════
#  API ROUTES
# ═══════════════════════════════════════════════════════════════════════════

@app.api_route("/", methods=["GET", "HEAD"])
async def root(request: Request):
    return JSONResponse({"status": "running", "agent": "Instagram AI Agent v3"})


@app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    checks = {
        "instagram_user_id": bool(os.getenv("INSTAGRAM_USER_ID")),
        "instagram_token":   bool(os.getenv("INSTAGRAM_ACCESS_TOKEN")),
        "openrouter_key":    bool(os.getenv("OPENROUTER_API_KEY")),
        "hf_key":            bool(os.getenv("HF_API_KEY")),
        "cloudinary":        bool(os.getenv("CLOUDINARY_CLOUD_NAME")),
        "nvidia_key":        bool(os.getenv("NVIDIA_API_KEY")),
    }
    all_ok = all(checks.values())
    return JSONResponse(
        content={"status": "healthy" if all_ok else "missing env vars", "checks": checks},
        status_code=200 if all_ok else 500,
    )


@app.get("/time-now")
def time_now():
    tz  = ZoneInfo(AGENT_CONFIG["timezone"])
    now = datetime.now(tz)
    return {"timezone": AGENT_CONFIG["timezone"], "now": now.isoformat()}


@app.post("/post-now")
def post_now(
    theme: str | None = None,
    content_type: str = "webdev",
    story_slot: int = 1,
):
    """
    Manually trigger an immediate post.
    content_type: "webdev" | "sports" | "realtime_sports"
    story_slot:   1 = top story, 2 = second story (sports only)
    theme:        optional manual theme override
    """
    tz     = ZoneInfo(AGENT_CONFIG["timezone"])
    run_at = datetime.now(tz) + timedelta(seconds=1)
    job_id = f"post_now_{int(run_at.timestamp())}"

    scheduler.add_job(
        run_post_cycle_sync,
        trigger="date",
        run_date=run_at,
        id=job_id,
        name="POST NOW (manual)",
        replace_existing=True,
        kwargs={"theme": theme, "content_type": content_type, "story_slot": story_slot},
    )
    return {
        "status":       "scheduled",
        "job_id":       job_id,
        "run_at":       run_at.isoformat(),
        "content_type": content_type,
        "story_slot":   story_slot,
        "theme":        theme or "auto",
    }


@app.get("/schedule-status")
def schedule_status():
    jobs = [
        {"id": j.id, "name": j.name, "next_run": str(j.next_run_time)}
        for j in scheduler.get_jobs()
    ]
    return {
        "status":            "running" if scheduler.running else "stopped",
        "timezone":          AGENT_CONFIG["timezone"],
        "scheduled_jobs":    jobs,
        "webdev_themes":     len(THEMES),
        "next_webdev_theme": THEMES[theme_index % len(THEMES)],
        "rss_feeds":         len(ALL_FEEDS),
    }


@app.post("/schedule-test")
def schedule_test(
    minutes: int = 2,
    theme: str | None = None,
    content_type: str = "webdev",
    story_slot: int = 1,
):
    """Schedule a one-off test post N minutes from now."""
    tz     = ZoneInfo(AGENT_CONFIG["timezone"])
    run_at = (datetime.now(tz) + timedelta(minutes=minutes)).replace(second=0, microsecond=0)
    job_id = f"test_once_{int(run_at.timestamp())}"

    scheduler.add_job(
        run_post_cycle_sync,
        trigger="date",
        run_date=run_at,
        id=job_id,
        name=f"TEST [{content_type} slot={story_slot}] at {run_at.isoformat()}",
        replace_existing=True,
        kwargs={"theme": theme, "content_type": content_type, "story_slot": story_slot},
    )
    return {
        "status":       "scheduled",
        "job_id":       job_id,
        "run_at":       run_at.isoformat(),
        "content_type": content_type,
        "story_slot":   story_slot,
        "theme":        theme or "auto",
    }


@app.get("/test-engine")
async def test_engine(theme: str = "HTML basics for beginners"):
    """Test the engine pipeline directly without posting."""
    try:
        result = await run_engine(theme)
        return {
            "status":  "success",
            "caption": result["caption"],
            "video":   result["video_path"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/sports-preview")
def sports_preview(
    story_slot: int = 1,
    match_end_only: bool = False,
):
    """
    Preview what the next sports post would be — no posting.

    story_slot=1    → top ranked story (as posted at 2PM)
    story_slot=2    → second ranked story (as posted at 8PM)
    match_end_only  → simulate real-time watcher (only returns if match ended)
    """
    article = get_top_sports_story(
        prefer_match_end=match_end_only,
        story_slot=story_slot,
    )
    if not article:
        return {
            "status":  "no_news",
            "message": "No qualifying sports story found (or all on cooldown).",
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
def scores_preview():
    """
    Shows the top 10 scored articles right now — useful for debugging
    what the agent considers most important at this moment.
    """
    articles = fetch_all_sports_news(max_age_hours=24)
    return {
        "total_fetched": len(articles),
        "top_10": [
            {
                "rank":            i + 1,
                "score":           a.get("relevance_score", 0),
                "title":           a["title"][:80],
                "source":          a["source"],
                "category":        a["category"],
                "region":          a.get("region", ""),
                "is_match_end":    a.get("is_match_end", False),
                "pub_date":        str(a.get("pub_date", "")),
            }
            for i, a in enumerate(articles[:10])
        ]
    }


@app.get("/job-status")
def job_status():
    return JOB_STATE