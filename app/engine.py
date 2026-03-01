import os
import asyncio
import random
import shutil
import subprocess
import base64
from io import BytesIO
from pathlib import Path

import ffmpeg
import edge_tts
import requests
from openai import OpenAI
from dotenv import load_dotenv
from huggingface_hub import InferenceClient
from PIL import Image, ImageEnhance

from app.config import AGENT_CONFIG

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# IMAGE POST-PROCESS
# ─────────────────────────────────────────────────────────────
def enhance_image(image: Image.Image) -> Image.Image:
    """Post-process image for maximum quality"""
    image = ImageEnhance.Sharpness(image).enhance(2.0)
    image = ImageEnhance.Contrast(image).enhance(1.25)
    image = ImageEnhance.Color(image).enhance(1.4)
    image = ImageEnhance.Brightness(image).enhance(1.05)
    return image


# ─────────────────────────────────────────────────────────────
# NVIDIA IMAGE GENERATION (PRIMARY)
# ─────────────────────────────────────────────────────────────
def _decode_image_from_response_json(data: dict) -> Image.Image:
    """
    Tries common NVIDIA / genai response formats:
    - {"image": "<b64>"}
    - {"images": ["<b64>", ...]}
    - {"images": [{"b64_json": "<b64>"}]}
    - {"data": [{"b64_json": "<b64>"}]}  (OpenAI-like)
    - {"output": [{"image": "<b64>"}]}
    """
    # 1) direct "image"
    if isinstance(data.get("image"), str):
        b = base64.b64decode(data["image"])
        return Image.open(BytesIO(b)).convert("RGB")

    # 2) "images": [b64...]
    if isinstance(data.get("images"), list) and data["images"]:
        first = data["images"][0]
        if isinstance(first, str):
            b = base64.b64decode(first)
            return Image.open(BytesIO(b)).convert("RGB")
        if isinstance(first, dict) and isinstance(first.get("b64_json"), str):
            b = base64.b64decode(first["b64_json"])
            return Image.open(BytesIO(b)).convert("RGB")

    # 3) OpenAI-style "data"
    if isinstance(data.get("data"), list) and data["data"]:
        first = data["data"][0]
        if isinstance(first, dict) and isinstance(first.get("b64_json"), str):
            b = base64.b64decode(first["b64_json"])
            return Image.open(BytesIO(b)).convert("RGB")

    # 4) other nested patterns
    if isinstance(data.get("output"), list) and data["output"]:
        first = data["output"][0]
        if isinstance(first, dict) and isinstance(first.get("image"), str):
            b = base64.b64decode(first["image"])
            return Image.open(BytesIO(b)).convert("RGB")

    raise ValueError(f"Unknown NVIDIA response schema. Keys: {list(data.keys())}")


def _generate_single_image_nvidia(prompt: str) -> Image.Image:
    """
    NVIDIA API Catalog image generation using NVIDIA_API_KEY.

    Requires:
      NVIDIA_API_KEY
      NVIDIA_SD_URL  (example: https://ai.api.nvidia.com/v1/genai/stabilityai/stable-diffusion-3-medium)
    """
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not api_key:
        raise ValueError("NVIDIA_API_KEY is missing")

    url = os.getenv("NVIDIA_SD_URL", "").strip()
    if not url:
        raise ValueError("NVIDIA_SD_URL is missing in .env")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # Minimal payload works for many genai endpoints.
    # If your model supports width/height/steps, you can add them here.
    payload = {
        "prompt": prompt
    }

    r = requests.post(url, headers=headers, json=payload, timeout=180)

    if r.status_code >= 400:
        raise RuntimeError(f"NVIDIA error {r.status_code}: {r.text[:400]}")

    data = r.json()
    return _decode_image_from_response_json(data)


# ─────────────────────────────────────────────────────────────
# HUGGINGFACE PROVIDER ROUTER (FALLBACK)
# ─────────────────────────────────────────────────────────────
def _generate_single_image_hf(prompt: str) -> Image.Image:
    """
    HuggingFace router fallback using HF_API_KEY.
    Uses FLUX.1-schnell via provider routing.
    """
    hf_key = os.getenv("HF_API_KEY", "").strip()
    if not hf_key:
        raise ValueError("HF_API_KEY is missing")

    providers = ["nscale", "fal-ai", "together", "nebius"]
    last_error = None

    width = int(os.getenv("IMG_W", "1080"))
    height = int(os.getenv("IMG_H", "1920"))

    for provider in providers:
        try:
            print(f"[HF] Trying provider={provider}", flush=True)
            client = InferenceClient(provider=provider, api_key=hf_key)

            image = client.text_to_image(
                prompt=prompt,
                model=os.getenv("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell"),
                width=width,
                height=height,
            )

            if image.getbbox() is None:
                raise ValueError("Blank image returned from HF provider")

            return image.convert("RGB")

        except Exception as e:
            last_error = e
            print(f"[HF] ⚠️ {provider} failed: {str(e)[:180]}", flush=True)

    raise RuntimeError(f"All HF providers failed: {last_error}")


