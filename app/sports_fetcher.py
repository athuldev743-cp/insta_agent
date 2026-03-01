# app/sports_fetcher.py
# =====================================================
# SPORTS NEWS FETCHER v3
# + AI relevance scorer (India + freshness + season + match-end)
# + story_slot support (slot 1 = top story, slot 2 = second best)
# =====================================================

import re
import os
import json
import feedparser
import requests
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Optional
from app.config import AGENT_CONFIG

SPORTS_CONFIG = AGENT_CONFIG["sports"]
DATA_DIR      = os.path.join(os.path.dirname(__file__), '..', 'data')
COOLDOWN_FILE = os.path.join(DATA_DIR, 'sports_cooldown.json')

SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 1 — OG:IMAGE SCRAPER
# ═══════════════════════════════════════════════════════════════════════════

def get_og_image(url: str, timeout: int = 8) -> Optional[str]:
    if not url:
        return None
    try:
        resp = requests.get(url, headers=SCRAPE_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return None
        html = resp.text
        patterns = [
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']',
            r'<meta\s+name=["\']twitter:image["\']\s+content=["\']([^"\']+)["\']',
            r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']twitter:image["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                img_url = match.group(1).strip()
                skip = ["logo", "favicon", "icon", "placeholder", "default", "blank"]
                if img_url and not any(k in img_url.lower() for k in skip):
                    print(f"[SPORTS] 🖼️  og:image → {img_url[:80]}", flush=True)
                    return img_url
        return None
    except Exception as e:
        print(f"[SPORTS] ⚠️  og:image error: {e}", flush=True)
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 2 — COOLDOWN SYSTEM
# ═══════════════════════════════════════════════════════════════════════════

def _load_cooldown() -> dict:
    try:
        with open(COOLDOWN_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cooldown(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(COOLDOWN_FILE, 'w') as f:
        json.dump(data, f, indent=2)


def _is_on_cooldown(article_url: str) -> bool:
    cooldown_h = SPORTS_CONFIG.get("cooldown_hours", 3)
    data       = _load_cooldown()
    last_post  = data.get(article_url)
    if not last_post:
        return False
    posted_at = datetime.fromisoformat(last_post).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - posted_at < timedelta(hours=cooldown_h)


def mark_as_posted(article_url: str):
    data = _load_cooldown()
    data[article_url] = datetime.now(timezone.utc).isoformat()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    data   = {k: v for k, v in data.items()
              if datetime.fromisoformat(v).replace(tzinfo=timezone.utc) > cutoff}
    _save_cooldown(data)
    print(f"[SPORTS] 📌 Marked posted: {article_url[:60]}", flush=True)


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 3 — RSS FEED DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

ALL_FEEDS = [
    # ── Indian Cricket ────────────────────────────────────────────────────
    {"name": "ESPNCricinfo",     "url": "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",     "category": "cricket",  "region": "india",         "priority": 1},
    {"name": "CricBuzz",         "url": "https://www.cricbuzz.com/rss-feeds/cricket-news",                "category": "cricket",  "region": "india",         "priority": 1},
    {"name": "NDTV Cricket",     "url": "https://sports.ndtv.com/feeds/cricket.xml",                      "category": "cricket",  "region": "india",         "priority": 2},
    {"name": "Sportstar Cricket","url": "https://sportstar.thehindu.com/cricket/?service=rss",             "category": "cricket",  "region": "india",         "priority": 2},
    {"name": "TOI Sports",       "url": "https://timesofindia.indiatimes.com/rssfeeds/4719161.cms",        "category": "general",  "region": "india",         "priority": 2},
    # ── Indian Football / ISL ─────────────────────────────────────────────
    {"name": "ISL Official",     "url": "https://www.indiansuperleague.com/rss-feed/news",                "category": "football", "region": "india",         "priority": 1},
    {"name": "NDTV Football",    "url": "https://sports.ndtv.com/feeds/football.xml",                     "category": "football", "region": "india",         "priority": 2},
    # ── Indian General ────────────────────────────────────────────────────
    {"name": "Sports Tak",       "url": "https://www.sportstak.com/rss/feed.xml",                         "category": "general",  "region": "india",         "priority": 2},
    # ── International ─────────────────────────────────────────────────────
    {"name": "BBC Sport",        "url": "https://feeds.bbci.co.uk/sport/rss.xml",                         "category": "general",  "region": "international", "priority": 3},
    {"name": "BBC Cricket",      "url": "https://feeds.bbci.co.uk/sport/cricket/rss.xml",                 "category": "cricket",  "region": "international", "priority": 3},
    {"name": "BBC Football",     "url": "https://feeds.bbci.co.uk/sport/football/rss.xml",                "category": "football", "region": "international", "priority": 3},
    {"name": "Sky Sports Cricket","url": "https://www.skysports.com/rss/12073",                           "category": "cricket",  "region": "international", "priority": 4},
    {"name": "Sky Sports Football","url":"https://www.skysports.com/rss/12040",                           "category": "football", "region": "international", "priority": 4},
]

MATCH_END_KEYWORDS = [
    "win", "wins", "beat", "beats", "beaten", "defeated",
    "lost", "loses", "match result", "final score",
    "by runs", "by wickets", "by goals", "full-time", "full time",
    "match ends", "series win", "series result", "clinch",
    "ipl result", "odi result", "t20 result", "test result",
    "isl result", "champions trophy result",
    "india won", "india beat", "india lose", "india lost",
    "india clinch", "india knocked out", "stumps", "innings complete",
]


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 4 — AI RELEVANCE SCORER
# ═══════════════════════════════════════════════════════════════════════════

def _get_season_priorities() -> dict:
    """Returns {"cricket": N, "football": N} based on current month."""
    month   = datetime.now().month
    seasons = AGENT_CONFIG.get("sport_seasons", {})
    return seasons.get(month, {"cricket": 5, "football": 5})


def score_article(article: dict) -> int:
    """
    Score an article 0–100 based on relevance to Indian audience.

    Components:
      India Relevance  0–40 pts   (does it involve India/Indian teams/players?)
      Sport Season     0–20 pts   (is this sport currently in peak season?)
      Freshness        0–25 pts   (how recently was it published?)
      Match Result     0–10 pts   (is this a match-end / result article?)
      Source Quality   0–5  pts   (priority 1 source = more trustworthy)
    """
    score = 0
    combined = (article.get("title", "") + " " + article.get("summary", "")).lower()

    # ── India Relevance (0-40) ─────────────────────────────────────────────
    india_keywords = SPORTS_CONFIG.get("india_keywords", [])
    india_hits = sum(1 for kw in india_keywords if kw in combined)

    if india_hits >= 4:
        score += 40
    elif india_hits >= 2:
        score += 28
    elif india_hits == 1:
        score += 15
    elif article.get("region") == "india":
        score += 8   # Indian source but no keyword match
    # else: international story, 0 pts

    # ── Sport Season Bonus (0-20) ──────────────────────────────────────────
    season = _get_season_priorities()
    cat    = article.get("category", "general")

    if cat == "cricket":
        # season priority: 1 = peak IPL (score 20), 9 = off-season (score 4)
        sport_score = max(0, 22 - season.get("cricket", 5) * 2)
        score += min(20, sport_score)
    elif cat == "football":
        sport_score = max(0, 22 - season.get("football", 5) * 2)
        score += min(20, sport_score)
    else:
        score += 5   # general sports

    # ── Freshness (0-25) ──────────────────────────────────────────────────
    pub_date = article.get("pub_date")
    if pub_date:
        age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
        if age_hours <= 1:
            score += 25
        elif age_hours <= 3:
            score += 20
        elif age_hours <= 6:
            score += 15
        elif age_hours <= 12:
            score += 10
        elif age_hours <= 24:
            score += 5
        # older than 24h = 0
    else:
        score += 5   # no date = unknown, give some benefit of the doubt

    # ── Match Result Bonus (0-10) ──────────────────────────────────────────
    if article.get("is_match_end"):
        score += 10

    # ── Source Quality (0-5) ──────────────────────────────────────────────
    priority = article.get("priority", 4)
    score += max(0, 6 - priority)   # priority 1 → +5, priority 4 → +2

    final = min(100, score)
    article["relevance_score"] = final   # attach score to article dict
    return final


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 5 — CORE RSS FETCH LOGIC
# ═══════════════════════════════════════════════════════════════════════════

def _parse_pub_date(entry) -> Optional[datetime]:
    for field in ("published", "updated"):
        raw = getattr(entry, field, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass
    return None


def _is_fresh(pub_date: Optional[datetime], max_hours: int) -> bool:
    if pub_date is None:
        return True
    return datetime.now(timezone.utc) - pub_date < timedelta(hours=max_hours)


def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    for e, c in [("&amp;","&"),("&lt;","<"),("&gt;",">"),("&nbsp;"," "),("&#39;","'")]:
        text = text.replace(e, c)
    return text.strip()


def _is_match_end(title: str, summary: str) -> bool:
    combined = (title + " " + summary).lower()
    return any(kw in combined for kw in MATCH_END_KEYWORDS)


def _rss_thumbnail(entry) -> Optional[str]:
    if hasattr(entry, "media_thumbnail") and entry.media_thumbnail:
        return entry.media_thumbnail[0].get("url")
    if hasattr(entry, "media_content") and entry.media_content:
        return entry.media_content[0].get("url")
    return None


def _fetch_feed(feed_cfg: dict, max_age_hours: int = 24) -> list[dict]:
    articles = []
    try:
        parsed = feedparser.parse(feed_cfg["url"])
        if parsed.bozo and not parsed.entries:
            return []
        for entry in parsed.entries:
            pub_date = _parse_pub_date(entry)
            if not _is_fresh(pub_date, max_age_hours):
                continue
            title   = _clean_text(entry.get("title", ""))
            summary = _clean_text(entry.get("summary", ""))
            url     = entry.get("link", "")
            if not title or not url:
                continue
            articles.append({
                "title":        title,
                "summary":      summary,
                "url":          url,
                "source":       feed_cfg["name"],
                "category":     feed_cfg["category"],
                "region":       feed_cfg["region"],
                "priority":     feed_cfg["priority"],
                "pub_date":     pub_date,
                "is_match_end": _is_match_end(title, summary),
                "image_url":    _rss_thumbnail(entry),
            })
    except Exception as e:
        print(f"[SPORTS] ❌ Feed [{feed_cfg['name']}]: {e}", flush=True)
    return articles


def fetch_all_sports_news(max_age_hours: int = 24) -> list[dict]:
    """
    Fetch all feeds, deduplicate, SCORE every article, sort by score DESC.
    Returns the full ranked list.
    """
    raw = []
    for feed_cfg in ALL_FEEDS:
        print(f"[SPORTS] 📡 {feed_cfg['name']}", flush=True)
        raw.extend(_fetch_feed(feed_cfg, max_age_hours))

    # Deduplicate by normalised title
    seen, deduped = set(), []
    for art in raw:
        key = re.sub(r'\W+', '', art["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            deduped.append(art)

    # Score every article
    for art in deduped:
        score_article(art)

    # Sort by relevance score DESC
    deduped.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    print(
        f"[SPORTS] ✅ {len(deduped)} articles scored. "
        f"Top score: {deduped[0]['relevance_score'] if deduped else 0} "
        f"({deduped[0]['title'][:50] if deduped else 'n/a'})",
        flush=True,
    )
    return deduped


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 6 — SMART STORY SELECTION
# ═══════════════════════════════════════════════════════════════════════════

def get_top_sports_story(
    prefer_match_end: bool = False,
    max_age_hours: int = 24,
    story_slot: int = 1,        # 1 = highest score, 2 = second highest, etc.
) -> Optional[dict]:
    """
    Returns the Nth best unposted sports story (story_slot=1 for top, 2 for second).

    prefer_match_end=True → real-time watcher: only return if score >= threshold
                            AND article is a match result.
    """
    articles = fetch_all_sports_news(max_age_hours)
    if not articles:
        print("[SPORTS] ⚠️  No fresh news.", flush=True)
        return None

    unposted = [a for a in articles if not _is_on_cooldown(a["url"])]
    if not unposted:
        print("[SPORTS] ⚠️  All on cooldown.", flush=True)
        return None

    if prefer_match_end:
        threshold = SPORTS_CONFIG.get("realtime_score_threshold", 65)
        results   = [a for a in unposted if a["is_match_end"] and a.get("relevance_score", 0) >= threshold]
        if results:
            chosen = results[0]
            print(f"[SPORTS] ⚡ Real-time match result (score={chosen['relevance_score']}): {chosen['title'][:60]}", flush=True)
            return chosen
        print("[SPORTS] ℹ️  No qualifying match result this check.", flush=True)
        return None

    # story_slot: 1-indexed pick from ranked list
    idx = story_slot - 1
    if idx >= len(unposted):
        idx = len(unposted) - 1

    chosen = unposted[idx]
    print(
        f"[SPORTS] 🏆 Story slot {story_slot} "
        f"(score={chosen.get('relevance_score',0)}): {chosen['title'][:60]}",
        flush=True,
    )
    return chosen


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 7 — THEME HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def build_sports_theme(article: dict) -> str:
    summary = (article.get("summary") or "")[:200]
    image   = article.get("image_url") or ""
    score   = article.get("relevance_score", 0)
    return (
        f"SPORTS_NEWS: {article['title']} | "
        f"{summary} | "
        f"source: {article['source']} | "
        f"region: {article.get('region','international')} | "
        f"score: {score} | "
        f"image_url: {image} | "
        f"url: {article['url']}"
    )


def is_sports_theme(theme: str) -> bool:
    return theme.startswith("SPORTS_NEWS:")


def parse_sports_theme(theme: str) -> dict:
    if not is_sports_theme(theme):
        return {}
    content = theme.replace("SPORTS_NEWS:", "").strip()
    parts   = content.split(" | ")
    result  = {}
    if parts:
        result["title"] = parts[0].strip()
    for part in parts[1:]:
        if ": " in part:
            k, v = part.split(": ", 1)
            result[k.strip()] = v.strip()
    return result