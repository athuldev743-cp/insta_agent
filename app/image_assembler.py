# app/image_assembler.py
# =====================================================
# SPORTS IMAGE ASSEMBLER
# Builds 8 slides for a sports post:
#
#  Slot 1  → Generated (headline card)          NVIDIA → HF
#  Slot 2  → Scraped  (main photo, source 1)    og:image
#  Slot 3  → Scraped  (action photo, source 2)  og:image
#  Slot 4  → Scraped  (reaction photo, source 3) og:image
#  Slot 5  → Scraped  (context photo, source 4) og:image
#  Slot 6  → Generated (key stat card 1)        NVIDIA → HF
#  Slot 7  → Generated (key stat card 2/result) NVIDIA → HF
#  Slot 8  → Generated (outro/follow CTA card)  NVIDIA → HF
#
# Scraped slots fall back to NVIDIA → HF if web image unavailable.
# Output: list of 8 JPEG paths (1080×1920) — same format as engine.py
# =====================================================

import os
import re
import shutil
import textwrap
import requests
from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from app.config import AGENT_CONFIG
from app.sports_fetcher import (
    get_og_image,
    fetch_all_sports_news,
    SCRAPE_HEADERS,
    ALL_FEEDS,
)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Target dimensions (Instagram Reel / 9:16)
W, H = 1080, 1920

