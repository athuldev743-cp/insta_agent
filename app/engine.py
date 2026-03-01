import os
import asyncio
import random
import shutil
import base64
from io import BytesIO
from pathlib import Path

import ffmpeg
import edge_tts
import requests
from moviepy.editor import (
    ImageClip, concatenate_videoclips,
    AudioFileClip, CompositeVideoClip,
    vfx,
)
from openai import OpenAI
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from PIL import Image, ImageEnhance

from app.config import AGENT_CONFIG
from app.sports_fetcher import (
    is_sports_theme,
    parse_sports_theme,
    fetch_all_sports_news,
    mark_as_posted,
)

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# IMAGE POST-PROCESS
# ─────────────────────────────────────────────────────────────
def enhance_image(image: Image.Image) -> Image.Image:
    image = ImageEnhance.Sharpness(image).enhance(2.0)
    image = ImageEnhance.Contrast(image).enhance(1.25)
    image = ImageEnhance.Color(image).enhance(1.4)
    image = ImageEnhance.Brightness(image).enhance(1.05)
    return image


# ─────────────────────────────────────────────────────────────
# NVIDIA IMAGE GENERATION (PRIMARY)
# ─────────────────────────────────────────────────────────────
def _decode_image_from_response_json(data: dict) -> Image.Image:
    if isinstance(data.get("image"), str):
        return Image.open(BytesIO(base64.b64decode(data["image"]))).convert("RGB")
    if isinstance(data.get("images"), list) and data["images"]:
        first = data["images"][0]
        if isinstance(first, str):
            return Image.open(BytesIO(base64.b64decode(first))).convert("RGB")
        if isinstance(first, dict) and isinstance(first.get("b64_json"), str):
            return Image.open(BytesIO(base64.b64decode(first["b64_json"]))).convert("RGB")
    if isinstance(data.get("data"), list) and data["data"]:
        first = data["data"][0]
        if isinstance(first, dict) and isinstance(first.get("b64_json"), str):
            return Image.open(BytesIO(base64.b64decode(first["b64_json"]))).convert("RGB")
    if isinstance(data.get("output"), list) and data["output"]:
        first = data["output"][0]
        if isinstance(first, dict) and isinstance(first.get("image"), str):
            return Image.open(BytesIO(base64.b64decode(first["image"]))).convert("RGB")
    raise ValueError(f"Unknown NVIDIA response schema. Keys: {list(data.keys())}")


def _generate_single_image_nvidia(prompt: str) -> Image.Image:
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    url     = os.getenv("NVIDIA_SD_URL", "").strip()
    if not api_key:
        raise ValueError("NVIDIA_API_KEY missing")
    if not url:
        raise ValueError("NVIDIA_SD_URL missing")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }
    r = requests.post(url, headers=headers, json={"prompt": prompt}, timeout=180)
    if r.status_code >= 400:
        raise RuntimeError(f"NVIDIA error {r.status_code}: {r.text[:400]}")
    return _decode_image_from_response_json(r.json())


# ─────────────────────────────────────────────────────────────
# HUGGINGFACE FALLBACK
# ─────────────────────────────────────────────────────────────
def _generate_single_image_hf(prompt: str) -> Image.Image:
    hf_key = os.getenv("HF_API_KEY", "").strip()
    if not hf_key:
        raise ValueError("HF_API_KEY missing")
    providers  = ["nscale", "fal-ai", "together", "nebius"]
    last_error = None
    width      = int(os.getenv("IMG_W", "1080"))
    height     = int(os.getenv("IMG_H", "1920"))
    for provider in providers:
        try:
            print(f"[HF] Trying provider={provider}", flush=True)
            client = InferenceClient(provider=provider, api_key=hf_key)
            image  = client.text_to_image(
                prompt=prompt,
                model=os.getenv("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"),
                width=width,
                height=height,
            )
            if image.getbbox() is None:
                raise ValueError("Blank image")
            return image.convert("RGB")
        except Exception as e:
            last_error = e
            print(f"[HF] ⚠️ {provider} failed: {str(e)[:180]}", flush=True)
    raise RuntimeError(f"All HF providers failed: {last_error}")


