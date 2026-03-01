import os
import time
import requests
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
)

IG_USER_ID = os.getenv("INSTAGRAM_USER_ID")
IG_TOKEN   = os.getenv("INSTAGRAM_ACCESS_TOKEN")
GRAPH_BASE = "https://graph.facebook.com/v20.0"
DATA_DIR   = os.path.join(os.path.dirname(__file__), '..', 'data')


def upload_to_cloudinary(video_path: str) -> str:
    print("[SOCIAL] Uploading to Cloudinary...")
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
    print("[SOCIAL] Creating Instagram container...")
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
    print("[SOCIAL] Waiting for Instagram to process...")
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
    print("[SOCIAL] Publishing reel...")
    response = requests.post(
        f"{GRAPH_BASE}/{IG_USER_ID}/media_publish",
        params={'creation_id': container_id, 'access_token': IG_TOKEN}
    )
    data = response.json()
    if 'error' in data:
        raise Exception(f"Publish error: {data['error']['message']}")
    print(f"[SOCIAL] ✅ Live! Post ID: {data['id']}")
    return data['id']


def cleanup_temp_files():
    """
    Delete all temporary files created during a post cycle.
    Now covers all 8 slides (was previously only 3).
    sports_cooldown.json and theme_index.txt are intentionally kept.
    """
    files_to_delete = [
        # All 8 slide images
        'slide_1.jpg', 'slide_2.jpg', 'slide_3.jpg', 'slide_4.jpg',
        'slide_5.jpg', 'slide_6.jpg', 'slide_7.jpg', 'slide_8.jpg',
        # Audio and final video
        'temp_audio.mp3',
        'reel.mp4',
    ]
    for filename in files_to_delete:
        path = os.path.join(DATA_DIR, filename)
        if os.path.exists(path):
            os.remove(path)
            print(f"[CLEANUP] Deleted {filename}")


def post_reel_full_pipeline(video_path: str, caption: str) -> str:
    """Complete posting pipeline — upload, publish, clean up."""
    try:
        video_url    = upload_to_cloudinary(video_path)
        container_id = create_ig_container(video_url, caption)
        wait_for_processing(container_id)
        post_id      = publish_reel(container_id)
        return post_id
    finally:
        cleanup_temp_files()  # Always runs, even if posting fails