SPORTS_CFG    = AGENT_CONFIG["sports"]
CAROUSEL_PLAN = SPORTS_CFG["carousel"]["slide_plan"]


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 1 — IMAGE HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _resize_fill(img: Image.Image, w: int = W, h: int = H) -> Image.Image:
    """
    Resize and centre-crop image to exact w×h (cover fill, no black bars).
    """
    img_ratio    = img.width / img.height
    target_ratio = w / h

    if img_ratio > target_ratio:
        # Image is wider → fit height, crop width
        new_h = h
        new_w = int(img_ratio * h)
    else:
        # Image is taller → fit width, crop height
        new_w = w
        new_h = int(w / img_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top  = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _enhance(img: Image.Image) -> Image.Image:
    """Same enhancement chain as engine.py."""
    img = ImageEnhance.Sharpness(img).enhance(1.8)
    img = ImageEnhance.Contrast(img).enhance(1.2)
    img = ImageEnhance.Color(img).enhance(1.3)
    img = ImageEnhance.Brightness(img).enhance(1.05)
    return img


def _save(img: Image.Image, filename: str) -> str:
    path = os.path.join(DATA_DIR, filename)
    img.save(path, format="JPEG", quality=95, optimize=True)
    return path


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a font — tries system fonts, falls back to PIL default."""
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arialbd.ttf",
        ]
        if bold else
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
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


def _draw_wrapped_text(
    draw: ImageDraw.Draw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    max_width: int,
    fill: tuple,
    line_spacing: int = 10,
    align: str = "center",
) -> int:
    """Draw word-wrapped text. Returns the y position after the last line."""
    words  = text.split()
    lines  = []
    line   = ""
    for word in words:
        test = (line + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)

    cur_y = y
    for ln in lines:
        bbox = draw.textbbox((0, 0), ln, font=font)
        lw   = bbox[2] - bbox[0]
        lh   = bbox[3] - bbox[1]
        if align == "center":
            draw.text((x + (max_width - lw) // 2, cur_y), ln, font=font, fill=fill)
        else:
            draw.text((x, cur_y), ln, font=font, fill=fill)
        cur_y += lh + line_spacing

    return cur_y


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 2 — GENERATED CARD BUILDERS (PIL — no AI needed)
# ═══════════════════════════════════════════════════════════════════════════

# Colour palette — sports broadcast dark navy
NAVY   = (10,  20,  50)
GOLD   = (255, 200, 50)
WHITE  = (255, 255, 255)
RED    = (220, 30,  30)
DKGRAY = (30,  30,  50)
LTGRAY = (180, 180, 200)


def _make_gradient_bg(top_color: tuple, bottom_color: tuple) -> Image.Image:
    """Creates a smooth vertical gradient background 1080×1920."""
    img  = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    return img


def build_headline_card(article_data: dict) -> Image.Image:
    """
    Slot 1 — Breaking news headline card.
    Navy gradient, red BREAKING bar, bold headline, source badge.
    """
    title   = article_data.get("title", "Breaking Sports News")
    source  = article_data.get("source", "")
    summary = article_data.get("summary", "")[:120]

    img  = _make_gradient_bg(NAVY, (5, 10, 35))
    draw = ImageDraw.Draw(img)

    # ── Red BREAKING NEWS bar ───────────────────────────────────────────
    bar_h = 90
    bar_y = 180
    draw.rectangle([(0, bar_y), (W, bar_y + bar_h)], fill=RED)
    font_break = _load_font(46, bold=True)
    bbox = draw.textbbox((0, 0), "⚡ BREAKING NEWS", font=font_break)
    bw   = bbox[2] - bbox[0]
    draw.text(((W - bw) // 2, bar_y + 20), "⚡ BREAKING NEWS", font=font_break, fill=WHITE)

    # ── Gold accent line ────────────────────────────────────────────────
    draw.rectangle([(80, bar_y + bar_h + 20), (W - 80, bar_y + bar_h + 25)], fill=GOLD)

    # ── Main headline ───────────────────────────────────────────────────
    font_title = _load_font(72, bold=True)
    _draw_wrapped_text(
        draw, title, font_title,
        x=60, y=bar_y + bar_h + 60,
        max_width=W - 120,
        fill=WHITE, line_spacing=18,
    )

    # ── Summary snippet ─────────────────────────────────────────────────
    if summary:
        font_sum = _load_font(40)
        _draw_wrapped_text(
            draw, summary, font_sum,
            x=60, y=900,
            max_width=W - 120,
            fill=LTGRAY, line_spacing=14,
        )

    # ── Source badge (bottom) ───────────────────────────────────────────
    if source:
        font_src = _load_font(36, bold=True)
        badge_text = f"  {source.upper()}  "
        bbox = draw.textbbox((0, 0), badge_text, font=font_src)
        bw   = bbox[2] - bbox[0]
        bh   = bbox[3] - bbox[1]
        bx   = (W - bw - 20) // 2
        by   = H - 220
        draw.rounded_rectangle([(bx, by), (bx + bw + 20, by + bh + 16)], radius=12, fill=GOLD)
        draw.text((bx + 10, by + 8), badge_text, font=font_src, fill=NAVY)

    # ── Bottom ticker bar ───────────────────────────────────────────────
    draw.rectangle([(0, H - 120), (W, H - 60)], fill=RED)
    font_tick = _load_font(32)
    draw.text((40, H - 110), "🔴 LIVE SPORTS UPDATE  •  Follow for more  •  🔴 LIVE", font=font_tick, fill=WHITE)

    return img


def build_stat_card(stat_text: str, sub_text: str = "", slot: int = 6) -> Image.Image:
    """
    Slots 6 & 7 — Key stat / match result cards.
    Dark card with gold stat highlight.
    """
    img  = _make_gradient_bg((8, 15, 40), NAVY)
    draw = ImageDraw.Draw(img)

    # Gold top accent bar
    draw.rectangle([(0, 0), (W, 12)], fill=GOLD)
    draw.rectangle([(0, H - 12), (W, H)], fill=GOLD)

    # Slot icon
    icon = "📊" if slot == 6 else "🏆"
    font_icon = _load_font(120)
    draw.text((W // 2 - 70, 280), icon, font=font_icon, fill=WHITE)

    # Main stat text
    font_stat = _load_font(80, bold=True)
    _draw_wrapped_text(
        draw, stat_text, font_stat,
        x=60, y=520,
        max_width=W - 120,
        fill=GOLD, line_spacing=20,
    )

    # Sub text
    if sub_text:
        font_sub = _load_font(48)
        _draw_wrapped_text(
            draw, sub_text, font_sub,
            x=60, y=900,
            max_width=W - 120,
            fill=WHITE, line_spacing=14,
        )

    # Divider
    draw.rectangle([(80, 860), (W - 80, 866)], fill=GOLD)

    return img


def build_outro_card(account_name: str = "@YourSportsPage") -> Image.Image:
    """
    Slot 8 — Follow CTA outro card.
    """
    img  = _make_gradient_bg((5, 10, 30), (20, 5, 50))
    draw = ImageDraw.Draw(img)

    # Gold circle accent
    draw.ellipse([(W // 2 - 180, 300), (W // 2 + 180, 660)], outline=GOLD, width=6)
    font_emoji = _load_font(140)
    draw.text((W // 2 - 90, 370), "🏆", font=font_emoji, fill=WHITE)

    # CTA text
    font_big = _load_font(80, bold=True)
    _draw_wrapped_text(draw, "FOLLOW FOR", font_big, 60, 750, W - 120, GOLD, 16)
    _draw_wrapped_text(draw, "DAILY SPORTS", font_big, 60, 860, W - 120, WHITE, 16)
    _draw_wrapped_text(draw, "UPDATES", font_big, 60, 970, W - 120, GOLD, 16)

    font_handle = _load_font(56, bold=True)
    _draw_wrapped_text(draw, account_name, font_handle, 60, 1150, W - 120, LTGRAY, 14)

    # Red bottom bar
    draw.rectangle([(0, H - 130), (W, H - 60)], fill=RED)
    font_tick = _load_font(34)
    draw.text((40, H - 118), "🔴 LIVE UPDATES  •  CRICKET  •  FOOTBALL  •  🏏", font=font_tick, fill=WHITE)

    return img


def build_scraped_photo_card(
    img: Image.Image,
    source_name: str = "",
    slot_label: str = "",
) -> Image.Image:
    """
    Wraps a scraped web photo into a branded 1080×1920 card.
    Adds a source badge and bottom ticker.
    """
    card = _resize_fill(img)
    card = _enhance(card)

    # Dark gradient overlay at top and bottom for text readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)

    # Top fade
    for y in range(200):
        alpha = int(180 * (1 - y / 200))
        ov_draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    # Bottom fade
    for y in range(300):
        alpha = int(220 * (y / 300))
        ov_draw.line([(0, H - 300 + y), (W, H - 300 + y)], fill=(0, 0, 0, alpha))

    card = card.convert("RGBA")
    card = Image.alpha_composite(card, overlay).convert("RGB")
    draw = ImageDraw.Draw(card)

    # Source badge top-left
    if source_name:
        font_src = _load_font(34, bold=True)
        badge = f"  {source_name}  "
        bbox  = draw.textbbox((0, 0), badge, font=font_src)
        bw    = bbox[2] - bbox[0]
        bh    = bbox[3] - bbox[1]
        draw.rounded_rectangle([(30, 30), (30 + bw + 20, 30 + bh + 16)], radius=8, fill=RED)
        draw.text((40, 38), badge, font=font_src, fill=WHITE)

    # Bottom ticker
    draw.rectangle([(0, H - 110), (W, H - 55)], fill=RED)
    font_tick = _load_font(30)
    draw.text((30, H - 100), "🔴 SPORTS UPDATE  •  Follow for live coverage  •  🔴", font=font_tick, fill=WHITE)

    return card


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 3 — MULTI-SOURCE IMAGE COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════

def _extract_match_keywords(title: str) -> list[str]:
    """
    Pull important keywords from article title for cross-source matching.
    E.g. "India beat Australia by 6 wickets" → ["India", "Australia", "wickets"]
    """
    # Remove common filler words
    stopwords = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "is", "was", "are", "were",
        "win", "wins", "beat", "beats", "vs", "match", "game", "series",
        "first", "second", "third", "after", "before", "their", "its",
    }
    words = re.findall(r'\b[A-Za-z]{3,}\b', title)
    keywords = [w for w in words if w.lower() not in stopwords]
    # Prioritise capitalised words (team/player names)
    caps   = [w for w in keywords if w[0].isupper()]
    others = [w for w in keywords if not w[0].isupper()]
    return (caps + others)[:6]


def _articles_about_same_story(
    primary: dict,
    all_articles: list[dict],
    max_results: int = 4,
) -> list[dict]:
    """
    Find other articles from different sources covering the same story.
    Uses keyword overlap on the title.
    """
    keywords = _extract_match_keywords(primary["title"])
    primary_source = primary["source"]
    matches = []

    for art in all_articles:
        if art["source"] == primary_source:
            continue   # skip same source
        if art["url"] == primary["url"]:
            continue

        title_lower = art["title"].lower()
        hits = sum(1 for kw in keywords if kw.lower() in title_lower)
        if hits >= 2:  # at least 2 keyword overlap = same story
            matches.append((hits, art))

    # Sort by overlap count descending, take top N
    matches.sort(key=lambda x: x[0], reverse=True)
    return [art for _, art in matches[:max_results]]


def collect_web_images(
    primary_article: dict,
    all_articles: list[dict],
    needed: int = 4,
) -> list[Optional[Image.Image]]:
    """
    Collects up to `needed` real web images for slots 2–5.
    Strategy:
      1. og:image from primary article
      2. og:images from same-story articles (different sources)
      3. Returns list of PIL Images (or None for failed slots)
    """
    results    = []
    sources_used = set()

    def _download_image(url: str) -> Optional[Image.Image]:
        try:
            resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=10)
            if resp.status_code == 200:
                img = Image.open(BytesIO(resp.content)).convert("RGB")
                if img.width > 100 and img.height > 100:
                    return img
        except Exception as e:
            print(f"[ASSEMBLER] ⚠️  Image download failed ({url[:50]}): {e}", flush=True)
        return None

    def _try_article(article: dict) -> Optional[tuple[Image.Image, str]]:
        """Try to get image from an article. Returns (image, source_name) or None."""
        src = article["source"]
        if src in sources_used:
            return None

        # Try RSS thumbnail first (fast)
        img_url = article.get("image_url")

        # Then og:image scrape
        if not img_url:
            img_url = get_og_image(article["url"])

        if img_url:
            img = _download_image(img_url)
            if img:
                sources_used.add(src)
                print(f"[ASSEMBLER] ✅ Web image from {src}", flush=True)
                return (img, src)
        return None

    # Step 1: primary article image
    result = _try_article(primary_article)
    if result:
        results.append(result)

    # Step 2: same-story articles from other sources
    related = _articles_about_same_story(primary_article, all_articles, max_results=needed * 2)
    for art in related:
        if len(results) >= needed:
            break
        result = _try_article(art)
        if result:
            results.append(result)

    print(f"[ASSEMBLER] 🖼️  Collected {len(results)}/{needed} web images", flush=True)
    return results  # list of (Image, source_name) tuples, may be shorter than needed


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 4 — AI IMAGE GENERATION (imported from engine, used as fallback)
# ═══════════════════════════════════════════════════════════════════════════

def _generate_sports_image_ai(prompt_hint: str, slot: int) -> Optional[Image.Image]:
    """
    Fallback: generate a sports graphic via NVIDIA → HF.
    Imports engine functions directly to avoid code duplication.
    """
    try:
        from app.engine import _generate_single_image_nvidia, _generate_single_image_hf, enhance_image

        style = SPORTS_CFG["image_style"]
        prompt = (
            f"{prompt_hint}, "
            f"{style['aesthetic']}, "
            f"{style['colors']}, "
            f"{style['mood']}, "
            f"{style['elements']}, "
            f"9:16 vertical portrait orientation, ultra detailed, no text overlay, no real people"
        )

        try:
            print(f"[ASSEMBLER] 🤖 NVIDIA fallback for slot {slot}", flush=True)
            img = _generate_single_image_nvidia(prompt)
        except Exception as e:
            print(f"[ASSEMBLER] ⚠️  NVIDIA failed slot {slot}: {e}", flush=True)
            print(f"[ASSEMBLER] 🤖 HF fallback for slot {slot}", flush=True)
            img = _generate_single_image_hf(prompt)

        return enhance_image(img)

    except Exception as e:
        print(f"[ASSEMBLER] ❌ AI generation failed slot {slot}: {e}", flush=True)
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 5 — MAIN ASSEMBLER ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

async def assemble_sports_slides(
    article_data: dict,
    all_articles: list[dict],
) -> list[str]:
    """
    Builds 8 JPEG slides for a sports post.
    Returns list of 8 file paths — same format as engine.generate_slideshow_images().

    article_data: the primary story dict from sports_fetcher
    all_articles: full fetch result (used to find same-story images from other sources)
    """
    import asyncio

    title   = article_data.get("title", "Sports Update")
    summary = article_data.get("summary", "")
    source  = article_data.get("source", "")

    print(f"[ASSEMBLER] 🏗️  Building 8 slides for: {title[:60]}", flush=True)

    # ── Step 1: Collect web images for slots 2–5 ──────────────────────────
    web_images = collect_web_images(article_data, all_articles, needed=4)
    # web_images is list of (PIL Image, source_name) tuples

    # ── Step 2: Parse stats from summary for cards ────────────────────────
    # Extract any numbers/scores for stat cards
    numbers = re.findall(r'\d+(?:\s*(?:runs?|wickets?|goals?|points?|off \d+))?', summary, re.I)
    stat1   = numbers[0] if numbers else title[:50]
    stat2   = summary[:80] if summary else title

    # ── Step 3: Build all 8 slides ────────────────────────────────────────
    slide_paths = []

    for slot_cfg in CAROUSEL_PLAN:
        slot     = slot_cfg["slot"]
        role     = slot_cfg["role"]
        stype    = slot_cfg["type"]
        filename = f"slide_{slot}.jpg"

        print(f"[ASSEMBLER] 🖼️  Slot {slot}: {role} ({stype})", flush=True)

        img = None

        # ── SLOT 1: Headline card (always generated by PIL) ───────────────
        if role == "headline_card":
            img = build_headline_card(article_data)

        # ── SLOTS 2–5: Scraped web photos ────────────────────────────────
        elif stype == "scraped":
            scrape_idx = slot - 2   # slots 2,3,4,5 → index 0,1,2,3
            if scrape_idx < len(web_images):
                web_img, src_name = web_images[scrape_idx]
                img = build_scraped_photo_card(web_img, source_name=src_name, slot_label=role)
                print(f"[ASSEMBLER] ✅ Slot {slot} → web image ({src_name})", flush=True)
            else:
                # No web image available → generate a sports graphic
                print(f"[ASSEMBLER] ⚠️  Slot {slot} → no web image, generating...", flush=True)
                prompt_hint = f"sports action scene related to {title}, dynamic match moment, stadium atmosphere"
                ai_img = _generate_sports_image_ai(prompt_hint, slot)
                if ai_img:
                    img = build_scraped_photo_card(ai_img, source_name="AI Generated", slot_label=role)
                else:
                    # Last resort: copy headline card with different tint
                    img = build_headline_card(article_data)

        # ── SLOT 6: Key stat card ─────────────────────────────────────────
        elif role == "key_stat_1":
            img = build_stat_card(
                stat_text=f"KEY STAT\n{stat1}",
                sub_text=f"from {source}",
                slot=6,
            )

        # ── SLOT 7: Match result / summary card ───────────────────────────
        elif role == "key_stat_2":
            img = build_stat_card(
                stat_text="MATCH RESULT",
                sub_text=stat2,
                slot=7,
            )

        # ── SLOT 8: Outro / follow CTA card ──────────────────────────────
        elif role == "outro_card":
            account = os.getenv("IG_ACCOUNT_NAME", "@YourSportsPage")
            img = build_outro_card(account_name=account)

        # ── Save slide ────────────────────────────────────────────────────
        if img:
            path = _save(img, filename)
            slide_paths.append(path)
            print(f"[ASSEMBLER] ✅ Saved: {filename}", flush=True)
        else:
            # Should never happen but safety fallback
            fallback = build_headline_card(article_data)
            path     = _save(fallback, filename)
            slide_paths.append(path)
            print(f"[ASSEMBLER] ⚠️  Slot {slot} used headline fallback", flush=True)

    print(f"[ASSEMBLER] 🎉 All {len(slide_paths)} slides ready!", flush=True)
    return slide_paths