def _generate_single_image(prompt: str, filename: str) -> str:
    out_path = os.path.join(DATA_DIR, filename)
    try:
        print(f"[IMG] NVIDIA → {filename}", flush=True)
        img = _generate_single_image_nvidia(prompt)
        img = enhance_image(img)
        img.save(out_path, format="JPEG", quality=95, optimize=True)
        print(f"[IMG] ✅ NVIDIA: {out_path}", flush=True)
        return out_path
    except Exception as e:
        print(f"[IMG] ⚠️ NVIDIA failed ({filename}): {str(e)[:220]}", flush=True)
        img = _generate_single_image_hf(prompt)
        img = enhance_image(img)
        img.save(out_path, format="JPEG", quality=95, optimize=True)
        print(f"[IMG] ✅ HF: {out_path}", flush=True)
        return out_path


# ─────────────────────────────────────────────────────────────
# TEXT GENERATION — WEBDEV (unchanged)
# ─────────────────────────────────────────────────────────────
def build_hashtags(cfg: dict = None) -> str:
    if cfg is None:
        cfg = AGENT_CONFIG
    fixed    = cfg["hashtags"]["fixed"]
    variable = cfg["hashtags"]["variable"]
    count    = cfg["hashtags"]["count"]
    picked   = random.sample(variable, min(count - len(fixed), len(variable)))
    return " ".join(fixed + picked)


async def generate_content(theme: str) -> dict:
    """
    Generates caption + voice script.
    Automatically switches prompts/config for sports vs webdev themes.
    """
    is_sports   = is_sports_theme(theme)
    sport_data  = parse_sports_theme(theme) if is_sports else {}
    cfg         = AGENT_CONFIG
    sports_cfg  = cfg["sports"]

    if is_sports:
        cap_style = sports_cfg["caption_style"]
        hashtags  = build_hashtags(sports_cfg)
        cta       = random.choice(cap_style["cta_examples"])
        voice_cfg = sports_cfg["voice"]
        title     = sport_data.get("title", theme)
        summary   = sport_data.get("summary", "")
        source    = sport_data.get("source", "")
        region    = sport_data.get("region", "")

        prompt = f"""
You manage a sports Instagram account for an Indian audience.

News headline: "{title}"
Details: {summary}
Source: {source} | Region: {region}

Generate exactly 2 lines:

LINE 1 (Caption): {cap_style['tone']} Instagram caption.
  - Hook in first 5 words (urgent, exciting)
  - Use {cap_style['emoji_count']} emojis naturally
  - End with: "{cta}"
  - Add hashtags: {hashtags}

LINE 2 (Voiceover): Malayalam sports news bulletin script.
  - Style: {voice_cfg['script_style']}
  - Length: {voice_cfg['script_length']}
  - Example style: {voice_cfg.get('script_example', '')}
  - Structure: [Breaking hook 5s] → [What happened 20s] → [Key stats/score 15s] → [India significance 10s] → [Closing 5s]
  - Player/team names and scores stay in English
  - Formal news Malayalam (Asianet/Manorama style), NOT conversational
  - No hashtags, no emojis, no labels

Output ONLY 2 lines. No labels, no extra text.
"""
    else:
        cap_style = cfg["caption_style"]
        hashtags  = build_hashtags(cfg)
        cta       = random.choice(cap_style["cta_examples"])
        voice_cfg = cfg["voice"]

        prompt = f"""
You manage an Instagram account with these details:
- Niche: {cfg['account_niche']}
- Description: {cfg['account_description']}
- Target Audience: {cfg['target_audience']}
- Brand Voice: {cfg['brand_voice']}

Today's theme: "{theme}"

Generate exactly 2 lines:

LINE 1 (Caption): {cap_style['tone']} caption.
  - Hook in first 5 words to stop scrolling
  - Use {cap_style['emoji_count']} emojis naturally placed
  - End with this CTA: "{cta}"
  - Then add these hashtags: {hashtags}

LINE 2 (Voiceover): Detailed educational script IN MALAYALAM.
  - Write ENTIRELY in Malayalam language
  - Use English ONLY for technical terms (HTML, CSS, JavaScript, etc.)
  - Length: {voice_cfg['script_length']}
  - Style: {voice_cfg['script_style']}
  - Example style: {voice_cfg.get('script_example', '')}
  - Structure:
    * Hook (5s): Surprising fact or question about {theme} in Malayalam
    * Explain (25s): What it is and why it matters, in Malayalam
    * Example (20s): Simple real code or real world example with Malayalam explanation
    * Pro tip (15s): One expert tip beginners don't know, in Malayalam
    * Takeaway (10s): Key thing to remember, in Malayalam
    * CTA (5s): Encourage to follow for more, in Malayalam
  - Use simple everyday Malayalam words
  - Add natural commas for breathing pauses
  - Sound like a passionate Kerala YouTube coding teacher
  - No hashtags, no emojis, no labels

Output ONLY these 2 lines. No labels, no extra text.
"""

    print(f"[ENGINE] Generating content ({'sports' if is_sports else 'webdev'}): {theme[:60]}", flush=True)

    client = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1"
    )
    response = client.chat.completions.create(
        model="openrouter/auto",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=700,
    )
    lines        = response.choices[0].message.content.strip().split("\n", 1)
    caption      = lines[0].strip()
    voice_script = lines[1].strip() if len(lines) > 1 else caption

    print(f"[ENGINE] ✅ Caption: {caption[:80]}", flush=True)
    print(f"[ENGINE] ✅ Script words: {len(voice_script.split())}", flush=True)
    return {"caption": caption, "voice_script": voice_script}


