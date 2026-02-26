import os
import asyncio
import base64
import random
import ffmpeg
import edge_tts
from google import genai
from google.genai import types
from dotenv import load_dotenv
from config import AGENT_CONFIG

load_dotenv()

DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def get_gemini_client():
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def get_next_theme(index: int) -> str:
    themes = AGENT_CONFIG["themes"]
    return themes[index % len(themes)]


def build_hashtags() -> str:
    """Mix fixed + random variable hashtags"""
    fixed = AGENT_CONFIG["hashtags"]["fixed"]
    variable = AGENT_CONFIG["hashtags"]["variable"]
    count = AGENT_CONFIG["hashtags"]["count"]
    
    # How many variable tags to pick
    variable_count = count - len(fixed)
    picked = random.sample(variable, min(variable_count, len(variable)))
    
    all_tags = fixed + picked
    return " ".join(all_tags)


async def generate_content(theme: str) -> dict:
    """Gemini generates caption + voiceover using your account config"""
    print(f"[ENGINE] Generating content for: {theme}")
    
    cfg = AGENT_CONFIG
    cap_style = cfg["caption_style"]
    cta = random.choice(cap_style["cta_examples"]) if cap_style["call_to_action"] else ""
    hashtags = build_hashtags()
    
    client = get_gemini_client()
    
    prompt = f"""
You manage an Instagram account with these details:
- Niche: {cfg['account_niche']}
- Description: {cfg['account_description']}
- Target Audience: {cfg['target_audience']}
- Brand Voice: {cfg['brand_voice']}

Today's theme: "{theme}"

Generate exactly 2 lines:
LINE 1 (Caption): {cap_style['tone']} caption. 
  - Length: {cap_style['length']} 
  - Use {cfg['caption_style']['emoji_count']} emojis: {'yes' if cap_style['use_emojis'] else 'no'}
  - End with this CTA: "{cta}"
  - Then add these hashtags: {hashtags}
  
LINE 2 (Voiceover): {cfg['voice']['script_style']} script.
  - Length: {cfg['voice']['script_length']}
  - Audience: {cfg['target_audience']}
  - No hashtags, natural spoken language only

Output ONLY these 2 lines. No labels, no extra text.
"""
    
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt
    )
    
    lines = response.text.strip().split('\n', 1)
    caption = lines[0].strip()
    voice_script = lines[1].strip() if len(lines) > 1 else caption
    
    print(f"[ENGINE] Caption: {caption}")
    print(f"[ENGINE] Script: {voice_script}")
    return {"caption": caption, "voice_script": voice_script}


async def generate_image(theme: str) -> str:
    """Gemini generates image matching your account aesthetic"""
    print(f"[ENGINE] Generating image for: {theme}")
    
    img_style = AGENT_CONFIG["image_style"]
    client = get_gemini_client()
    
    prompt = (
        f"Create a stunning vertical Instagram reel background image. "
        f"Theme: {theme}. "
        f"Aesthetic: {img_style['aesthetic']}. "
        f"Colors: {img_style['colors']}. "
        f"Mood: {img_style['mood']}. "
        f"Include elements like: {img_style['elements']}. "
        f"Format: 9:16 portrait, photorealistic, ultra high quality, "
        f"no text, no watermarks, no people."
    )
    
    response = client.models.generate_content(
        model="gemini-2.0-flash-preview-image-generation",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"]
        )
    )
    
    image_path = os.path.join(DATA_DIR, 'temp_image.jpg')
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            with open(image_path, 'wb') as f:
                f.write(part.inline_data.data)
            print(f"[ENGINE] Image saved: {image_path}")
            return image_path
    
    raise ValueError("Gemini returned no image.")


async def generate_voice(script: str) -> str:
    """Edge-TTS using voice from config"""
    print("[ENGINE] Generating voiceover...")
    
    voice = AGENT_CONFIG["voice"]["tts_voice"]
    audio_path = os.path.join(DATA_DIR, 'temp_audio.mp3')
    communicate = edge_tts.Communicate(script, voice)
    await communicate.save(audio_path)
    
    print(f"[ENGINE] Audio saved: {audio_path}")
    return audio_path


def create_reel(image_path: str, audio_path: str) -> str:
    """FFmpeg builds Instagram-ready MP4"""
    print("[ENGINE] Building reel...")
    output_path = os.path.join(DATA_DIR, 'reel.mp4')
    
    try:
        video_input = ffmpeg.input(image_path, loop=1, t=15, framerate=30)
        audio_input = ffmpeg.input(audio_path)
        
        video_scaled = (
            video_input
            .filter('scale', 1080, 1920, force_original_aspect_ratio='decrease')
            .filter('pad', 1080, 1920, '(ow-iw)/2', '(oh-ih)/2', color='black')
        )
        
        out = ffmpeg.output(
            video_scaled, audio_input, output_path,
            vcodec='libx264',
            acodec='aac',
            pix_fmt='yuv420p',
            movflags='+faststart',
            r=30,
            shortest=None
        )
        ffmpeg.run(out, overwrite_output=True, quiet=True)
        print(f"[ENGINE] Reel ready: {output_path}")
        return output_path
        
    except ffmpeg.Error as e:
        print(f"[ENGINE] FFmpeg error: {e.stderr.decode()}")
        raise


async def run_engine(theme: str) -> dict:
    """Full pipeline — runs image + voice in parallel"""
    content = await generate_content(theme)
    
    image_path, audio_path = await asyncio.gather(
        generate_image(theme),
        generate_voice(content['voice_script'])
    )
    
    video_path = create_reel(image_path, audio_path)
    return {"video_path": video_path, "caption": content['caption']}