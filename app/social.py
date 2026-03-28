# app/social.py
# =====================================================
# SOCIAL MEDIA PIPELINE v4.2
# + Cloudinary Video Upload
# + Instagram Graph API (Reels)
# + Exhaustive Temporary File Cleanup
# =====================================================

import os
import time
import requests
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

# Use absolute path for consistency with engine.py
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

IG_USER_ID = os.getenv("INSTAGRAM_USER_ID")
IG_TOKEN   = os.getenv("INSTAGRAM_ACCESS_TOKEN")
GRAPH_BASE = "https://graph.facebook.com/v20.0"

def upload_to_cloudinary(video_path: str) -> str:
    print("[SOCIAL] Uploading upscaled reel to Cloudinary...", flush=True)
    result = cloudinary.uploader.upload(
        video_path,
        resource_type="video",
        folder="ig_agent",
        overwrite=True,
        public_id="latest_reel"
    )
    url = result['secure_url']
    print(f"[SOCIAL] Public URL: {url}")
    return url

def create_ig_container(video_url: str, caption: str) -> str:
    print("[SOCIAL] Creating Instagram container...", flush=True)
    response = requests.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media",
        data={
            'media_type':   'REELS',
            'video_url':    video_url,
            'caption':      caption,
            'access_token': IG_TOKEN
        }
    )
    data = response.json()
    if 'error' in data:
        raise Exception(f"Container error: {data['error']['message']}")
    print(f"[SOCIAL] Container ID: {data['id']}")
    return data['id']

def wait_for_processing(container_id: str, max_wait: int = 300) -> bool:
    print("[SOCIAL] Waiting for Instagram to process video...", flush=True)
    elapsed = 0
    while elapsed < max_wait:
        response = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={'fields': 'status_code', 'access_token': IG_TOKEN}
        )
        status = response.json().get('status_code', 'UNKNOWN')
        print(f"[SOCIAL] Status: {status} ({elapsed}s)")
        if status == 'FINISHED':
            return True
        elif status == 'ERROR':
            raise Exception("Instagram processing failed.")
        time.sleep(15)
        elapsed += 15
    raise TimeoutError("Instagram processing timed out.")

def publish_reel(container_id: str) -> str:
    print("[SOCIAL] Publishing reel live...", flush=True)
    response = requests.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media_publish",
        params={'creation_id': container_id, 'access_token': IG_TOKEN}
    )
    data = response.json()
    if 'error' in data:
        raise Exception(f"Publish error: {data['error']['message']}")
    print(f"[SOCIAL] ✅ LIVE ON INSTAGRAM! Post ID: {data['id']}")
    return data['id']

def cleanup_temp_files():
    """
    Deletes all temporary assets to keep the Railway disk/local folder clean.
    Updated to include force-mux and silent-render files.
    """
    # Define filenames to wipe
    files_to_delete = [
        # All potential slide files
        'slide_1.jpg', 'slide_2.jpg', 'slide_3.jpg', 'slide_4.jpg',
        'slide_5.jpg', 'slide_6.jpg', 'slide_7.jpg', 'slide_8.jpg',
        # Audio assets
        'temp_audio.mp3', 
        'temp-render-voice.m4a', 
        'temp_voice.m4a',
        # Video assets
        'silent_temp.mp4', 
        'reel.mp4'
    ]
    
    # Also look for any file starting with 'web_processed' or 'raw_news' 
    # if you decide to keep those in image_assembler
    
    for filename in files_to_delete:
        path = os.path.join(DATA_DIR, filename)
        # Check in DATA_DIR
        if os.path.exists(path):
            try:
                os.remove(path)
                print(f"[CLEANUP] Deleted {filename}")
            except Exception as e:
                print(f"[CLEANUP] Error deleting {filename}: {e}")
                
    # Also check the project root for MoviePy's temp-render-voice.m4a 
    # (Sometimes it defaults there if pathing is stripped)
    root_temp = os.path.join(os.getcwd(), "temp-render-voice.m4a")
    if os.path.exists(root_temp):
        os.remove(root_temp)

def post_reel_full_pipeline(video_path: str, caption: str) -> str:
    """Complete posting pipeline — upload, publish, clean up."""
    try:
        video_url    = upload_to_cloudinary(video_path)
        container_id = create_ig_container(video_url, caption)
        wait_for_processing(container_id)
        post_id      = publish_reel(container_id)
        return post_id
    except Exception as e:
        print(f"[SOCIAL] ❌ Post Pipeline Failed: {e}")
        raise
    finally:
        # We only cleanup in production; 
        # for local testing, you might want to comment this out to see the files.
        if os.getenv("ENV") == "production":
            cleanup_temp_files()
        else:
            print("[SOCIAL] ℹ️ Local environment: Skipping cleanup to allow file inspection.")