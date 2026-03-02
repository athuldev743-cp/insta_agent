import os
import asyncio
import random
import shutil
import base64
import csv
import re
from io import BytesIO
from pathlib import Path
import unicodedata
import ffmpeg
import edge_tts
import requests
# moviepy import patched below
from PIL import Image as _PilImg
_PilImg.ANTIALIAS = getattr(_PilImg, "ANTIALIAS", _PilImg.LANCZOS)
try:
    from moviepy import ImageClip, concatenate_videoclips, AudioFileClip, CompositeVideoClip
except ImportError:
    from moviepy import ImageClip, concatenate_videoclips, AudioFileClip, CompositeVideoClip
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
def build_hashtags(cfg: dict | None = None) -> str:
    """
    Supports 2 config shapes:
    1) cfg["hashtags"] = {"fixed": [...], "variable": [...], "count": N}
    2) cfg itself has {"fixed": [...], "variable": [...], "count": N}
    """
    if cfg is None:
        cfg = AGENT_CONFIG

    tag_cfg = cfg.get("hashtags", cfg)  # fallback to cfg itself

    fixed = tag_cfg.get("fixed", [])
    variable = tag_cfg.get("variable", [])
    count = int(tag_cfg.get("count", len(fixed)))

    if not fixed and not variable:
        return ""  # no hashtags configured

    need = max(0, count - len(fixed))
    picked = random.sample(variable, min(need, len(variable)))
    return " ".join(fixed + picked)


async def generate_content(theme: str) -> dict:
    is_sports = is_sports_theme(theme)
    sport_data = parse_sports_theme(theme) if is_sports else {}
    cfg = AGENT_CONFIG
    
    # ── THE ABSOLUTE TRANSLATION RULE ──
    # This prevents the "West Indies" stumbling issue at the source.
    transliteration_rules = """
    STRICT SCRIPT RULES (LINE 2):
    1. ZERO ENGLISH ALPHABETS: Every single word must be in Malayalam script. 
       If a name is English, write its Malayalam sound (e.g., 'West Indies' -> 'വെസ്റ്റ് ഇൻഡീസ്').
    2. PHONETIC ACCURACY: Use the spelling common in Kerala news (e.g., 'Messi' -> 'മെസ്സി').
    3. NUMBERS: Write scores/numbers as Malayalam words (e.g., '10' -> 'പത്ത്').
    4. NO ABBREVIATIONS: Write 'IPL' as 'ഐ പി എൽ'.
    """

    if is_sports:
        sports_cfg = cfg["sports"]
        hashtags = build_hashtags(sports_cfg)
        prompt = f"""
You are a Malayalam sports anchor. 
Headline: "{sport_data.get('title', theme)}"
{transliteration_rules}
Generate 2 lines:
LINE 1: Instagram Caption + {hashtags}
LINE 2: High-energy news script ENTIRELY in Malayalam script.
"""
    else:
        hashtags = build_hashtags(cfg)
        prompt = f"""
You are a Malayalam coding teacher.
Topic: "{theme}"
{transliteration_rules}
Generate 2 lines:
LINE 1: Helpful Caption + {hashtags}
LINE 2: Educational script ENTIRELY in Malayalam script.
"""

    print(f"[ENGINE] 🔄 Step 1: Requesting 100% Transliterated Script...", flush=True)

    client = OpenAI(api_key=os.getenv("OPENROUTER_API_KEY"), base_url="https://openrouter.ai/api/v1")
    response = client.chat.completions.create(
        model="openrouter/auto",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
    )
    
    lines = response.choices[0].message.content.strip().split("\n", 1)
    caption = lines[0].strip()
    # The "Clean" script that goes to the Voice engine
    voice_script = lines[1].strip() if len(lines) > 1 else caption
    caption = re.sub(r"^\s*LINE\s*1\s*:\s*", "", caption, flags=re.I).strip()
    voice_script = re.sub(r"^\s*LINE\s*2\s*:\s*", "", voice_script, flags=re.I).strip()

    return {"caption": caption, "voice_script": voice_script}
# ─────────────────────────────────────────────────────────────
# MALAYALAM PREPROCESSOR (unchanged)
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# PHONETIC PREPROCESSOR SETUP
# ─────────────────────────────────────────────────────────────