# ─────────────────────────────────────────────────────────────
# MALAYALAM PREPROCESSOR (unchanged)
# ─────────────────────────────────────────────────────────────
def preprocess_malayalam_script(script: str) -> str:
    replacements = {
        "HTML": "എച്ച് ടി എം എൽ", "CSS": "സി എസ് എസ്",
        "JavaScript": "ജാവാസ്ക്രിപ്റ്റ്", "Python": "പൈത്തൺ",
        "React": "റിയാക്റ്റ്", "Node.js": "നോഡ് ജെ എസ്",
        "API": "എ പി ഐ", "SQL": "എസ് ക്യൂ എൽ",
        "JSON": "ജെ സൺ", "Git": "ഗിറ്റ്", "GitHub": "ഗിറ്റ്ഹബ്",
        "VS Code": "വി എസ് കോഡ്", "UI": "യൂ ഐ", "UX": "യൂ എക്സ്",
        "URL": "യൂ ആർ എൽ", "HTTP": "എച്ച് ടി ടി പി",
        "HTTPS": "എച്ച് ടി ടി പി എസ്", "DOM": "ഡോം",
        "TypeScript": "ടൈപ്പ്സ്ക്രിപ്റ്റ്", "MongoDB": "മോംഗോ ഡി ബി",
        "MySQL": "മൈ എസ് ക്യൂ എൽ", "Firebase": "ഫയർബേസ്",
        "Bootstrap": "ബൂട്ട്സ്ട്രാപ്പ്", "flexbox": "ഫ്ലെക്സ്ബോക്സ്",
        "Flexbox": "ഫ്ലെക്സ്ബോക്സ്", "frontend": "ഫ്രണ്ട്എൻഡ്",
        "Frontend": "ഫ്രണ്ട്എൻഡ്", "backend": "ബാക്ക്എൻഡ്",
        "Backend": "ബാക്ക്എൻഡ്", "developer": "ഡെവലപ്പർ",
        "Developer": "ഡെവലപ്പർ", "function": "ഫങ്ഷൻ",
        "variable": "വേരിയബിൾ", "array": "അറേ", "loop": "ലൂപ്പ്",
        "class": "ക്ലാസ്സ്", "object": "ഒബ്ജക്റ്റ്",
        "server": "സെർവർ", "database": "ഡേറ്റാബേസ്",
        "website": "വെബ്സൈറ്റ്", "browser": "ബ്രൗസർ",
        "code": "കോഡ്", "Code": "കോഡ്", "coding": "കോഡിംഗ്",
        "programming": "പ്രോഗ്രാമിംഗ്",
    }
    for eng, mal in replacements.items():
        script = script.replace(eng, mal)
    return script


