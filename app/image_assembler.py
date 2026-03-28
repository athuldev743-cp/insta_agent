# app/image_assembler.py  v6.0 — SMART BING IMAGE SEARCH
# Images are fetched by searching the article subject directly on Bing Images.
# No API key needed. Returns exactly 10 slides → 60s reel at 6s/slide.

import os, gc, re, requests
from io import BytesIO
from urllib.parse import quote_plus
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageOps
from app.sports_fetcher import get_og_image, SCRAPE_HEADERS

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
os.makedirs(DATA_DIR, exist_ok=True)

WHITE = (255, 255, 255)
RED   = (210, 25,  25)
BLACK = (0,   0,   0)
TARGET_SLIDES = 10   # 10 slides × 6s = 60s

# ── fonts ─────────────────────────────────────────────────────
def _font(size, bold=False):
    paths = (
        ["C:/Windows/Fonts/arialbd.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
        if bold else
        ["C:/Windows/Fonts/arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    )
    for p in paths:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except: pass
    return ImageFont.load_default()

# ── image processing ──────────────────────────────────────────
def _smart_crop(img, w=1080, h=1920):
    img = img.convert("RGB")
    r = w / h
    ir = img.width / img.height
    if ir > r:
        nw, nh = int(ir * h), h
    else:
        nw, nh = w, int(w / ir)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    l, t = (nw - w) // 2, (nh - h) // 2
    return img.crop((l, t, l + w, t + h))

def _enhance(img):
    img = _smart_crop(img)
    img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageEnhance.Sharpness(img).enhance(1.6)
    ov = Image.new("RGBA", img.size, (0,0,0,0))
    d = ImageDraw.Draw(ov)
    d.rectangle([0, 0, 1080, 500],      fill=(0,0,0,130))
    d.rectangle([0, 1450, 1080, 1920],  fill=(0,0,0,150))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

def _wrap(draw, text, font, max_w):
    words, lines, line = text.split(), [], ""
    for w in words:
        t = (line + " " + w).strip()
        if draw.textbbox((0,0), t, font=font)[2] <= max_w:
            line = t
        else:
            if line: lines.append(line)
            line = w
    if line: lines.append(line)
    return lines

# ── keyword extraction ────────────────────────────────────────
_STOP = {
    "the","this","that","with","from","they","will","have","been","were",
    "their","about","after","could","would","also","just","more","than",
    "into","over","such","both","when","time","said","news","sports",
    "joins","says","gets","match","game","team","play","first","last",
    "next","season","week","year","2025","2026","because","likely","miss",
}

def _build_query(article):
    title = article.get("title", "")
    proper = re.findall(r'\b[A-Z][a-zA-Z]{2,}\b', title)
    all_w  = re.findall(r'\b[a-zA-Z]{3,}\b', title)
    seen, parts = set(), []
    for w in proper + all_w:
        if w.lower() not in _STOP and w.lower() not in seen:
            seen.add(w.lower())
            parts.append(w)
        if len(parts) >= 5:
            break
    tl = title.lower()
    if any(x in tl for x in ["ipl","cricket","dhoni","kohli","ashwin","test","odi","mlc"]):
        parts.append("cricket")
    elif any(x in tl for x in ["isl","football","goal","fifa","premier"]):
        parts.append("football")
    q = " ".join(parts)
    print(f"[ASSEMBLER] Search query: '{q}'")
    return q

# ── Bing image search (no API key) ───────────────────────────
_BING_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.bing.com/",
}

def _bing_search(query, count=20):
    url = (
        "https://www.bing.com/images/search"
        f"?q={quote_plus(query)}&form=HDRSC2&first=1"
    )
    try:
        r = requests.get(url, headers=_BING_HDR, timeout=10)
        if r.status_code != 200:
            print(f"[ASSEMBLER] Bing HTTP {r.status_code}")
            return []
        urls = re.findall(r'"murl":"(https?://[^"]+)"', r.text)
        out, seen = [], set()
        for u in urls:
            ul = u.lower()
            if any(b in ul for b in [".svg",".gif","logo","icon","thumb"]):
                continue
            if u not in seen:
                seen.add(u)
                out.append(u)
            if len(out) >= count:
                break
        print(f"[ASSEMBLER] Bing → {len(out)} URLs")
        return out
    except Exception as e:
        print(f"[ASSEMBLER] Bing failed: {e}")
        return []

def _download(url, min_w=400, min_h=300):
    try:
        r = requests.get(url, headers=_BING_HDR, timeout=8, stream=True)
        if r.status_code != 200:
            return None
        data = b""
        for chunk in r.iter_content(8192):
            data += chunk
            if len(data) > 15 * 1024 * 1024:
                return None
        img = Image.open(BytesIO(data))
        if img.width < min_w or img.height < min_h:
            return None
        return img
    except:
        return None

# ── card builders ─────────────────────────────────────────────
def _ticker(draw):
    draw.rectangle([(0, 1820), (1080, 1920)], fill=RED)
    draw.text((20, 1833), "🔴 LIVE  •  Follow for more sports updates  •  🔴",
              font=_font(30), fill=WHITE)

def build_opener(title, img):
    card = _enhance(img)
    d = ImageDraw.Draw(card)
    d.rectangle([(50, 60), (360, 125)], fill=RED)
    d.text((65, 70), "⚡ BREAKING NEWS", font=_font(40, True), fill=WHITE)
    lines = _wrap(d, title.upper(), _font(66, True), 980)
    y = 190
    for ln in lines[:4]:
        d.text((52, y+2), ln, font=_font(66, True), fill=BLACK)
        d.text((50, y),   ln, font=_font(66, True), fill=WHITE)
        y += 80
    _ticker(d)
    return card

def build_photo(img, num, total, source=""):
    card = _enhance(img)
    d = ImageDraw.Draw(card)
    # slide counter
    d.rectangle([(870, 50), (1050, 115)], fill=(0,0,0,200))
    d.text((885, 58), f"{num}/{total}", font=_font(44, True), fill=WHITE)
    # source badge
    if source:
        s = source.upper()[:16]
        bw = len(s) * 19 + 24
        d.rectangle([(30, 50), (30+bw, 115)], fill=RED)
        d.text((42, 58), s, font=_font(40, True), fill=WHITE)
    _ticker(d)
    return card

# ── master assembly ───────────────────────────────────────────
async def assemble_sports_slides(article_data, all_articles):
    title = article_data.get("title", "Sports News")
    print(f"[ASSEMBLER] Building reel: {title[:60]}")

    query = _build_query(article_data)
    urls  = _bing_search(query, count=TARGET_SLIDES + 10)

    # Download from Bing
    images = []   # (PIL.Image, source_label)
    for url in urls:
        if len(images) >= TARGET_SLIDES:
            break
        img = _download(url)
        if img:
            images.append((img, ""))
            print(f"[ASSEMBLER] ✅ {len(images)}/{TARGET_SLIDES}")

    # Fallback: OG images from related RSS articles
    if len(images) < TARGET_SLIDES:
        print(f"[ASSEMBLER] Bing gave {len(images)}, trying RSS OG images...")
        kw = [w.lower() for w in re.findall(r'\b[A-Z][a-z]{2,}\b', title)]
        seen = set()
        for art in all_articles:
            if len(images) >= TARGET_SLIDES:
                break
            body = (art.get("title","") + " " + art.get("summary","")).lower()
            if not any(k in body for k in kw):
                continue
            u = art.get("image_url") or get_og_image(art["url"])
            if not u or u in seen:
                continue
            seen.add(u)
            img = _download(u)
            if img:
                images.append((img, art.get("source","")))

    # Clone-fill as last resort
    if 0 < len(images) < TARGET_SLIDES:
        base = len(images)
        print(f"[ASSEMBLER] Cloning {base} → {TARGET_SLIDES}")
        idx = 0
        while len(images) < TARGET_SLIDES:
            src, sn = images[idx % base]
            clone = src.copy()
            if idx % 3 == 0:
                clone = clone.transpose(Image.FLIP_LEFT_RIGHT)
            elif idx % 3 == 1:
                clone = ImageEnhance.Brightness(clone).enhance(0.88)
            images.append((clone, sn))
            idx += 1

    if not images:
        raise ValueError("No images found for this topic")

    total = min(len(images), TARGET_SLIDES)
    paths = []
    for i in range(total):
        raw, src = images[i]
        try:
            card = build_opener(title, raw) if i == 0 else build_photo(raw, i+1, total, src)
            p = os.path.join(DATA_DIR, f"slide_{i+1}.jpg")
            card.save(p, "JPEG", quality=92, optimize=True)
            card.close()
            paths.append(p)
        except Exception as e:
            print(f"[ASSEMBLER] Slide {i+1} error: {e}")
        finally:
            try: raw.close()
            except: pass
        gc.collect()

    print(f"[ASSEMBLER] ✅ {len(paths)} slides saved")
    return paths