CSV_REPLACEMENTS = {}
PATTERN = None

def _debug_script(script: str):
    latin = re.findall(r"[A-Za-z]", script)
    if latin:
        print("[TTS DEBUG] ❌ Latin letters still present:", "".join(sorted(set(latin))))
        for m in re.finditer(r".{0,20}[A-Za-z].{0,20}", script):
            print("[TTS DEBUG] context:", m.group(0))
    else:
        print("[TTS DEBUG] ✅ No Latin letters found.")

    # Detect weird whitespace (tabs, non-breaking spaces, zero-width)
    weird = [ch for ch in script if ch in ("\u200b", "\u200c", "\u200d", "\ufeff", "\u00a0", "\t")]
    if weird:
        print("[TTS DEBUG] ⚠️ Weird whitespace codepoints:", [hex(ord(c)) for c in weird])

    # Detect spaced-out Malayalam initials (common reason for spelling)
    if re.search(r"[\u0D00-\u0D7F]\s+[\u0D00-\u0D7F]\s+[\u0D00-\u0D7F]", script):
        print("[TTS DEBUG] ⚠️ Malayalam letters separated by spaces detected (may cause spelling).")

    print("[TTS DEBUG] script preview:", script[:300].replace("\n", " ⏎ "))

    if re.search(r"[\u0D00-\u0D7F]\s+[\u0D00-\u0D7F]\s+[\u0D00-\u0D7F]", script):
     print("[TTS DEBUG] ⚠️ Spaced Malayalam initials detected (may sound like spelling).")

def init_preprocessor():
    global CSV_REPLACEMENTS, PATTERN

    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(current_dir, "replacements.csv")

    if not os.path.exists(file_path):
        print(f"[PREPROCESSOR] ⚠️ Warning: {file_path} not found.")
        return

    try:
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            data = sorted(list(reader), key=lambda x: len(x["english"]), reverse=True)
            CSV_REPLACEMENTS = {
                row["english"]: row["malayalam"]
                for row in data
                if row.get("english") and row.get("malayalam")
            }

        if CSV_REPLACEMENTS:
            # Match phrases even with punctuation nearby. No \b.
            # We still escape keys, and we sort longest-first already.
            pattern_str = "|".join(re.escape(k) for k in CSV_REPLACEMENTS.keys())
            PATTERN = re.compile(pattern_str, re.IGNORECASE)
            print(f"[PREPROCESSOR] 🎯 Loaded {len(CSV_REPLACEMENTS)} rules from {file_path}")
    except Exception as e:
        print(f"[PREPROCESSOR] ❌ Error loading CSV: {e}")


init_preprocessor()

def preprocess_malayalam_script(script: str) -> str:
    # Normalize
    script = unicodedata.normalize("NFC", script)

    # 0) Remove invisible junk early (always)
    script = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", " ", script)

    # 1) Apply CSV replacements (always)
    if PATTERN:
        def repl(m):
            s = m.group(0)
            return (
                CSV_REPLACEMENTS.get(s)
                or CSV_REPLACEMENTS.get(s.title())
                or CSV_REPLACEMENTS.get(s.upper())
                or CSV_REPLACEMENTS.get(s.lower())
                or s
            )
        script = PATTERN.sub(repl, script)

    # 2) Collapse spaced Malayalam abbreviations (always)
    script = re.sub(r"\bഐ\s*പി\s*എൽ\b", "ഐപിഎൽ", script)
    script = re.sub(r"\bഎ\s*ഐ\b", "എഐ", script)
    script = re.sub(r"\bഎ\s*പി\s*ഐ\b", "എപിഐ", script)
    script = re.sub(r"\bഎ\s*ൽ\s*എൽ\s*എം\b", "എൽഎൽഎം", script)
    script = re.sub(r"\bഎ\s*ഐ\s*എം\s*എൽ\b", "എഐഎംഎൽ", script)

    # Optional aggressive collapse (use only if needed)
    # script = re.sub(r"(?<=\b[\u0D00-\u0D7F])\s+(?=[\u0D00-\u0D7F]\b)", "", script)

    # 3) Strip leftover Latin tokens (only if still present)
    if re.search(r"[A-Za-z]", script):
        print("[PREPROCESSOR] ⚠️ Latin detected, stripping leftovers...")
        script = re.sub(r"[A-Za-z0-9_./:#@-]+", "", script)

    # 4) Pacing
    script = script.replace(", ", ",   ").replace(". ", ".    ")

    # Clean extra whitespace
    script = re.sub(r"\s{2,}", " ", script).strip()
    return script