def _generate_single_image(prompt: str, filename: str) -> str:
    """
    Your required order:
    1) NVIDIA
    2) HF fallback
    """
    out_path = os.path.join(DATA_DIR, filename)

    try:
        print(f"[IMG] NVIDIA → {filename}", flush=True)
        img = _generate_single_image_nvidia(prompt)
        img = enhance_image(img)
        img.save(out_path, format="JPEG", quality=95, optimize=True)
        print(f"[IMG] ✅ Saved via NVIDIA: {out_path}", flush=True)
        return out_path

    except Exception as nvidia_err:
        print(f"[IMG] ⚠️ NVIDIA failed ({filename}): {str(nvidia_err)[:220]}", flush=True)
        print(f"[IMG] Falling back → HF ({filename})", flush=True)

        img = _generate_single_image_hf(prompt)
        img = enhance_image(img)
        img.save(out_path, format="JPEG", quality=95, optimize=True)
        print(f"[IMG] ✅ Saved via HF: {out_path}", flush=True)
        return out_path


# ─────────────────────────────────────────────────────────────
# TEXT GENERATION (OPENROUTER)
# ─────────────────────────────────────────────────────────────
def build_hashtags() -> str:
    fixed = AGENT_CONFIG["hashtags"]["fixed"]
    variable = AGENT_CONFIG["hashtags"]["variable"]
    count = AGENT_CONFIG["hashtags"]["count"]
    variable_count = max(0, count - len(fixed))
    picked = random.sample(variable, min(variable_count, len(variable)))
    return " ".join(fixed + picked)


async def generate_content(theme: str) -> dict:
    """OpenRouter — generates caption + detailed voice script"""
    print(f"[ENGINE] Generating content for: {theme}", flush=True)

    cfg = AGENT_CONFIG
    cap_style = cfg["caption_style"]
    cta = random.choice(cap_style["cta_examples"])
    hashtags = build_hashtags()

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
  - Hook in first 5 words to stop scrolling
  - Use {cap_style['emoji_count']} emojis naturally placed
  - End with this CTA: "{cta}"
  - Then add these hashtags: {hashtags}

LINE 2 (Voiceover): Detailed educational script IN MALAYALAM.
  - Write ENTIRELY in Malayalam language
  - Use English ONLY for technical terms (HTML, CSS, JavaScript, etc.)
  - Length: {cfg['voice']['script_length']}
  - Style: {cfg['voice']['script_style']}
  - Structure:
    * Hook (5s): Surprising fact or question about {theme} in Malayalam
    * Explain (25s): What it is and why it matters, explained in Malayalam
    * Example (20s): Simple real code or real world example with Malayalam explanation
    * Pro tip (15s): One expert tip beginners dont know, in Malayalam
    * Takeaway (10s): Key thing to remember, in Malayalam
    * CTA (5s): Encourage to follow for more, in Malayalam
  - Use simple everyday Malayalam words
  - Add natural commas for breathing pauses
  - Sound like a passionate Kerala YouTube coding teacher
  - No hashtags, no emojis, no labels

