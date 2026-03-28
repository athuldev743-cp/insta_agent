# diagnose_and_fix.py
# Run this from your project root: python diagnose_and_fix.py
# It will tell you EXACTLY what is broken and then render a test reel.

import subprocess
import sys
import os
import asyncio

DATA_DIR = os.path.abspath("data")
os.makedirs(DATA_DIR, exist_ok=True)

def run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)

def probe_duration(path):
    r = run(["ffprobe", "-v", "error",
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path])
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0

# ─────────────────────────────────────────────────────────────
print("=" * 60)
print("STEP 1: Check FFmpeg & ffprobe on PATH")
print("=" * 60)

r = run(["ffmpeg", "-version"])
if r.returncode != 0:
    print("❌ FATAL: ffmpeg not found on PATH")
    print("   Install from https://www.gyan.dev/ffmpeg/builds/ and add to PATH")
    sys.exit(1)
ffmpeg_ver = r.stdout.splitlines()[0]
print(f"✅ {ffmpeg_ver}")

r = run(["ffprobe", "-version"])
if r.returncode != 0:
    print("❌ FATAL: ffprobe not found on PATH")
    sys.exit(1)
print(f"✅ ffprobe OK")

# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 2: Check existing data/ files")
print("=" * 60)

audio_path = os.path.join(DATA_DIR, "temp_audio.mp3")
if os.path.exists(audio_path):
    dur = probe_duration(audio_path)
    size = os.path.getsize(audio_path)
    print(f"✅ temp_audio.mp3 exists — size={size//1024}KB  duration={dur:.2f}s")
    if dur < 1.0:
        print("❌ PROBLEM: audio duration is too short — edge-tts likely failed")
else:
    print("⚠️  temp_audio.mp3 not found (will be generated)")

slides = sorted([f for f in os.listdir(DATA_DIR) if f.startswith("slide_") and f.endswith(".jpg")])
print(f"✅ Found {len(slides)} slide images: {slides}")

# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 3: Which engine.py is loaded?")
print("=" * 60)

import app.engine as eng
import inspect
src = inspect.getsource(eng.render_reel)
if "moviepy" in src.lower() or "ImageClip" in src or "concatenate_videoclips" in src:
    print("❌ FATAL: app/engine.py still uses MoviePy!")
    print("   You did NOT save the new engine.py — replace it now.")
    print("   Check that you saved to: app/engine.py  (NOT app/engine copy.py etc)")
    sys.exit(1)
elif "ffmpeg" in src.lower() and "filter_complex" in src:
    print("✅ engine.py is the correct FFmpeg-only version")
else:
    print("⚠️  engine.py content unclear — printing render_reel source:")
    print(src[:500])

# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 4: Generate test audio (edge-tts)")
print("=" * 60)

async def gen_audio():
    import edge_tts
    out = os.path.join(DATA_DIR, "diag_audio.mp3")
    if os.path.exists(out):
        os.remove(out)
    tts = edge_tts.Communicate(
        "ഐ പി എൽ 2026 ൽ മഹേന്ദ്ര സിംഗ് ധോണി ആദ്യ രണ്ടാഴ്ച കളിക്കില്ല എന്ന് റിപ്പോർട്ട്. "
        "കാൽ മസിൽ വേദനയാണ് കാരണം. ചെന്നൈ സൂപ്പർ കിംഗ്സ് ആരാധകർ ആശങ്കയിൽ. "
        "ഐ പി എൽ ലൈവ് അപ്ഡേറ്റുകൾക്ക് ഈ പേജ് ഫോളോ ചെയ്യൂ.",
        "ml-IN-MidhunNeural",
        rate="+10%"
    )
    await tts.save(out)
    await asyncio.sleep(1)
    return out

audio_path = asyncio.run(gen_audio())
dur = probe_duration(audio_path)
size = os.path.getsize(audio_path)
print(f"✅ Audio generated: {dur:.2f}s  {size//1024}KB  → {audio_path}")
if dur < 5.0:
    print(f"❌ PROBLEM: Audio is only {dur:.2f}s — TTS voice too short")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 5: Create test slides")
print("=" * 60)

from PIL import Image, ImageDraw
test_slides = []
colors = [(30,60,120), (120,30,60), (60,120,30), (80,80,30)]
for i, color in enumerate(colors):
    img = Image.new("RGB", (1080, 1920), color=color)
    draw = ImageDraw.Draw(img)
    draw.rectangle([50, 800, 1030, 1120], fill=(255,200,50))
    path = os.path.join(DATA_DIR, f"diag_slide_{i+1}.jpg")
    img.save(path, "JPEG", quality=95)
    test_slides.append(path)
    print(f"  Created {path}")

# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("STEP 6: Render test reel via render_reel()")
print("=" * 60)

output_path = os.path.join(DATA_DIR, "diag_reel.mp4")
if os.path.exists(output_path):
    os.remove(output_path)

try:
    eng.render_reel(test_slides, audio_path, output_path)
except Exception as e:
    print(f"❌ render_reel() raised exception: {e}")
    sys.exit(1)

final_dur = probe_duration(output_path)
final_size = os.path.getsize(output_path)
print(f"\n{'='*60}")
if final_dur > 5.0:
    print(f"✅ SUCCESS! diag_reel.mp4 = {final_dur:.1f}s  {final_size//1024}KB")
    print(f"   Open data/diag_reel.mp4 and verify audio plays.")
    print(f"\n   Your app/engine.py is working correctly.")
    print(f"   The bug was MoviePy in the OLD engine.py.")
else:
    print(f"❌ STILL BROKEN: diag_reel.mp4 = {final_dur:.1f}s")
    print(f"   Run this command manually and paste the full output:")
    n = len(test_slides)
    per = probe_duration(audio_path) / n
    cmd_parts = ["ffmpeg", "-y", "-i", audio_path]
    for s in test_slides:
        cmd_parts += ["-loop", "1", "-t", str(per), "-i", s]
    filt = "".join(f"[{i+1}:v]" for i in range(n)) + f"concat=n={n}:v=1[v]"
    cmd_parts += ["-filter_complex", filt, "-map", "[v]", "-map", "0:a:0",
                  "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-ar", "44100", "-ac", "2", "-b:a", "128k",
                  "-movflags", "+faststart", "-shortest", output_path]
    print("\n   MANUAL CMD:")
    print(" ".join(f'"{x}"' if " " in x else x for x in cmd_parts))
print("=" * 60)