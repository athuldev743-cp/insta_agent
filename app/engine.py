import os
import gc
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
from PIL import Image as _PilImg
_PilImg.ANTIALIAS = getattr(_PilImg, "ANTIALIAS", _PilImg.LANCZOS)

from moviepy import ImageClip, concatenate_videoclips, AudioFileClip

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
    img = _decode_image_from_response_json(r.json())
    del r
    gc.collect()
    return img


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
            result = image.convert("RGB")
            del image
            gc.collect()
            return result
        except Exception as e:
            last_error = e
            print(f"[HF] ⚠️ {provider} failed: {str(e)[:180]}", flush=True)
            gc.collect()
    raise RuntimeError(f"All HF providers failed: {last_error}")


def _generate_single_image(prompt: str, filename: str) -> str:
    out_path = os.path.join(DATA_DIR, filename)
    img = None
    try:
        print(f"[IMG] NVIDIA → {filename}", flush=True)
        img = _generate_single_image_nvidia(prompt)
        img = enhance_image(img)
        img.save(out_path, format="JPEG", quality=95, optimize=True)
        print(f"[IMG] ✅ NVIDIA: {out_path}", flush=True)
    except Exception as e:
        print(f"[IMG] ⚠️ NVIDIA failed ({filename}): {str(e)[:220]}", flush=True)
        if img:
            img.close()
            del img
            gc.collect()
        img = _generate_single_image_hf(prompt)
        img = enhance_image(img)
        img.save(out_path, format="JPEG", quality=95, optimize=True)
        print(f"[IMG] ✅ HF: {out_path}", flush=True)
    finally:
        if img:
            img.close()
            del img
        gc.collect()
    return out_path


# ─────────────────────────────────────────────────────────────
# HASHTAG BUILDER
# ─────────────────────────────────────────────────────────────
def build_hashtags(cfg: dict | None = None) -> str:
    if cfg is None:
        cfg = AGENT_CONFIG
    tag_cfg  = cfg.get("hashtags", cfg)
    fixed    = tag_cfg.get("fixed", [])
    variable = tag_cfg.get("variable", [])
    count    = int(tag_cfg.get("count", len(fixed)))
    if not fixed and not variable:
        return ""
    need   = max(0, count - len(fixed))
    picked = random.sample(variable, min(need, len(variable)))
    return " ".join(fixed + picked)


def clean_llm_output_line(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"^\s*LINE\s*\d+\s*:\s*", "", text, flags=re.I)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


# ─────────────────────────────────────────────────────────────
# TEXT GENERATION
# ─────────────────────────────────────────────────────────────
async def generate_content(theme: str) -> dict:
    sport_data = parse_sports_theme(theme)
    cfg        = AGENT_CONFIG
    sports_cfg = cfg.get("sports", {})
    cap_cfg    = sports_cfg.get("caption_style", {})
    voice_cfg  = sports_cfg.get("voice", {})
    hashtags   = build_hashtags(sports_cfg)
    cta        = random.choice(cap_cfg.get("cta_examples", ["Follow for more!"]))
    title      = sport_data.get("title", theme)
    summary    = sport_data.get("summary", "")
    source     = sport_data.get("source", "")

    transliteration_rules = """
MANDATORY MALAYALAM SCRIPT RULES (voice script only):
1. ZERO ENGLISH LETTERS: Every word must be in Malayalam script only.
2. ABBREVIATIONS — write as ONE connected word, NO spaces between letters:
   - IPL  → ഐപിഎൽ
   - BCCI → ബിസിസിഐ
   - ICC  → ഐസിസി
   - ISL  → ഐഎസ്എൽ
3. NUMBERS: write as Malayalam words (97→തൊണ്ണൂറ്റേഴ്, 6→ആറ്)
4. NO markdown, NO labels, NO emojis in script.
"""

    prompt = f"""You are a professional Malayalam sports news anchor for Instagram.

News headline: "{title}"
Details: {summary}
Source: {source}

Generate exactly 2 lines with NO labels, NO line numbers:

LINE 1 (Caption — English OK): {cap_cfg.get('tone', 'Exciting')} Instagram caption.
Hook in first 5 words. {cap_cfg.get('emoji_count', '3-4')} emojis. End with: "{cta}"
Add hashtags: {hashtags}

LINE 2 (Voice script — Malayalam ONLY):
Style: {voice_cfg.get('script_style', 'Professional news reader')}
Length: {voice_cfg.get('script_length', '55-65 seconds')}
Structure: [Breaking hook 5s] → [What happened 20s] → [Key score/stat 15s] → [India significance 10s] → [Closing 5s]

{transliteration_rules}

Output ONLY 2 plain lines. No labels. No extra text. No markdown."""

    print(f"[ENGINE] Generating Sports Content: {theme[:60]}", flush=True)

    client = OpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1",
    )
    response = client.chat.completions.create(
        model="openrouter/auto",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
    )
    lines        = response.choices[0].message.content.strip().split("\n", 1)
    caption      = clean_llm_output_line(lines[0].strip())
    voice_script = clean_llm_output_line(lines[1].strip() if len(lines) > 1 else caption)

    # Free response from memory
    del response, client
    gc.collect()

    print(f"[ENGINE] ✅ Sports Caption: {caption[:80]}", flush=True)
    return {"caption": caption, "voice_script": voice_script}