Output ONLY these 2 lines. No labels, no extra text.
"""

    response = client.chat.completions.create(
        model="openrouter/auto",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=700
    )

    lines = response.choices[0].message.content.strip().split("\n", 1)
    caption = lines[0].strip()
    voice_script = lines[1].strip() if len(lines) > 1 else caption

    print(f"[ENGINE] ✅ Caption: {caption}", flush=True)
    print(f"[ENGINE] ✅ Script words: {len(voice_script.split())}", flush=True)
    return {"caption": caption, "voice_script": voice_script}


# ─────────────────────────────────────────────────────────────
# MALAYALAM PREPROCESSOR
# ─────────────────────────────────────────────────────────────
def preprocess_malayalam_script(script: str) -> str:
    replacements = {
        "HTML": "എച്ച് ടി എം എൽ",
        "CSS": "സി എസ് എസ്",
        "JavaScript": "ജാവാസ്ക്രിപ്റ്റ്",
        "Python": "പൈത്തൺ",
        "React": "റിയാക്റ്റ്",
        "Node.js": "നോഡ് ജെ എസ്",
        "API": "എ പി ഐ",
        "SQL": "എസ് ക്യൂ എൽ",
        "JSON": "ജെ സൺ",
        "Git": "ഗിറ്റ്",
        "GitHub": "ഗിറ്റ്ഹബ്",
        "VS Code": "വി എസ് കോഡ്",
        "UI": "യൂ ഐ",
        "UX": "യൂ എക്സ്",
        "URL": "യൂ ആർ എൽ",
        "HTTP": "എച്ച് ടി ടി പി",
        "HTTPS": "എച്ച് ടി ടി പി എസ്",
        "DOM": "ഡോം",
        "CPU": "സി പി യൂ",
        "GPU": "ജി പി യൂ",
        "RAM": "റാം",
        "TypeScript": "ടൈപ്പ്സ്ക്രിപ്റ്റ്",
        "MongoDB": "മോംഗോ ഡി ബി",
        "MySQL": "മൈ എസ് ക്യൂ എൽ",
        "Firebase": "ഫയർബേസ്",
        "Bootstrap": "ബൂട്ട്സ്ട്രാപ്പ്",
        "flexbox": "ഫ്ലെക്സ്ബോക്സ്",
        "Flexbox": "ഫ്ലെക്സ്ബോക്സ്",
        "frontend": "ഫ്രണ്ട്എൻഡ്",
        "Frontend": "ഫ്രണ്ട്എൻഡ്",
        "backend": "ബാക്ക്എൻഡ്",
        "Backend": "ബാക്ക്എൻഡ്",
        "fullstack": "ഫുൾസ്റ്റാക്ക്",
        "framework": "ഫ്രെയിംവർക്ക്",
        "Framework": "ഫ്രെയിംവർക്ക്",
        "developer": "ഡെവലപ്പർ",
        "Developer": "ഡെവലപ്പർ",
        "debugging": "ഡീബഗ്ഗിംഗ്",
        "Debugging": "ഡീബഗ്ഗിംഗ്",
        "function": "ഫങ്ഷൻ",
        "Function": "ഫങ്ഷൻ",
        "variable": "വേരിയബിൾ",
        "Variable": "വേരിയബിൾ",
        "array": "അറേ",
        "Array": "അറേ",
        "loop": "ലൂപ്പ്",
        "Loop": "ലൂപ്പ്",
        "class": "ക്ലാസ്സ്",
        "Class": "ക്ലാസ്സ്",
        "object": "ഒബ്ജക്റ്റ്",
        "Object": "ഒബ്ജക്റ്റ്",
        "server": "സെർവർ",
        "Server": "സെർവർ",
        "database": "ഡേറ്റാബേസ്",
        "Database": "ഡേറ്റാബേസ്",
        "website": "വെബ്സൈറ്റ്",
        "Website": "വെബ്സൈറ്റ്",
        "browser": "ബ്രൗസർ",
        "Browser": "ബ്രൗസർ",
        "code": "കോഡ്",
        "Code": "കോഡ്",
        "coding": "കോഡിംഗ്",
        "Coding": "കോഡിംഗ്",
        "programming": "പ്രോഗ്രാമിംഗ്",
        "Programming": "പ്രോഗ്രാമിംഗ്",
    }
    for english, malayalam in replacements.items():
        script = script.replace(english, malayalam)
    return script


# ─────────────────────────────────────────────────────────────
# VOICE GENERATION (EDGE TTS)
# ─────────────────────────────────────────────────────────────
async def generate_voice(script: str) -> str:
    print("[ENGINE] Generating voiceover...", flush=True)

    voice = AGENT_CONFIG["voice"]["tts_voice"]
    audio_path = os.path.join(DATA_DIR, "temp_audio.mp3")

    processed_script = preprocess_malayalam_script(script)
    processed_script = (
        processed_script
        .replace(". ", ".   ")
        .replace("? ", "?   ")
        .replace("! ", "!   ")
        .replace(", ", ",  ")
    )

    communicate = edge_tts.Communicate(
        processed_script,
        voice,
        rate="-10%",
        volume="+15%",
        pitch="+0Hz"
    )
    await communicate.save(audio_path)

    print(f"[ENGINE] ✅ Audio saved: {audio_path}", flush=True)
    return audio_path


# ─────────────────────────────────────────────────────────────
# SLIDESHOW IMAGES
# ─────────────────────────────────────────────────────────────
async def generate_slideshow_images(theme: str, count: int = 8) -> list:
    print(f"[ENGINE] Generating {count} slides...", flush=True)

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
    print(f"[ENGINE] 🎨 Art style: {style_name}", flush=True)

    color_schemes = [
        "deep purple and cyan",
        "electric blue and white",
        "neon green on black",
        "orange and yellow",
        "hot pink and violet",
        "gold and white",
        "red and coral",
        "teal and mint",
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

    seed = random.randint(10000, 99999)

    image_paths = []
    for i in range(count):
        color = color_schemes[i % len(color_schemes)]
        concept = slide_concepts[i % len(slide_concepts)]

        prompt = (
            f"{concept}, {chosen_style}, color palette: {color}, "
            f"professional Instagram reel content, ultra sharp, "
            f"unique variation {seed + i * 7}, "
            f"9:16 vertical portrait orientation, "
            f"no text overlay, no watermarks, no real people"
        )

        try:
            path = _generate_single_image(prompt, f"slide_{i+1}.jpg")
            image_paths.append(path)

        except Exception as e:
            print(f"[ENGINE] ⚠️ Slide {i+1} failed: {e}", flush=True)

            # fallback: copy first successful slide to unique filename
            if image_paths:
                fallback_src = image_paths[0]
                fallback_dest = os.path.join(DATA_DIR, f"slide_{i+1}.jpg")
                shutil.copy2(fallback_src, fallback_dest)
                print(f"[ENGINE] Using fallback copy for slide {i+1}", flush=True)
                image_paths.append(fallback_dest)
            else:
                raise

    print(f"[ENGINE] ✅ All {len(image_paths)} slides ready!", flush=True)
    return image_paths


# ─────────────────────────────────────────────────────────────
# VIDEO CREATION
# ─────────────────────────────────────────────────────────────
def create_reel(image_paths, audio_path: str) -> str:
    print("[ENGINE] Building reel with FFmpeg...", flush=True)

    output_path = os.path.join(DATA_DIR, "reel.mp4")
    slide_duration = 5  # 8 slides -> 40s

    if isinstance(image_paths, str):
        image_paths = [image_paths]

    try:
        if len(image_paths) == 1:
            video_input = ffmpeg.input(image_paths[0], loop=1, t=40, framerate=30)
            video_scaled = (
                video_input
                .filter("scale", 1080, 1920, force_original_aspect_ratio="decrease")
                .filter("pad", 1080, 1920, "(ow-iw)/2", "(oh-ih)/2", color="black")
                .filter("setsar", "1/1")
            )
        else:
            segments = []
            for path in image_paths:
                seg = (
                    ffmpeg
                    .input(path, loop=1, t=slide_duration, framerate=30)
                    .filter("scale", 1080, 1920, force_original_aspect_ratio="decrease")
                    .filter("pad", 1080, 1920, "(ow-iw)/2", "(oh-ih)/2", color="black")
                    .filter("setsar", "1/1")
                    .filter("fade", type="in", start_time=0, duration=0.4)
                    .filter("fade", type="out", start_time=slide_duration - 0.4, duration=0.4)
                )
                segments.append(seg)

            video_scaled = ffmpeg.concat(*segments, v=1, a=0)

        audio_input = ffmpeg.input(audio_path)

        out = ffmpeg.output(
            video_scaled,
            audio_input,
            output_path,
            vcodec="libx264",
            acodec="aac",
            pix_fmt="yuv420p",
            movflags="+faststart",
            r=30,
            video_bitrate="5000k",
            audio_bitrate="192k",
            shortest=None,
            **{"threads": "4", "preset": "fast"},
        )
        ffmpeg.run(out, overwrite_output=True, quiet=False)
        print(f"[ENGINE] ✅ Reel ready: {output_path}", flush=True)
        return output_path

    except ffmpeg.Error as e:
        print(f"[ENGINE] ❌ FFmpeg error: {e.stderr.decode()}", flush=True)
        raise


# ─────────────────────────────────────────────────────────────
# MASTER PIPELINE
# ─────────────────────────────────────────────────────────────
async def run_engine(theme: str) -> dict:
    """Full pipeline: Text → Images → Voice → Slideshow"""
    content = await generate_content(theme)

    results = await asyncio.gather(
        generate_slideshow_images(theme, count=8),
        generate_voice(content["voice_script"]),
    )

    image_paths = results[0]
    audio_path = results[1]

    video_path = create_reel(image_paths, audio_path)

    return {
        "video_path": video_path,
        "caption": content["caption"]
    }