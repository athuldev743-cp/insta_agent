import os
import asyncio
import random
import requests
import ffmpeg
import edge_tts
from openai import OpenAI
from dotenv import load_dotenv
from config import AGENT_CONFIG
from huggingface_hub import InferenceClient
from PIL import Image

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(DATA_DIR, exist_ok=True)


# ── TEXT GENERATION ───────────────────────────────────────────
async def generate_content(theme: str) -> dict:
    """OpenRouter auto-router — picks best available free model automatically"""
    print(f"[ENGINE] Generating content for: {theme}")

    cfg       = AGENT_CONFIG
    cap_style = cfg["caption_style"]
    cta       = random.choice(cap_style["cta_examples"])
    hashtags  = build_hashtags()

    client = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1"
    )

    prompt = f"""
You manage an Instagram account with these details:
- Niche: {cfg['account_niche']}
- Description: {cfg['account_description']}
- Target Audience: {cfg['target_audience']}
- Brand Voice: {cfg['brand_voice']}

Today's theme: "{theme}"

Generate exactly 2 lines:
LINE 1 (Caption): {cap_style['tone']} caption.
  - Use {cap_style['emoji_count']} emojis
  - End with this CTA: "{cta}"
  - Then add these hashtags: {hashtags}

LINE 2 (Voiceover): {cfg['voice']['script_style']} script.
  - Length: {cfg['voice']['script_length']}
  - Natural spoken language only, no hashtags
  - Use short sentences with natural pauses
  - Teach ONE concept clearly and simply

Output ONLY these 2 lines. No labels, no extra text.
"""

    response = client.chat.completions.create(
        model="openrouter/auto",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=400
    )

    lines        = response.choices[0].message.content.strip().split('\n', 1)
    caption      = lines[0].strip()
    voice_script = lines[1].strip() if len(lines) > 1 else caption

    print(f"[ENGINE] ✅ Caption: {caption}")
    print(f"[ENGINE] ✅ Script:  {voice_script}")
    return {"caption": caption, "voice_script": voice_script}


# ── IMAGE GENERATION ──────────────────────────────────────────
def _generate_single_image(slide_prompt: str, filename: str) -> str:
    """Generate one image via HuggingFace — tries multiple providers"""
    providers  = ["nscale", "sambanova", "nebius"]
    last_error = None

    for provider in providers:
        try:
            print(f"[ENGINE] Trying provider: {provider}")
            client = InferenceClient(
                provider=provider,
                api_key=os.getenv("HF_API_KEY"),
            )
            image = client.text_to_image(
    prompt=slide_prompt,
    model="black-forest-labs/FLUX.1-schnell",
    width=576,    # ← was 768, smaller = less memory
    height=1024,  # ← was 1344, smaller = less memory
)

            if image.getbbox() is None:
                raise ValueError("Blank image returned")

            image_path = os.path.join(DATA_DIR, filename)
            image.save(image_path, format='JPEG', quality=95)
            print(f"[ENGINE] ✅ {filename} saved via {provider}")
            return image_path

        except Exception as e:
            print(f"[ENGINE] ⚠️  {provider} failed: {str(e)[:80]}")
            last_error = e
            continue

    raise ValueError(f"All providers failed for {filename}: {last_error}")


async def generate_slideshow_images(theme: str, count: int = 3) -> list:
    """Generate multiple images — each slide a different visual angle"""
    print(f"[ENGINE] Generating {count} slideshow images...")

    img_style = AGENT_CONFIG["image_style"]

    slide_prompts = [
        # Slide 1 — Visual concept / intro
        (
            f"Dark mode developer workspace, {theme} concept visualization, "
            f"{img_style['aesthetic']}, {img_style['colors']}, "
            f"cinematic wide shot, glowing screen, "
            f"9:16 portrait, ultra high quality, no text, no watermarks, no people"
        ),
        # Slide 2 — Code focused
        (
            f"Clean VS Code dark theme showing {theme} code example, "
            f"electric blue syntax highlighting on pure black background, "
            f"code centered on screen, crisp and readable, "
            f"9:16 portrait, ultra high quality, no watermarks"
        ),
        # Slide 3 — Visual metaphor / inspiring
        (
            f"Inspiring tech visual representing {theme}, "
            f"{img_style['mood']}, {img_style['colors']}, "
            f"abstract digital art, futuristic, "
            f"9:16 portrait, ultra high quality, no text, no watermarks"
        ),
    ]

    image_paths = []
    for i, slide_prompt in enumerate(slide_prompts[:count]):
        try:
            path = _generate_single_image(slide_prompt, f'slide_{i+1}.jpg')
            image_paths.append(path)
        except Exception as e:
            print(f"[ENGINE] ⚠️  Slide {i+1} failed: {e}")
            if image_paths:
                print(f"[ENGINE] Reusing slide 1 as fallback for slide {i+1}")
                image_paths.append(image_paths[0])
            else:
                raise

    print(f"[ENGINE] ✅ All {len(image_paths)} slides ready")
    return image_paths