# ─────────────────────────────────────────────────────────────
# PHONETIC PREPROCESSOR SETUP
# ─────────────────────────────────────────────────────────────
CSV_REPLACEMENTS = {}
PATTERN          = None


def _debug_script(script: str):
    latin = re.findall(r"[A-Za-z]", script)
    if latin:
        print("[TTS DEBUG] ❌ Latin letters:", "".join(sorted(set(latin))))
    else:
        print("[TTS DEBUG] ✅ No Latin letters.")
    runs = re.findall(r"\s{5,}", script)
    if runs:
        print(f"[TTS DEBUG] ❌ {len(runs)} long whitespace runs found")
    else:
        print("[TTS DEBUG] ✅ No long whitespace runs.")
    print("[TTS DEBUG] preview:", script[:300].replace("\n", " ⏎ "))


def init_preprocessor():
    global CSV_REPLACEMENTS, PATTERN
    current_dir = os.path.dirname(os.path.abspath(__file__))
    file_path   = os.path.join(current_dir, "replacements.csv")
    if not os.path.exists(file_path):
        print(f"[PREPROCESSOR] ⚠️  replacements.csv not found — skipping.")
        return
    try:
        with open(file_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            data   = sorted(list(reader), key=lambda x: len(x["english"]), reverse=True)
            CSV_REPLACEMENTS = {
                row["english"]: row["malayalam"]
                for row in data
                if row.get("english") and row.get("malayalam")
            }
        if CSV_REPLACEMENTS:
            pattern_str = "|".join(re.escape(k) for k in CSV_REPLACEMENTS.keys())
            PATTERN     = re.compile(pattern_str, re.IGNORECASE)
            print(f"[PREPROCESSOR] 🎯 Loaded {len(CSV_REPLACEMENTS)} rules")
    except Exception as e:
        print(f"[PREPROCESSOR] ❌ Error: {e}")


init_preprocessor()


# ─────────────────────────────────────────────────────────────
# MALAYALAM PREPROCESSOR
# ─────────────────────────────────────────────────────────────
def preprocess_malayalam_script(script: str) -> str:
    script = unicodedata.normalize("NFC", script)
    script = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", " ", script)
    script = re.sub(r"[*_`]+", "", script)
    script = re.sub(
        r"^\s*(വിദ്യാഭ്യാസപരമായ\s+സ്ക്രിപ്റ്റ്|വിദ്യാഭ്യാസ\s+സ്ക്രിപ്റ്റ്|"
        r"വാർത്ത|ന്യൂസ്|സ്ക്രിപ്റ്റ്)\s*:\s*",
        "", script, flags=re.IGNORECASE,
    )
    abbreviation_fixes = [
        (r"ഐ[\s\.\-]*പി[\s\.\-]*എൽ",      "അയ്പിഎൽ"),
        (r"ഐ[\s\.\-]*സി[\s\.\-]*സി",      "അയ്സിസി"),
        (r"ഐ[\s\.\-]*എസ്[\s\.\-]*എൽ",     "അയ്എസ്എൽ"),
        (r"ബി[\s\.\-]*സി[\s\.\-]*സി[\s\.\-]*ഐ", "ബിസിസിഐ"),
        (r"ഒ[\s\.\-]*ഡി[\s\.\-]*ഐ",      "ഒഡിഐ"),
        (r"എ[\s\.\-]*ഐ(?!\s*[എ-ഹ])",      "എഐ"),
        (r"എ[\s\.\-]*പി[\s\.\-]*ഐ",      "എപിഐ"),
        (r"ടി[\s\.\-]*ട്വന്റി",           "ടി ട്വന്റി"),
    ]
    for pattern, replacement in abbreviation_fixes:
        script = re.sub(pattern, replacement, script)

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

    tts_map = {
        "ഐ-പി-എൽ മാച്ച്":  "ഐ-പി-എൽ മത്സരം",
        "മാച്ച് വിശേഷങ്ങൾ": "മത്സര വിശേഷങ്ങൾ",
        "മാച്ച്":            "മത്സരം",
    }
    for k, v in sorted(tts_map.items(), key=lambda x: -len(x[0])):
        script = script.replace(k, v)

    if re.search(r"[A-Za-z]", script):
        print("[PREPROCESSOR] ⚠️  Latin detected — stripping...", flush=True)
        script = re.sub(r"[A-Za-z0-9_./:#@-]+", "", script)

    script = re.sub(r"(?<=\S)\s{5,}(?=\S)", " ", script)
    script = script.replace("\t", " ")
    script = script.replace(", ", ", ").replace(". ", ".  ")
    script = re.sub(r"^[\W_]+", "", script).strip()
    script = re.sub(r"\n{2,}", "\n", script)
    return script.strip()


# ─────────────────────────────────────────────────────────────
# VOICE GENERATION
# ─────────────────────────────────────────────────────────────
async def generate_voice(script: str, is_sports: bool = False) -> str:
    print("[ENGINE] Generating voiceover...", flush=True)
    script = preprocess_malayalam_script(script)
    _debug_script(script)

    voice = "ml-IN-MidhunNeural"  if is_sports else "ml-IN-SobhanaNeural"
    rate  = "+8%"                  if is_sports else "+2%"
    pitch = "+2Hz"                 if is_sports else "+0Hz"

    audio_path  = os.path.join(DATA_DIR, "temp_audio.mp3")
    communicate = edge_tts.Communicate(script, voice, rate=rate, pitch=pitch, volume="+20%")
    await communicate.save(audio_path)
    del communicate
    gc.collect()

    print(f"[ENGINE] ✅ Audio: {audio_path} at speed {rate}", flush=True)
    return audio_path


# ─────────────────────────────────────────────────────────────
# VIDEO CREATION  (memory-optimised MoviePy)
# ─────────────────────────────────────────────────────────────
def _moviepy_reel(image_paths: list[str], audio_path: str, output_path: str):
    from moviepy import vfx

    audio      = AudioFileClip(audio_path)
    total_dur  = audio.duration
    num_slides = len(image_paths)
    slide_dur  = total_dur / num_slides
    overlap    = 0.6

    clips = []
    for i, img_path in enumerate(image_paths):
        clip = (
            ImageClip(img_path)
            .with_duration(slide_dur + overlap)
            .resized((1080, 1920))
        )
        clip = clip.resized(lambda t: 1 + 0.1 * (t / (slide_dur + overlap)))
        if i > 0:
            clip = clip.with_effects([vfx.FadeIn(overlap)])
        clips.append(clip)

    final_video = concatenate_videoclips(clips, method="compose", padding=-overlap)
    final_video = final_video.with_audio(audio).subclipped(0, total_dur)

    print(f"[ENGINE] 🎬 Rendering: {total_dur:.2f}s", flush=True)
    final_video.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="4000k",       # ← reduced from 6000k to save RAM during encode
        preset="ultrafast",    # ← ultrafast uses far less RAM than "medium"
        threads=2,             # ← reduced from 4 to limit concurrent RAM use
        logger=None,
    )

    # ── Free everything immediately after render ──
    final_video.close()
    audio.close()
    for clip in clips:
        clip.close()
    del final_video, audio, clips
    gc.collect()


