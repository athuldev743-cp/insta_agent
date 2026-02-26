import asyncio
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import atexit

# Import your pipeline
from engine import run_engine
from social import post_reel_full_pipeline

# --- CUSTOMIZE YOUR THEMES HERE ---
THEMES = [
    "Morning motivation and productivity",
    "Night time reflection and gratitude",
    "Technology and AI future",
    "Nature and mindfulness",
    "Success mindset and hustle"
]

theme_index = 0


def run_post_cycle():
    """Called by scheduler - runs the full AI → Instagram pipeline"""
    global theme_index
    theme = THEMES[theme_index % len(THEMES)]
    theme_index += 1
    
    print(f"\n{'='*50}")
    print(f"[SCHEDULER] Starting post cycle with theme: {theme}")
    print(f"{'='*50}\n")
    
    try:
        # Run async engine in sync context
        result = asyncio.run(run_engine(theme))
        
        # Post to Instagram
        post_id = post_reel_full_pipeline(
            video_path=result['video_path'],
            caption=result['caption']
        )
        
        print(f"[SCHEDULER] ✅ Cycle complete! Post ID: {post_id}")
        
    except Exception as e:
        print(f"[SCHEDULER] ❌ Cycle failed: {e}")


def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    
    # Post at 9 AM IST
    scheduler.add_job(
        run_post_cycle,
        CronTrigger(hour=9, minute=0, timezone="Asia/Kolkata"),
        id="morning_post",
        name="Morning Instagram Post"
    )
    
    # Post at 9 PM IST
    scheduler.add_job(
        run_post_cycle,
        CronTrigger(hour=21, minute=0, timezone="Asia/Kolkata"),
        id="evening_post",
        name="Evening Instagram Post"
    )
    
    scheduler.start()
    print("[SCHEDULER] ✅ Scheduler started! Posts at 9AM & 9PM IST")
    
    # Graceful shutdown
    atexit.register(lambda: scheduler.shutdown())
    return scheduler