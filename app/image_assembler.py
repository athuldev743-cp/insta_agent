# app/image_assembler.py
# =====================================================
# SPORTS IMAGE ASSEMBLER v2
#
# Slot 1  → AI generated cinematic opener      NVIDIA → Pollinations → HF
# Slot 2  → Best scraped web image             quality filtered + enhanced
# Slot 3  → Best scraped web image             quality filtered + enhanced
# Slot 4  → Best scraped web image             quality filtered + enhanced
# Slot 5  → Best scraped web image             quality filtered + enhanced
#           (missing slots → AI generated sports scene)
# Slot 6  → AI generated key stat card         NVIDIA → Pollinations → HF
# Slot 7  → AI generated match result card     NVIDIA → Pollinations → HF
# Slot 8  → AI generated cinematic outro       NVIDIA → Pollinations → HF
#
# All generated images get PIL text overlay burned in after generation.
# All scraped images go through quality filter + 6-step enhancement chain.
# =====================================================

import os
import re
import time
import urllib.parse
import requests
import numpy as np
from io import BytesIO
from typing import Optional

from PIL import (
    Image, ImageDraw, ImageFont,
    ImageFilter, ImageEnhance, ImageOps,
)
from app.config import AGENT_CONFIG
from app.sports_fetcher import (
    get_og_image,
    SCRAPE_HEADERS,
)

DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

SPORTS_CFG    = AGENT_CONFIG["sports"]
CAROUSEL_PLAN = SPORTS_CFG["carousel"]["slide_plan"]

# Canvas size
W, H = 1080, 1920

# Colour palette — sports broadcast
NAVY    = (8,   18,  48)
GOLD    = (255, 200, 50)
WHITE   = (255, 255, 255)
RED     = (210, 25,  25)
BLACK   = (0,   0,   0)
DKNAVY  = (4,   8,   28)
LTGRAY  = (180, 180, 200)
CRIMSON = (180, 10,  30)


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 1 — FONT LOADER
# ═══════════════════════════════════════════════════════════════════════════

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/Arial Bold.ttf",
        ] if bold else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
    )
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 2 — PIL DRAWING HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _gradient(top: tuple, bottom: tuple, w: int = W, h: int = H) -> Image.Image:
    img  = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _wrap_text(
    draw: ImageDraw.Draw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int, y: int,
    max_w: int,
    fill: tuple,
    spacing: int = 12,
    align: str = "center",
    shadow: bool = False,
) -> int:
    """Draw word-wrapped text with optional drop shadow. Returns end Y."""
    words = text.split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_w:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)

    cy = y
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        lw   = bbox[2] - bbox[0]
        lh   = bbox[3] - bbox[1]
        lx   = x + (max_w - lw) // 2 if align == "center" else x
        if shadow:
            draw.text((lx + 3, cy + 3), ln, font=font, fill=(0, 0, 0, 160))
        draw.text((lx, cy), ln, font=font, fill=fill)
        cy += lh + spacing
    return cy