# ─────────────────────────────────────────────────────────────
# VOICE GENERATION (unchanged)
# ─────────────────────────────────────────────────────────────
async def generate_voice(script: str, is_sports: bool = False) -> str:
    print("[ENGINE] Generating voiceover...", flush=True)

    # Sports uses same TTS voice — just skip the tech word replacements
    if not is_sports:
        script = preprocess_malayalam_script(script)

    voice      = AGENT_CONFIG["voice"]["tts_voice"]
    audio_path = os.path.join(DATA_DIR, "temp_audio.mp3")

    # Add natural pauses
    script = (
        script
        .replace(". ", ".   ")
        .replace("? ", "?   ")
        .replace("! ", "!   ")
        .replace(", ", ",  ")
    )

    communicate = edge_tts.Communicate(script, voice, rate="-10%", volume="+15%", pitch="+0Hz")
    await communicate.save(audio_path)
    print(f"[ENGINE] ✅ Audio: {audio_path}", flush=True)
    return audio_path


# ─────────────────────────────────────────────────────────────
# WEBDEV SLIDESHOW IMAGES (unchanged)
# ─────────────────────────────────────────────────────────────
async def generate_slideshow_images(theme: str, count: int = 8) -> list:
    print(f"[ENGINE] Generating {count} webdev slides...", flush=True)

    art_styles = {
        "pixel_art": (
            "pixel art style, vibrant saturated colors, intricate detailed pixel design, "
            "radiant neon glow effects, stark pure black background, nostalgic retro aesthetic, "
            "futuristic elements, rich atmospheric depth, ultra detailed pixel artwork, 16-bit game art style"
        ),
        "disney_3d": (
            "Disney Pixar 3D animation style, glossy smooth 3D render, vibrant saturated colors, "
            "soft cinematic lighting, studio quality 3D CGI, ray traced shadows and reflections, ultra detailed"
        ),
        "cyberpunk": (
            "cyberpunk neon city aesthetic, neon pink and cyan glow, rain reflections, holographic displays, "
            "futuristic tech noir atmosphere, ultra detailed digital painting, cinematic wide angle"
        ),
        "minimalist": (
            "ultra clean minimalist design, pure white and black, geometric shapes, Swiss design influence, "
            "sharp crisp lines, premium luxury brand visual style"
        ),
        "anime": (
            "anime illustration style, vibrant anime colors, cel shaded artwork, detailed background art, "
            "beautiful lighting, cinematic anime scene"
        ),
        "neon_noir": (
            "neon noir dark aesthetic, deep shadows and neon highlights, moody atmospheric lighting, "
            "purple and blue neon, mysterious cinematic feel, ultra detailed digital art"
        ),
    }

    style_name, chosen_style = random.choice(list(art_styles.items()))
    color_schemes = [
        "deep purple and cyan", "electric blue and white", "neon green on black",
        "orange and yellow", "hot pink and violet", "gold and white",
        "red and coral", "teal and mint",
    ]
    slide_concepts = [
        f"cinematic intro, big bold concept of {theme}, inspiring opener",
        f"clear definition and theory of {theme}, educational diagram",
        f"code editor showing {theme} example, syntax highlighted",
        f"step by step breakdown of {theme}, numbered process",
        f"warning, common mistakes beginners make with {theme}",
        f"trophy, best practices and pro tips for {theme}",
        f"real world apps and websites powered by {theme}",
        f"celebration scene, key takeaways of {theme}, motivational closer",
    ]

    seed        = random.randint(10000, 99999)
    image_paths = []

    for i in range(count):
        color   = color_schemes[i % len(color_schemes)]
        concept = slide_concepts[i % len(slide_concepts)]
        prompt  = (
            f"{concept}, {chosen_style}, color palette: {color}, "
            f"professional Instagram reel content, ultra sharp, "
            f"unique variation {seed + i * 7}, "
            f"9:16 vertical portrait orientation, no text overlay, no watermarks, no real people"
        )
        try:
            path = _generate_single_image(prompt, f"slide_{i+1}.jpg")
            image_paths.append(path)
        except Exception as e:
            print(f"[ENGINE] ⚠️ Slide {i+1} failed: {e}", flush=True)
            if image_paths:
                fallback = os.path.join(DATA_DIR, f"slide_{i+1}.jpg")
                shutil.copy2(image_paths[0], fallback)
                image_paths.append(fallback)
            else:
                raise

    print(f"[ENGINE] ✅ {len(image_paths)} webdev slides ready!", flush=True)
    return image_paths


