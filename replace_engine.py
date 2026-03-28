"""
replace_engine.py
Run from project root: python replace_engine.py
Writes the correct engine.py directly - no shell quoting needed.
"""
import os, sys

ENGINE_PATH = os.path.join("app", "engine.py")

NEW_ENGINE = '''\
# app/engine.py  v7.0 - MOVIEPY REMOVED
# Single-pass FFmpeg concat+mux. No temp files. No broken duration headers.
import os, gc, asyncio, subprocess, re
import edge_tts

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
os.makedirs(DATA_DIR, exist_ok=True)


def _probe_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", path]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(r.stdout.strip())
    except Exception:
        return 0.0


async def generate_voice(script):
    audio_path = os.path.join(DATA_DIR, "temp_audio.mp3")
    if os.path.exists(audio_path):
        os.remove(audio_path)
    communicate = edge_tts.Communicate(script, "ml-IN-MidhunNeural", rate="+10%")
    await communicate.save(audio_path)
    await asyncio.sleep(1)
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
        raise RuntimeError("Audio generation failed")
    duration = _probe_duration(audio_path)
    if duration < 1.0:
        raise RuntimeError(f"Audio duration invalid: {duration}s")
    print(f"[ENGINE] Audio ready: {duration:.1f}s ({os.path.getsize(audio_path)//1024} KB)")
    return audio_path


def render_reel(image_paths, audio_path, output_path):
    """Single-pass FFmpeg: concat images + mux audio. No MoviePy."""
    audio_dur = _probe_duration(audio_path)
    if audio_dur < 1.0:
        raise RuntimeError(f"Audio duration invalid: {audio_dur}s")

    n = len(image_paths)
    per_image = audio_dur / n
    print(f"[ENGINE] {n} slides x {per_image:.2f}s = {audio_dur:.1f}s")

    # audio = input 0, images = inputs 1..n
    inputs = ["-i", audio_path]
    for img in image_paths:
        inputs += ["-loop", "1", "-t", str(per_image), "-i", img]

    concat_filter = (
        "".join(f"[{i+1}:v]" for i in range(n))
        + f"concat=n={n}:v=1[v]"
    )

    cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + [
            "-filter_complex", concat_filter,
            "-map", "[v]",
            "-map", "0:a:0",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-b:a", "128k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]
    )

    print("[ENGINE] FFmpeg single-pass render+mux...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg render failed:\\n{result.stderr[-1000:]}")

    final_dur = _probe_duration(output_path)
    print(f"[ENGINE] Reel ready: {final_dur:.1f}s -> {output_path}")
    if final_dur < 1.0:
        raise RuntimeError(f"Output {final_dur}s invalid.\\n{result.stderr[-500:]}")
    gc.collect()


async def generate_content(theme):
    from openai import OpenAI
    try:
        client = OpenAI(
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
        )
        prompt = (
            f"Create Malayalam sports reel content.\\n"
            f"Topic: {theme}\\n\\n"
            f"Output exactly 2 lines:\\n"
            f"LINE 1: Instagram caption with emojis and hashtags.\\n"
            f"LINE 2: Malayalam voiceover script (ONLY Malayalam text, minimum 5 sentences)."
        )
        response = client.chat.completions.create(
            model="openrouter/auto",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
        )
        text = response.choices[0].message.content.strip()
        lines = text.split("\\n", 1)
        caption = re.sub(r"[*_`]+", "", lines[0]).strip()
        script = re.sub(r"[*_`]+", "", lines[1] if len(lines) > 1 else caption).strip()
        return {"caption": caption, "voice_script": script}
    except Exception as e:
        print(f"[ENGINE] LLM failed, using fallback: {e}")
        return {
            "caption": f"Breaking: {theme} | Latest Sports Update #IPL2026 #CricketNews",
            "voice_script": (
                "ഐ പി എൽ 2026 ലെ ഏറ്റവും വലിയ വാർത്ത ഇതാ. "
                "ചെന്നൈ സൂപ്പർ കിംഗ്സ് നായകൻ മഹേന്ദ്ര സിംഗ് ധോണി ആദ്യ രണ്ടാഴ്ച കളിക്കില്ല. "
                "കാൽ മസിൽ വേദനയാണ് ധോണിയെ ബാധിക്കുന്നത്. "
                "ടീം മാനേജ്മെന്റ് ധോണിയുടെ ആരോഗ്യ സ്ഥിതി സസൂക്ഷ്മം നിരീക്ഷിക്കുകയാണ്. "
                "ആരാധകർ ധോണിക്ക് വേണ്ടി പ്രാർത്ഥിക്കുന്നു. "
                "ഐ പി എൽ ലൈവ് അപ്ഡേറ്റുകൾക്ക് ഈ പേജ് ഫോളോ ചെയ്യൂ."
            ),
        }


async def run_engine(theme):
    from app.image_assembler import assemble_sports_slides
    from app.sports_fetcher import fetch_all_sports_news, parse_sports_theme

    sport_data = parse_sports_theme(theme)
    content = await generate_content(theme)
    audio_path = await generate_voice(content["voice_script"])
    all_articles = fetch_all_sports_news(max_age_hours=24)
    image_paths = await assemble_sports_slides(sport_data, all_articles)
    if not image_paths:
        raise ValueError("No images found")
    output_path = os.path.join(DATA_DIR, "reel.mp4")
    render_reel(image_paths, audio_path, output_path)
    return {"video_path": output_path, "caption": content["caption"]}
'''

# ── Write the file ──────────────────────────────────────────
print(f"Writing to: {os.path.abspath(ENGINE_PATH)}")
with open(ENGINE_PATH, "w", encoding="utf-8") as f:
    f.write(NEW_ENGINE)

# ── Verify ─────────────────────────────────────────────────
with open(ENGINE_PATH, encoding="utf-8") as f:
    content = f.read()

ok = True
if "MOVIEPY REMOVED" not in content:
    print("FAIL: header not found"); ok = False
if "filter_complex" not in content:
    print("FAIL: FFmpeg filter_complex not found"); ok = False
if "moviepy" in content.lower() or "ImageClip" in content or "concatenate_videoclips" in content:
    print("FAIL: MoviePy still present!"); ok = False

if ok:
    print("SUCCESS: app/engine.py replaced correctly")
    print("  - No MoviePy imports")
    print("  - FFmpeg single-pass render present")
    print()
    print("Now delete cached bytecode and re-run diagnostics:")
    print("  python -c \"import shutil,os; [shutil.rmtree(r) for r in ['app/__pycache__'] if os.path.exists(r)]\"")
    print("  python diagnose_and_fix.py")
else:
    print("FAILED - check errors above")
    sys.exit(1)