def create_reel(image_paths, audio_path: str, use_moviepy: bool = False) -> str:
    output_path    = os.path.join(DATA_DIR, "reel.mp4")
    slide_duration = 5

    if isinstance(image_paths, str):
        image_paths = [image_paths]

    if use_moviepy:
        _moviepy_reel(image_paths, audio_path, output_path)
        return output_path

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
            video_bitrate="4000k", audio_bitrate="192k",
            shortest=None, **{"threads": "2", "preset": "ultrafast"},
        )
        ffmpeg.run(out, overwrite_output=True, quiet=False)
        print(f"[ENGINE] ✅ FFmpeg reel: {output_path}", flush=True)
        return output_path
    except ffmpeg.Error as e:
        print(f"[ENGINE] ❌ FFmpeg error: {e.stderr.decode()}", flush=True)
        raise
    finally:
        gc.collect()


# ─────────────────────────────────────────────────────────────
# MASTER PIPELINE  (sequential to minimise peak RAM)
# ─────────────────────────────────────────────────────────────
async def run_engine(theme: str) -> dict:
    is_sports  = is_sports_theme(theme)
    sport_data = parse_sports_theme(theme) if is_sports else {}

    # ── Step 1: Generate text content first (lightweight) ────────────────
    content = await generate_content(theme)
    gc.collect()

    if is_sports:
        from app.image_assembler import assemble_sports_slides
        all_articles = fetch_all_sports_news(max_age_hours=24)

        # ── Step 2: Generate audio first (small RAM footprint) ────────────
        print(f"[ENGINE] 🎙️ Generating voice first...", flush=True)
        audio_path = await generate_voice(content["voice_script"], is_sports=True)
        gc.collect()

        # ── Step 3: Generate slides after audio is done ───────────────────
        print(f"[ENGINE] 🖼️ Generating slides...", flush=True)
        image_paths = await assemble_sports_slides(sport_data, all_articles)

        # ── Free articles list — no longer needed ─────────────────────────
        del all_articles
        gc.collect()

    else:
        audio_path, image_paths = await asyncio.gather(
            generate_voice(content["voice_script"], is_sports=False),
            generate_slideshow_images(theme, count=8),
        )

    # ── Step 4: Compose video ─────────────────────────────────────────────
    print(f"[ENGINE] 🎬 Composing video...", flush=True)
    video_path = create_reel(image_paths, audio_path, use_moviepy=True)

    # ── Step 5: Clean up slide files from disk after video render ─────────
    for p in image_paths:
        try:
            os.remove(p)
        except Exception:
            pass
    gc.collect()

    return {"video_path": video_path, "caption": content["caption"]}


# ─────────────────────────────────────────────────────────────
# WEBDEV SLIDESHOW (non-sports — unchanged logic, added gc)
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
            gc.collect()  # free after each slide
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