# ─────────────────────────────────────────────────────────────
# VIDEO CREATION — MoviePy (sports: smooth transitions)
#                 FFmpeg  (webdev: existing behaviour)
# ─────────────────────────────────────────────────────────────

TRANSITION_EFFECTS = [
    "fade",       # smooth opacity fade (always works)
    "slide_left", # slide in from right
    "zoom_in",    # subtle ken-burns zoom
    "crossfade",  # dissolve between slides
]


def _moviepy_reel(image_paths: list[str], audio_path: str, output_path: str):
    """
    Build reel with MoviePy — used for sports posts.
    Each slide gets a randomly chosen transition effect.
    Total duration matches the audio.
    """
    audio        = AudioFileClip(audio_path)
    total_dur    = audio.duration
    slide_dur    = total_dur / len(image_paths)
    transition   = 0.5   # seconds overlap for transitions
    clips        = []

    for i, img_path in enumerate(image_paths):
        effect = random.choice(TRANSITION_EFFECTS)
        clip   = (
            ImageClip(img_path)
            .set_duration(slide_dur)
            .resize((1080, 1920))
        )

        # ── Apply transition effect ────────────────────────────────────
        if effect == "fade":
            clip = clip.fadein(transition).fadeout(transition)

        elif effect == "zoom_in":
            # Ken-Burns slow zoom: scale from 1.0 → 1.08 over slide duration
            clip = clip.resize(lambda t: 1 + 0.08 * (t / slide_dur))

        elif effect == "slide_left":
            # Slide in from right edge
            clip = clip.set_position(
                lambda t: (max(0, int(1080 * (1 - t / transition))), 0)
                if t < transition else (0, 0)
            )

        elif effect == "crossfade":
            clip = clip.fadein(transition)

        clips.append(clip)

    # Concatenate all clips
    final_video = concatenate_videoclips(clips, method="compose", padding=-transition)
    final_video = final_video.set_audio(audio).subclip(0, total_dur)

    print(f"[ENGINE] 🎬 MoviePy rendering {len(clips)} slides ({total_dur:.1f}s total)...", flush=True)
    final_video.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="5000k",
        audio_bitrate="192k",
        preset="fast",
        threads=4,
        logger=None,   # suppress verbose moviepy output
    )
    final_video.close()
    audio.close()
    print(f"[ENGINE] ✅ MoviePy reel: {output_path}", flush=True)