# ─────────────────────────────────────────────────────────────
# VOICE GENERATION (unchanged)
# ─────────────────────────────────────────────────────────────
async def generate_voice(script: str, is_sports: bool = False) -> str:
    print("[ENGINE] Generating voiceover...", flush=True)

    

    script = preprocess_malayalam_script(script)
    _debug_script(script)

    if is_sports:
        voice = AGENT_CONFIG["sports"]["voice"]["tts_voice"]
        rate = "+15%" 
        pitch = "+4Hz"
    else:
        voice = AGENT_CONFIG.get("voice", {}).get("tts_voice", "ml-IN-MidhunNeural")
        rate = "-5%"
        pitch = "+0Hz"

    audio_path = os.path.join(DATA_DIR, "temp_audio.mp3")

    communicate = edge_tts.Communicate(
        script, 
        voice, 
        rate=rate, 
        pitch=pitch, 
        volume="+15%"
    )
    
    await communicate.save(audio_path)
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
    from moviepy import vfx

    audio = AudioFileClip(audio_path)
    total_dur = audio.duration  # Dynamic fix: matches Malayalam speech perfectly
    
    num_slides = len(image_paths)
    slide_dur = total_dur / num_slides
    overlap = 0.6  # Seconds for the transition overlap
    
    clips = []

    for i, img_path in enumerate(image_paths):
        # Create clip with extra time for the crossfade
        clip = (
            ImageClip(img_path)
            .with_duration(slide_dur + overlap)
            .resized((1080, 1920))
        )

        # ── ANIMATION: Slow Ken-Burns Zoom ──
        # Starts at 1.0x and zooms to 1.1x over the duration
        clip = clip.resized(lambda t: 1 + 0.1 * (t / (slide_dur + overlap)))
        
        # ── TRANSITION: Crossfade ──
        if i > 0:
            clip = clip.with_effects([vfx.FadeIn(overlap)])
        
        clips.append(clip)

    # Combine clips with negative padding to create the overlap
    final_video = concatenate_videoclips(clips, method="compose", padding=-overlap)

    # Trim to match audio exactly
    final_video = final_video.with_audio(audio).subclipped(0, total_dur)

    print(f"[ENGINE] 🎬 Rendering Reel: {total_dur:.2f}s", flush=True)
    
    final_video.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="6000k",
        preset="medium",
        threads=4,
        logger=None
    )
    
    final_video.close()
    audio.close()

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
    is_sports = is_sports_theme(theme)
    sport_data = parse_sports_theme(theme) if is_sports else {}

    # ── STEP 1: TRANSLATE & GENERATE SCRIPT ──
    # We do this first so we have the pure Malayalam text ready.
    content = await generate_content(theme)

    # ── STEP 2: GENERATE VOICE & IMAGES ──
    if is_sports:
        from app.image_assembler import assemble_sports_slides
        all_articles = fetch_all_sports_news(max_age_hours=24)

        # Run Slide Assembly and Voice Generation
        print(f"[ENGINE] 🎙️ Step 2: Generating Voice and 🖼️ Slides...", flush=True)
        image_paths, audio_path = await asyncio.gather(
            assemble_sports_slides(sport_data, all_articles),
            generate_voice(content["voice_script"], is_sports=True)
        )

        # ── STEP 3: ASSEMBLE VIDEO (Using exact audio duration) ──
        print(f"[ENGINE] 🎬 Step 3: Mastering Video to match Audio duration...", flush=True)
        video_path = create_reel(image_paths, audio_path, use_moviepy=True)

    else:
        # WebDev Path
        image_paths, audio_path = await asyncio.gather(
            generate_slideshow_images(theme, count=8),
            generate_voice(content["voice_script"], is_sports=False)
        )
        video_path = create_reel(image_paths, audio_path, use_moviepy=False)

    return {
        "video_path": video_path,
        "caption": content["caption"],
    }