def _dark_overlay(img: Image.Image, alpha: int = 160) -> Image.Image:
    """Add a semi-transparent dark overlay — improves text readability on photos."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    # Gradient: transparent middle, dark top and bottom
    for y in range(img.height):
        if y < img.height * 0.35:
            a = int(alpha * (1 - y / (img.height * 0.35)))
        elif y > img.height * 0.65:
            a = int(alpha * ((y - img.height * 0.65) / (img.height * 0.35)))
        else:
            a = 0
        draw.line([(0, y), (img.width, y)], fill=(0, 0, 0, a))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def _gold_bar(draw: ImageDraw.Draw, y: int, thickness: int = 6):
    draw.rectangle([(0, y), (W, y + thickness)], fill=GOLD)


def _red_ticker(draw: ImageDraw.Draw, text: str = "🔴 LIVE SPORTS UPDATE  •  Follow for more  •  🔴"):
    draw.rectangle([(0, H - 110), (W, H - 55)], fill=RED)
    f = _font(30)
    draw.text((30, H - 100), text, font=f, fill=WHITE)


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 3 — IMAGE QUALITY FILTER + ENHANCEMENT
# ═══════════════════════════════════════════════════════════════════════════

def _blur_score(img: Image.Image) -> float:
    """
    Laplacian variance — measures sharpness.
    Higher = sharper. Below ~100 is noticeably blurry.
    """
    gray    = img.convert("L")
    arr     = np.array(gray, dtype=np.float32)
    lap     = (
        -arr[:-2, 1:-1] - arr[2:, 1:-1]
        - arr[1:-1, :-2] - arr[1:-1, 2:]
        + 4 * arr[1:-1, 1:-1]
    )
    return float(np.var(lap))


def _is_acceptable_quality(img: Image.Image) -> tuple[bool, str]:
    """
    Balanced quality check — only reject clearly bad images.
    Returns (is_ok, reason_if_rejected).
    """
    # Too small — tiny thumbnail, not usable
    if img.width < 300 or img.height < 200:
        return False, f"too small ({img.width}×{img.height})"

    # Extremely blurry
    score = _blur_score(img)
    if score < 30:
        return False, f"too blurry (score={score:.0f})"

    # Mostly one colour — probably a placeholder/error image
    arr    = np.array(img.convert("RGB"))
    r_std  = np.std(arr[:, :, 0])
    g_std  = np.std(arr[:, :, 1])
    b_std  = np.std(arr[:, :, 2])
    if r_std < 8 and g_std < 8 and b_std < 8:
        return False, "near-solid colour (placeholder?)"

    return True, ""


def _smart_crop(img: Image.Image, target_w: int = W, target_h: int = H) -> Image.Image:
    """
    Crop to 9:16 targeting the most visually interesting region.
    Uses brightness hotspot to find action rather than dead-centre crop.
    """
    target_ratio = target_w / target_h
    img_ratio    = img.width / img.height

    if img_ratio > target_ratio:
        # Landscape → fit height, crop width smartly
        new_h = target_h
        new_w = int(img_ratio * target_h)
        img   = img.resize((new_w, new_h), Image.LANCZOS)

        # Find brightest horizontal region (action tends to be brighter)
        gray      = img.convert("L")
        arr       = np.array(gray)
        col_sums  = arr.sum(axis=0)
        crop_w    = target_w
        best_left = 0
        best_sum  = 0
        step      = max(1, (new_w - crop_w) // 20)
        for left in range(0, new_w - crop_w + 1, step):
            s = col_sums[left:left + crop_w].sum()
            if s > best_sum:
                best_sum  = s
                best_left = left
        # Bias towards centre (avoid extreme edges)
        centre_left = (new_w - crop_w) // 2
        best_left   = int(best_left * 0.3 + centre_left * 0.7)
        img = img.crop((best_left, 0, best_left + crop_w, new_h))

    else:
        # Portrait → fit width, crop height smartly
        new_w = target_w
        new_h = int(target_w / img_ratio)
        img   = img.resize((new_w, new_h), Image.LANCZOS)

        # Find brightest vertical region
        gray     = img.convert("L")
        arr      = np.array(gray)
        row_sums = arr.sum(axis=1)
        crop_h   = target_h
        best_top = 0
        best_sum = 0
        step     = max(1, (new_h - crop_h) // 20)
        for top in range(0, max(1, new_h - crop_h + 1), step):
            s = row_sums[top:top + crop_h].sum()
            if s > best_sum:
                best_sum = s
                best_top = top
        centre_top = (new_h - crop_h) // 2
        best_top   = int(best_top * 0.3 + centre_top * 0.7)
        best_top   = max(0, min(best_top, new_h - crop_h))
        img = img.crop((0, best_top, new_w, best_top + crop_h))

    return img.resize((target_w, target_h), Image.LANCZOS)


def enhance_scraped_image(img: Image.Image) -> Image.Image:
    """
    6-step enhancement chain for scraped web photos.
    Goal: vivid, punchy, broadcast-quality look.
    """
    # 1. Smart crop to 9:16
    img = _smart_crop(img, W, H)

    # 2. Auto levels — stretch histogram for maximum impact
    img = ImageOps.autocontrast(img, cutoff=1)

    # 3. Sharpness — crisp details
    img = ImageEnhance.Sharpness(img).enhance(2.2)

    # 4. Unsharp mask — edge crispness (sports photography style)
    img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=140, threshold=2))

    # 5. Contrast + Color + Brightness — vivid broadcast look
    img = ImageEnhance.Contrast(img).enhance(1.25)
    img = ImageEnhance.Color(img).enhance(1.45)
    img = ImageEnhance.Brightness(img).enhance(1.05)

    # 6. Dark overlay so text on top is always readable
    img = _dark_overlay(img, alpha=140)

    return img


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 4 — AI IMAGE GENERATION (NVIDIA → POLLINATIONS → HF)
# ═══════════════════════════════════════════════════════════════════════════

def _generate_via_pollinations(prompt: str, w: int = W, h: int = H) -> Image.Image:
    """
    Pollinations AI — completely free, no API key required.
    GET https://image.pollinations.ai/prompt/{encoded_prompt}
    Returns a JPEG image directly.
    """
    encoded = urllib.parse.quote(prompt)
    url     = f"https://image.pollinations.ai/prompt/{encoded}?width={w}&height={h}&nologo=true&enhance=true"

    print(f"[IMG] 🌸 Pollinations → requesting...", flush=True)
    resp = requests.get(url, timeout=60)

    if resp.status_code != 200:
        raise RuntimeError(f"Pollinations HTTP {resp.status_code}")

    img = Image.open(BytesIO(resp.content)).convert("RGB")
    if img.width < 100 or img.height < 100:
        raise ValueError("Pollinations returned tiny image")

    print(f"[IMG] ✅ Pollinations: {img.width}×{img.height}", flush=True)
    return img


def _generate_ai_image(prompt: str, slot: int) -> Image.Image:
    """
    3-tier AI image generation:
    1. NVIDIA API   (best quality)
    2. Pollinations (free, no key)
    3. HuggingFace  (free, multiple providers)
    """
    from app.engine import (
        _generate_single_image_nvidia,
        _generate_single_image_hf,
        enhance_image,
    )

    # ── Tier 1: NVIDIA ────────────────────────────────────────────────────
    nvidia_key = os.getenv("NVIDIA_API_KEY", "").strip()
    nvidia_url = os.getenv("NVIDIA_SD_URL", "").strip()

    if nvidia_key and nvidia_url:
        try:
            print(f"[IMG] 🎮 NVIDIA → slot {slot}", flush=True)
            img = _generate_single_image_nvidia(prompt)
            img = enhance_image(img)
            print(f"[IMG] ✅ NVIDIA success slot {slot}", flush=True)
            return img
        except Exception as e:
            print(f"[IMG] ⚠️  NVIDIA failed slot {slot}: {str(e)[:120]}", flush=True)
    else:
        print(f"[IMG] ℹ️  NVIDIA not configured — skipping to Pollinations", flush=True)

    # ── Tier 2: Pollinations ──────────────────────────────────────────────
    try:
        img = _generate_via_pollinations(prompt)
        img = enhance_image(img)
        print(f"[IMG] ✅ Pollinations success slot {slot}", flush=True)
        return img
    except Exception as e:
        print(f"[IMG] ⚠️  Pollinations failed slot {slot}: {str(e)[:120]}", flush=True)

    # ── Tier 3: HuggingFace ───────────────────────────────────────────────
    try:
        print(f"[IMG] 🤗 HuggingFace → slot {slot}", flush=True)
        img = _generate_single_image_hf(prompt)
        img = enhance_image(img)
        print(f"[IMG] ✅ HuggingFace success slot {slot}", flush=True)
        return img
    except Exception as e:
        print(f"[IMG] ❌ All AI providers failed slot {slot}: {e}", flush=True)
        raise RuntimeError(f"All image generation providers failed for slot {slot}")


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 5 — CINEMATIC PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def _build_cinematic_prompt(role: str, article_data: dict) -> str:
    """
    Builds a detailed cinematic photorealistic prompt for each generated slot.
    """
    title    = article_data.get("title", "sports match")
    category = article_data.get("category", "cricket")
    source   = article_data.get("source", "")

    # Detect sport from category + title keywords
    title_lower = title.lower()
    if category == "cricket" or any(w in title_lower for w in ["cricket", "wicket", "runs", "batting", "bowling", "t20", "odi", "test match", "ipl"]):
        sport_scene = (
            "cricket stadium, green pitch under floodlights, "
            "packed crowd cheering, players in white and blue India cricket jerseys, "
            "dramatic stadium lights, cricket ground atmosphere"
        )
        sport_action = "batsman hitting a six, crowd erupting, cricket bat raised in celebration"
    elif category == "football" or any(w in title_lower for w in ["football", "goal", "soccer", "isl", "premier league", "fifa", "uefa"]):
        sport_scene = (
            "football stadium at night, green pitch, "
            "massive crowd with flares and flags, "
            "bright stadium floodlights, football atmosphere"
        )
        sport_action = "footballer scoring a goal, team celebration, crowd going wild"
    else:
        sport_scene  = "modern sports stadium, packed crowd, dramatic floodlights"
        sport_action = "athlete celebrating victory, crowd cheering"

    base_quality = (
        "ultra photorealistic, cinematic photography, "
        "professional sports photography, Canon EOS R5, "
        "f/2.8 aperture, dramatic lighting, "
        "8K resolution, sharp focus, "
        "no text, no watermarks, no logos"
    )

    if role == "opener":
        return (
            f"Cinematic wide-angle shot of {sport_scene}, "
            f"{sport_action}, "
            f"golden hour lighting with lens flare, "
            f"epic atmosphere, sense of triumph and glory, "
            f"9:16 vertical portrait composition, "
            f"{base_quality}"
        )

    elif role == "stat_card_bg":
        return (
            f"Close-up dramatic sports portrait, "
            f"athlete in {sport_scene.split(',')[0]}, "
            f"moody cinematic lighting, dark vignette edges, "
            f"bokeh background with stadium lights, "
            f"intense focused expression, "
            f"9:16 vertical portrait composition, "
            f"{base_quality}"
        )

    elif role == "result_card_bg":
        return (
            f"Triumphant victory celebration scene, "
            f"{sport_action}, "
            f"confetti falling, team jubilation, "
            f"stadium lit up in celebration, "
            f"euphoric atmosphere, warm golden tones, "
            f"9:16 vertical portrait composition, "
            f"{base_quality}"
        )

    elif role == "outro_bg":
        return (
            f"Aerial drone shot of {sport_scene.split(',')[0]}, "
            f"sunset/golden hour, stunning stadium architecture, "
            f"majestic wide establishing shot, "
            f"inspirational and awe-inspiring, "
            f"9:16 vertical portrait composition, "
            f"{base_quality}"
        )

    return f"{sport_scene}, {base_quality}"


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 6 — GENERATED CARD BUILDERS (AI bg + PIL text overlay)
# ═══════════════════════════════════════════════════════════════════════════

def _text_with_shadow(
    draw: ImageDraw.Draw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int, y: int,
    fill: tuple,
    shadow_offset: int = 3,
    shadow_alpha: int = 200,
):
    """Draw text with a drop shadow for readability on photos."""
    # Shadow
    draw.text((x + shadow_offset, y + shadow_offset), text,
              font=font, fill=(0, 0, 0))
    # Main text
    draw.text((x, y), text, font=font, fill=fill)


def build_opener_card(article_data: dict, bg_img: Image.Image) -> Image.Image:
    """
    Slot 1 — Cinematic opener.
    AI-generated stadium background + bold headline overlay.
    """
    # Resize and enhance background
    card = _smart_crop(bg_img, W, H)
    card = _dark_overlay(card, alpha=180)
    draw = ImageDraw.Draw(card)

    title   = article_data.get("title", "Breaking Sports News").upper()
    source  = article_data.get("source", "")
    summary = article_data.get("summary", "")[:100]

    # ── Top: BREAKING badge ────────────────────────────────────────────────
    draw.rectangle([(0, 0), (W, 8)], fill=GOLD)
    f_break = _font(42, bold=True)
    badge   = "  ⚡ BREAKING  "
    bbox    = draw.textbbox((0, 0), badge, font=f_break)
    bw, bh  = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rectangle([(60, 50), (60 + bw + 20, 50 + bh + 16)], fill=RED)
    draw.text((70, 58), badge, font=f_break, fill=WHITE)

    # ── Centre: main headline ──────────────────────────────────────────────
    f_title = _font(74, bold=True)
    _wrap_text(draw, title, f_title, 50, 320, W - 100, WHITE,
               spacing=18, align="center", shadow=True)

    # ── Summary line ──────────────────────────────────────────────────────
    if summary:
        f_sum = _font(38)
        _wrap_text(draw, summary, f_sum, 60, 900, W - 120, LTGRAY,
                   spacing=12, align="center", shadow=True)

    # ── Gold divider ──────────────────────────────────────────────────────
    _gold_bar(draw, 880)

    # ── Source watermark ──────────────────────────────────────────────────
    if source:
        f_src = _font(32)
        draw.text((60, H - 180), f"via {source}", font=f_src, fill=GOLD)

    # ── Red bottom ticker ─────────────────────────────────────────────────
    _red_ticker(draw)

    return card


def build_stat_card(
    bg_img: Image.Image,
    stat_text: str,
    sub_text: str = "",
    slot: int = 6,
) -> Image.Image:
    """
    Slot 6 — Key player stat card.
    AI cinematic close-up background + stat overlay.
    """
    card = _smart_crop(bg_img, W, H)
    card = _dark_overlay(card, alpha=200)
    draw = ImageDraw.Draw(card)

    # Gold top bar
    draw.rectangle([(0, 0), (W, 10)], fill=GOLD)
    draw.rectangle([(0, H - 10), (W, H)], fill=GOLD)

    # Icon
    icon = "📊" if slot == 6 else "🏆"
    f_icon = _font(100)
    draw.text((W // 2 - 60, 200), icon, font=f_icon, fill=WHITE)

    # Stat label
    f_label = _font(44, bold=True)
    label   = "KEY STAT" if slot == 6 else "MATCH RESULT"
    bbox    = draw.textbbox((0, 0), label, font=f_label)
    lw      = bbox[2] - bbox[0]
    draw.rectangle([(W//2 - lw//2 - 20, 420), (W//2 + lw//2 + 20, 470)], fill=RED)
    draw.text((W//2 - lw//2, 425), label, font=f_label, fill=WHITE)

    # Main stat — large gold text
    f_stat = _font(80, bold=True)
    _wrap_text(draw, stat_text, f_stat, 50, 520, W - 100, GOLD,
               spacing=20, align="center", shadow=True)

    # Gold divider
    _gold_bar(draw, 840)

    # Sub text
    if sub_text:
        f_sub = _font(44)
        _wrap_text(draw, sub_text, f_sub, 60, 880, W - 120, WHITE,
                   spacing=14, align="center", shadow=True)

    _red_ticker(draw)
    return card


def build_result_card(
    bg_img: Image.Image,
    article_data: dict,
    result_text: str,
) -> Image.Image:
    """
    Slot 7 — Match result card.
    AI victory celebration background + result overlay.
    """
    card = _smart_crop(bg_img, W, H)
    card = _dark_overlay(card, alpha=190)
    draw = ImageDraw.Draw(card)

    # Gold bars top and bottom
    draw.rectangle([(0, 0), (W, 12)], fill=GOLD)

    # Trophy icon
    f_icon = _font(130)
    draw.text((W // 2 - 75, 160), "🏆", font=f_icon, fill=WHITE)

    # FINAL RESULT banner
    f_label = _font(48, bold=True)
    label   = "FINAL RESULT"
    bbox    = draw.textbbox((0, 0), label, font=f_label)
    lw      = bbox[2] - bbox[0]
    draw.rectangle([
        (W//2 - lw//2 - 30, 400),
        (W//2 + lw//2 + 30, 455)
    ], fill=CRIMSON)
    draw.text((W//2 - lw//2, 408), label, font=f_label, fill=WHITE)

    # Main result text
    f_result = _font(72, bold=True)
    _wrap_text(draw, result_text.upper(), f_result, 50, 510, W - 100, GOLD,
               spacing=18, align="center", shadow=True)

    # Title sub-line
    title = article_data.get("title", "")[:80]
    if title:
        f_sub = _font(40)
        _gold_bar(draw, 840)
        _wrap_text(draw, title, f_sub, 60, 870, W - 120, WHITE,
                   spacing=12, align="center", shadow=True)

    _red_ticker(draw)
    return card


def build_outro_card(
    bg_img: Image.Image,
    account_name: str = "@YourSportsPage",
) -> Image.Image:
    """
    Slot 8 — Cinematic outro / follow CTA.
    AI aerial stadium background + branded CTA overlay.
    """
    card = _smart_crop(bg_img, W, H)
    card = _dark_overlay(card, alpha=200)
    draw = ImageDraw.Draw(card)

    # Gold frame lines
    draw.rectangle([(0, 0), (W, 10)], fill=GOLD)
    draw.rectangle([(0, H - 10), (W, H)], fill=GOLD)
    draw.rectangle([(0, 0), (8, H)], fill=GOLD)
    draw.rectangle([(W - 8, 0), (W, H)], fill=GOLD)

    # Bell icon
    f_icon = _font(140)
    draw.text((W // 2 - 80, 220), "🔔", font=f_icon, fill=WHITE)

    # CTA text
    f_big = _font(88, bold=True)
    _wrap_text(draw, "FOLLOW", f_big, 50, 560, W - 100, GOLD,
               spacing=16, align="center", shadow=True)
    _wrap_text(draw, "FOR DAILY", f_big, 50, 670, W - 100, WHITE,
               spacing=16, align="center", shadow=True)
    _wrap_text(draw, "SPORTS", f_big, 50, 780, W - 100, GOLD,
               spacing=16, align="center", shadow=True)
    _wrap_text(draw, "UPDATES", f_big, 50, 890, W - 100, WHITE,
               spacing=16, align="center", shadow=True)

    # Account handle
    _gold_bar(draw, 1060)
    f_handle = _font(52, bold=True)
    _wrap_text(draw, account_name, f_handle, 50, 1090, W - 100, GOLD,
               spacing=14, align="center", shadow=True)

    # Sport icons row
    f_sports = _font(64)
    draw.text((W // 2 - 120, 1250), "🏏  ⚽  🏆", font=f_sports, fill=WHITE)

    _red_ticker(draw, "🔴 LIVE SPORTS  •  CRICKET  •  FOOTBALL  •  FOLLOW NOW  🔴")
    return card


def build_scraped_photo_card(
    img: Image.Image,
    source_name: str = "",
) -> Image.Image:
    """
    Slots 2–5 — Enhanced scraped web photo with source badge.
    """
    card = enhance_scraped_image(img)
    draw = ImageDraw.Draw(card)

    # Source badge top-left
    if source_name:
        f_src  = _font(34, bold=True)
        badge  = f"  {source_name.upper()}  "
        bbox   = draw.textbbox((0, 0), badge, font=f_src)
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.rounded_rectangle([(28, 28), (28 + bw + 20, 28 + bh + 16)],
                                radius=10, fill=RED)
        draw.text((38, 36), badge, font=f_src, fill=WHITE)

    _red_ticker(draw)
    return card


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 7 — MULTI-SOURCE WEB IMAGE COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════

def _extract_keywords(title: str) -> list[str]:
    stopwords = {
        "the","a","an","and","or","but","in","on","at","to","for","of",
        "with","by","from","is","was","are","were","win","wins","beat",
        "beats","vs","match","game","series","their","its","after","before",
    }
    words    = re.findall(r'\b[A-Za-z]{3,}\b', title)
    keywords = [w for w in words if w.lower() not in stopwords]
    caps     = [w for w in keywords if w[0].isupper()]
    others   = [w for w in keywords if not w[0].isupper()]
    return (caps + others)[:6]


def _same_story_articles(primary: dict, all_articles: list[dict], max_results: int = 6) -> list[dict]:
    keywords       = _extract_keywords(primary["title"])
    primary_source = primary["source"]
    matches        = []
    for art in all_articles:
        if art["source"] == primary_source or art["url"] == primary["url"]:
            continue
        hits = sum(1 for kw in keywords if kw.lower() in art["title"].lower())
        if hits >= 2:
            matches.append((hits, art))
    matches.sort(key=lambda x: x[0], reverse=True)
    return [a for _, a in matches[:max_results]]


def _download_image(url: str) -> Optional[Image.Image]:
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
        if resp.status_code == 200:
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            return img
    except Exception as e:
        print(f"[ASSEMBLER] ⚠️  Download failed ({url[:50]}): {e}", flush=True)
    return None


def collect_web_images(
    primary_article: dict,
    all_articles: list[dict],
    needed: int = 4,
) -> list[tuple[Image.Image, str]]:
    """
    Collects up to `needed` quality-filtered web images from different sources.
    Returns list of (PIL Image, source_name) tuples.
    """
    results      = []
    sources_used = set()

    def _try_article(article: dict) -> Optional[tuple]:
        src = article["source"]
        if src in sources_used:
            return None

        # Get image URL — RSS thumbnail or og:image scrape
        img_url = article.get("image_url") or get_og_image(article["url"])
        if not img_url:
            return None

        img = _download_image(img_url)
        if img is None:
            return None

        # Quality check
        ok, reason = _is_acceptable_quality(img)
        if not ok:
            print(f"[ASSEMBLER] ⚠️  Rejected image from {src}: {reason}", flush=True)
            return None

        sources_used.add(src)
        print(f"[ASSEMBLER] ✅ Web image from {src} ({img.width}×{img.height})", flush=True)
        return (img, src)

    # Try primary article first
    result = _try_article(primary_article)
    if result:
        results.append(result)

    # Try same-story articles from other sources
    related = _same_story_articles(primary_article, all_articles, max_results=needed * 3)
    for art in related:
        if len(results) >= needed:
            break
        result = _try_article(art)
        if result:
            results.append(result)

    print(f"[ASSEMBLER] 🖼️  Collected {len(results)}/{needed} quality web images", flush=True)
    return results


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 8 — STAT EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════

def _extract_stats(article_data: dict) -> tuple[str, str]:
    """
    Extract the most meaningful stat and result from article title/summary.
    Returns (stat_text, result_text).
    """
    title   = article_data.get("title", "")
    summary = article_data.get("summary", "")
    combined = title + " " + summary

    # Look for score patterns: "97 off 50", "6 wickets", "3-1", "2-0"
    score_patterns = [
        r'\d+\*?\s+(?:off|from)\s+\d+\s+balls?',   # "97* off 50 balls"
        r'\d+\s+(?:runs?|wickets?|goals?|points?)', # "6 wickets"
        r'\d+-\d+',                                  # "3-1"
        r'by\s+\d+\s+\w+',                          # "by 6 wickets"
    ]
    stat_text = ""
    for pattern in score_patterns:
        match = re.search(pattern, combined, re.I)
        if match:
            stat_text = match.group(0).upper()
            break
    if not stat_text:
        # Fallback: first number + context
        nums = re.findall(r'\d+', combined)
        stat_text = nums[0] if nums else "KEY STAT"

    # Result text — first 60 chars of title
    result_text = title[:60] if title else "MATCH RESULT"

    return stat_text, result_text


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 9 — MAIN ASSEMBLER
# ═══════════════════════════════════════════════════════════════════════════

def _save(img: Image.Image, filename: str) -> str:
    path = os.path.join(DATA_DIR, filename)
    img.save(path, format="JPEG", quality=95, optimize=True)
    return path


async def assemble_sports_slides(
    article_data: dict,
    all_articles: list[dict],
) -> list[str]:
    """
    Builds 8 JPEG slides for a sports post.
    Returns list of 8 file paths — same format as engine.generate_slideshow_images().
    """
    title   = article_data.get("title", "Sports Update")
    account = os.getenv("IG_ACCOUNT_NAME", "@YourSportsPage")

    print(f"[ASSEMBLER] 🏗️  Building 8 slides: {title[:60]}", flush=True)

    # ── Step 1: Extract stats ──────────────────────────────────────────────
    stat_text, result_text = _extract_stats(article_data)
    print(f"[ASSEMBLER] 📊 Stat: {stat_text} | Result: {result_text[:40]}", flush=True)

    # ── Step 2: Collect web images for slots 2–5 ──────────────────────────
    web_images = collect_web_images(article_data, all_articles, needed=4)

    # ── Step 3: Generate AI backgrounds for slots 1, 6, 7, 8 in parallel ──
    import asyncio

    async def _gen(role: str, slot: int) -> Optional[Image.Image]:
        prompt = _build_cinematic_prompt(role, article_data)
        loop   = asyncio.get_event_loop()
        try:
            # Run blocking AI generation in thread executor
            img = await loop.run_in_executor(None, _generate_ai_image, prompt, slot)
            return img
        except Exception as e:
            print(f"[ASSEMBLER] ❌ AI gen failed slot {slot}: {e}", flush=True)
            return None

    print(f"[ASSEMBLER] 🤖 Generating AI backgrounds for slots 1,6,7,8 in parallel...", flush=True)
    opener_bg, stat_bg, result_bg, outro_bg = await asyncio.gather(
        _gen("opener",          slot=1),
        _gen("stat_card_bg",    slot=6),
        _gen("result_card_bg",  slot=7),
        _gen("outro_bg",        slot=8),
    )

    # ── Step 4: Build all 8 slides ─────────────────────────────────────────
    slide_paths = []

    for slot_cfg in CAROUSEL_PLAN:
        slot = slot_cfg["slot"]
        role = slot_cfg["role"]

        print(f"[ASSEMBLER] 🖼️  Slot {slot}: {role}", flush=True)
        img = None

        # ── Slot 1: Cinematic opener ───────────────────────────────────────
        if role == "headline_card":
            if opener_bg:
                img = build_opener_card(article_data, opener_bg)
            else:
                # Fallback: gradient card
                bg  = _gradient(NAVY, DKNAVY)
                img = build_opener_card(article_data, bg)

        # ── Slots 2–5: Quality-filtered web photos ─────────────────────────
        elif slot_cfg["type"] == "scraped":
            scrape_idx = slot - 2   # slots 2,3,4,5 → index 0,1,2,3
            if scrape_idx < len(web_images):
                web_img, src_name = web_images[scrape_idx]
                img = build_scraped_photo_card(web_img, source_name=src_name)
                print(f"[ASSEMBLER] ✅ Slot {slot} → web ({src_name})", flush=True)
            else:
                # No web image → generate a sports scene
                print(f"[ASSEMBLER] ⚠️  Slot {slot} → generating fallback...", flush=True)
                try:
                    prompt = _build_cinematic_prompt("opener", article_data)
                    ai_img = await asyncio.get_event_loop().run_in_executor(
                        None, _generate_ai_image, prompt, slot
                    )
                    img = build_scraped_photo_card(ai_img, source_name="AI")
                except Exception:
                    bg  = _gradient(DKNAVY, NAVY)
                    img = build_scraped_photo_card(bg, source_name="Sports Update")

        # ── Slot 6: Key stat card ──────────────────────────────────────────
        elif role == "key_stat_1":
            bg  = stat_bg or _gradient(DKNAVY, NAVY)
            img = build_stat_card(bg, stat_text=stat_text,
                                  sub_text=article_data.get("source",""), slot=6)

        # ── Slot 7: Match result card ──────────────────────────────────────
        elif role == "key_stat_2":
            bg  = result_bg or _gradient(NAVY, DKNAVY)
            img = build_result_card(bg, article_data, result_text=result_text)

        # ── Slot 8: Cinematic outro ────────────────────────────────────────
        elif role == "outro_card":
            bg  = outro_bg or _gradient(DKNAVY, (10, 5, 40))
            img = build_outro_card(bg, account_name=account)

        # ── Save ───────────────────────────────────────────────────────────
        if img:
            path = _save(img, f"slide_{slot}.jpg")
            slide_paths.append(path)
            print(f"[ASSEMBLER] ✅ Saved slide_{slot}.jpg", flush=True)
        else:
            # Should never happen — safety fallback
            bg   = _gradient(NAVY, DKNAVY)
            path = _save(build_opener_card(article_data, bg), f"slide_{slot}.jpg")
            slide_paths.append(path)
            print(f"[ASSEMBLER] ⚠️  Slot {slot} used emergency fallback", flush=True)

    print(f"[ASSEMBLER] 🎉 All {len(slide_paths)} slides ready!", flush=True)
    return slide_paths