def create_reel(image_paths, audio_path: str, use_moviepy: bool = False) -> str:
    """
    use_moviepy=True  → sports posts (smooth transitions via MoviePy)
    use_moviepy=False → webdev posts (existing FFmpeg behaviour, unchanged)
    """
    output_path = os.path.join(DATA_DIR, "reel.mp4")
    slide_duration = 5

    if isinstance(image_paths, str):
        image_paths = [image_paths]

    if use_moviepy:
        _moviepy_reel(image_paths, audio_path, output_path)
        return output_path

    # ── Original FFmpeg path (webdev — unchanged) ──────────────────────────
    print("[ENGINE] Building reel with FFmpeg...", flush=True)
    try:
        if len(image_paths) == 1:
            video_input  = ffmpeg.input(image_paths[0], loop=1, t=40, framerate=30)
            video_scaled = (
                video_input
                .filter("scale", 1080, 1920, force_original_aspect_ratio="decrease")
                .filter("pad",   1080, 1920, "(ow-iw)/2", "(oh-ih)/2", color="black")
                .filter("setsar", "1/1")
            )
        else:
            segments = []
            for path in image_paths:
                seg = (
                    ffmpeg.input(path, loop=1, t=slide_duration, framerate=30)
                    .filter("scale", 1080, 1920, force_original_aspect_ratio="decrease")
                    .filter("pad",   1080, 1920, "(ow-iw)/2", "(oh-ih)/2", color="black")
                    .filter("setsar", "1/1")
                    .filter("fade", type="in",  start_time=0, duration=0.4)
                    .filter("fade", type="out", start_time=slide_duration - 0.4, duration=0.4)
                )
                segments.append(seg)
            video_scaled = ffmpeg.concat(*segments, v=1, a=0)

        audio_input = ffmpeg.input(audio_path)
        out = ffmpeg.output(
            video_scaled, audio_input, output_path,
            vcodec="libx264", acodec="aac", pix_fmt="yuv420p",
            movflags="+faststart", r=30,
            video_bitrate="5000k", audio_bitrate="192k",
            shortest=None,
            **{"threads": "4", "preset": "fast"},
        )
        ffmpeg.run(out, overwrite_output=True, quiet=False)
        print(f"[ENGINE] ✅ FFmpeg reel: {output_path}", flush=True)
        return output_path

    except ffmpeg.Error as e:
        print(f"[ENGINE] ❌ FFmpeg error: {e.stderr.decode()}", flush=True)
        raise


# ─────────────────────────────────────────────────────────────
# MASTER PIPELINE
# ─────────────────────────────────────────────────────────────
async def run_engine(theme: str) -> dict:
    """
    Full pipeline. Auto-detects webdev vs sports and routes accordingly.

    WebDev:  generate all 8 images (NVIDIA/HF) → FFmpeg reel
    Sports:  assemble_sports_slides (web scrape + PIL cards + NVIDIA/HF fallback)
             → MoviePy reel with transitions
    """
    is_sports  = is_sports_theme(theme)
    sport_data = parse_sports_theme(theme) if is_sports else {}

    # ── Generate caption + voice script ───────────────────────────────────
    content = await generate_content(theme)

    if is_sports:
        # ── Sports branch ─────────────────────────────────────────────────
        from app.image_assembler import assemble_sports_slides

        # Fetch all articles for multi-source image collection
        all_articles = fetch_all_sports_news(max_age_hours=24)

        # Assemble 8 slides + generate voice in parallel
        results = await asyncio.gather(
            assemble_sports_slides(sport_data, all_articles),
            generate_voice(content["voice_script"], is_sports=True),
        )
        image_paths = results[0]
        audio_path  = results[1]

        # MoviePy reel with smooth transitions for sports
        video_path = create_reel(image_paths, audio_path, use_moviepy=True)

    else:
        # ── WebDev branch (original behaviour — unchanged) ─────────────────
        results = await asyncio.gather(
            generate_slideshow_images(theme, count=8),
            generate_voice(content["voice_script"], is_sports=False),
        )
        image_paths = results[0]
        audio_path  = results[1]

        # FFmpeg reel (original)
        video_path = create_reel(image_paths, audio_path, use_moviepy=False)

    return {
        "video_path": video_path,
        "caption":    content["caption"],
    }