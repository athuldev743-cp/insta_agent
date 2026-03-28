# test_agent.py
import asyncio
from app.engine import run_engine
from app.sports_fetcher import get_top_sports_story, build_sports_theme
import os
import subprocess


async def local_test():
    print("--- 🔍 PHASE 1: Testing IPL Fetcher ---")
    
    # We simulate a 2 PM run to test the "Lineup/Probable XI" logic
    article = get_top_sports_story(story_slot=1)

    if not article:
        print("❌ No articles found. Check your internet or RSS feeds.")
        return

    print(f"✅ Found Article: {article['title']}")
    print(f"📊 Relevance Score: {article['relevance_score']}")

    print("\n--- 🖼️ PHASE 2: Testing Engine (Scrape & Upscale) ---")
    theme = build_sports_theme(article)

    # Check audio duration BEFORE running engine (optional debug)
    r = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            "data/temp_audio.mp3"
        ],
        capture_output=True,
        text=True
    )

    print("AUDIO DURATION:", r.stdout.strip() or "FAILED: " + r.stderr[:200])

    result = await run_engine(theme)

    print("\n--- 🎬 PHASE 3: Verify Output ---")
    video_path = result.get("video_path")

    if video_path and os.path.exists(video_path):
        print(f"✅ SUCCESS! Reel generated at: {video_path}")
        print(f"📝 Caption Preview: {result['caption'][:100]}...")
    else:
        print("❌ Video file was not created.")


if __name__ == "__main__":
    asyncio.run(local_test())