# ── VOICE GENERATION ──────────────────────────────────────────
async def generate_voice(script: str) -> str:
    """Edge-TTS — completely free, unlimited"""
    print("[ENGINE] Generating voiceover...")

    voice       = AGENT_CONFIG["voice"]["tts_voice"]
    audio_path  = os.path.join(DATA_DIR, 'temp_audio.mp3')
    communicate = edge_tts.Communicate(script, voice)
    await communicate.save(audio_path)

    print(f"[ENGINE] ✅ Audio saved: {audio_path}")
    return audio_path


# ── VIDEO CREATION ────────────────────────────────────────────
def create_reel(image_paths, audio_path: str) -> str:
    """FFmpeg slideshow — cycles through images with fade transitions"""
    print("[ENGINE] Building slideshow reel with FFmpeg...")

    output_path = os.path.join(DATA_DIR, 'reel.mp4')

    # Handle both single image string and list
    if isinstance(image_paths, str):
        image_paths = [image_paths]

    try:
        slide_duration = 5  # 5s per slide = 15s total for 3 slides

        if len(image_paths) == 1:
            # Single image — simple loop
            video_input  = ffmpeg.input(image_paths[0], loop=1, t=15, framerate=30)
            video_scaled = (
                video_input
                .filter('scale', 1080, 1920, force_original_aspect_ratio='decrease')
                .filter('pad',   1080, 1920, '(ow-iw)/2', '(oh-ih)/2', color='black')
            )
        else:
            # Multiple images — concat with fade transitions
            segments = []
            for path in image_paths:
                seg = (
                    ffmpeg
                    .input(path, loop=1, t=slide_duration, framerate=30)
                    .filter('scale', 1080, 1920, force_original_aspect_ratio='decrease')
                    .filter('pad',   1080, 1920, '(ow-iw)/2', '(oh-ih)/2', color='black')
                    .filter('fade', type='in',  start_time=0,                  duration=0.4)
                    .filter('fade', type='out', start_time=slide_duration-0.4, duration=0.4)
                )
                segments.append(seg)

            video_scaled = ffmpeg.concat(*segments, v=1, a=0)

        audio_input = ffmpeg.input(audio_path)

        out = ffmpeg.output(
      video_scaled, audio_input, output_path,
      vcodec='libx264',
       acodec='aac',
       pix_fmt='yuv420p',
       movflags='+faststart',
        r=30,
        shortest=None,
    **{'threads': '1'}   # ← limits CPU/memory usage
)
        ffmpeg.run(out, overwrite_output=True, quiet=True)
        print(f"[ENGINE] ✅ Slideshow reel ready: {output_path}")
        return output_path

    except ffmpeg.Error as e:
        print(f"[ENGINE] ❌ FFmpeg error: {e.stderr.decode()}")
        raise


# ── HELPERS ───────────────────────────────────────────────────
def build_hashtags() -> str:
    fixed    = AGENT_CONFIG["hashtags"]["fixed"]
    variable = AGENT_CONFIG["hashtags"]["variable"]
    count    = AGENT_CONFIG["hashtags"]["count"]

    variable_count = count - len(fixed)
    picked         = random.sample(variable, min(variable_count, len(variable)))

    return " ".join(fixed + picked)


# ── MASTER PIPELINE ───────────────────────────────────────────
async def run_engine(theme: str) -> dict:
    content = await generate_content(theme)

    # Changed count=3 to count=1 — saves memory on Render free tier
    results = await asyncio.gather(
        generate_slideshow_images(theme, count=1),  # ← was count=3
        generate_voice(content['voice_script'])
    )

    image_paths = results[0]
    audio_path  = results[1]

    video_path = create_reel(image_paths, audio_path)
    return {
        "video_path": video_path,
        "caption":    content['caption']
    }