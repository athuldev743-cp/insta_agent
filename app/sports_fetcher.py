# app/sports_fetcher.py
# =====================================================
# SPORTS NEWS FETCHER v4 — IPL & SCHEDULE OPTIMISED
# + IPL Priority Scoring (MI, CSK, RCB, etc.)
# + Time-of-Day awareness (Noon Lineups vs Night Results)
# + Enhanced Freshness for 9am/2pm/9pm Schedule
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
    {"name": "ESPNCricinfo",     "url": "https://www.espncricinfo.com/rss/content/story/feeds/0.xml",     "category": "cricket",  "region": "india",         "priority": 1},
    {"name": "CricBuzz",         "url": "https://www.cricbuzz.com/rss-feeds/cricket-news",                "category": "cricket",  "region": "india",         "priority": 1},
    {"name": "NDTV Cricket",     "url": "https://sports.ndtv.com/feeds/cricket.xml",                      "category": "cricket",  "region": "india",         "priority": 2},
    {"name": "Sportstar Cricket","url": "https://sportstar.thehindu.com/cricket/?service=rss",             "category": "cricket",  "region": "india",         "priority": 2},
    {"name": "TOI Sports",       "url": "https://timesofindia.indiatimes.com/rssfeeds/4719161.cms",        "category": "general",  "region": "india",         "priority": 2},
    {"name": "ISL Official",     "url": "https://www.indiansuperleague.com/rss-feed/news",                "category": "football", "region": "india",         "priority": 1},
    {"name": "NDTV Football",    "url": "https://sports.ndtv.com/feeds/football.xml",                     "category": "football", "region": "india",         "priority": 2},
    {"name": "Sports Tak",       "url": "https://www.sportstak.com/rss/feed.xml",                         "category": "general",  "region": "india",         "priority": 2},
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

PRE_MATCH_KEYWORDS = [
    "probable xi", "lineup", "playing 11", "toss", "preview", 
    "head to head", "injury update", "dream11 team", "match prediction",
    "squad announcement", "clash today", "face off", "likely xi"
]

IPL_TEAMS = [
    "mi", "csk", "rcb", "gt", "lsg", "kkr", "rr", "dc", "pbks", "srh",
    "mumbai indians", "chennai super kings", "royal challengers", "gujarat titans",
    "lucknow super giants", "kolkata knight riders", "rajasthan royals", 
    "delhi capitals", "punjab kings", "sunrisers hyderabad"
]

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 4 — RELEVANCE SCORER
# ═══════════════════════════════════════════════════════════════════════════

def _get_season_priorities() -> dict:
    month   = datetime.now().month
    seasons = AGENT_CONFIG.get("sport_seasons", {})
    return seasons.get(month, {"cricket": 5, "football": 5})

def score_article(article: dict) -> int:
    score = 0
    title_summary = (article.get("title", "") + " " + article.get("summary", "")).lower()
    current_hour = datetime.now().hour # Use local hour for schedule awareness

    # ── IPL & Team Booster (0-55 pts) ──
    is_ipl = "ipl" in title_summary or "indian premier league" in title_summary
    if is_ipl:
        score += 45
    
    team_hits = sum(1 for team in IPL_TEAMS if team in title_summary)
    if team_hits > 0:
        score += 10

    # ── Time-of-Day Logic (0-15 pts) ──
    # Noon (11am - 3pm): Prioritize Lineups & Pre-match
    if 11 <= current_hour <= 15:
        if any(kw in title_summary for kw in PRE_MATCH_KEYWORDS):
            score += 15
            print(f"[SPORTS] 🎯 Pre-match priority detected for slot.", flush=True)
    # Night (8pm - 12am): Prioritize Results
    elif current_hour >= 20 or current_hour <= 1:
        if article.get("is_match_end"):
            score += 15

    # ── India Relevance (0-25) ──
    india_keywords = SPORTS_CONFIG.get("india_keywords", [])
    india_hits = sum(1 for kw in india_keywords if kw in title_summary)
    if india_hits >= 1:
        score += 25
    elif article.get("region") == "india":
        score += 10

    # ── Freshness (0-30) ──
    pub_date = article.get("pub_date")
    if pub_date:
        age_hours = (datetime.now(timezone.utc) - pub_date).total_seconds() / 3600
        if age_hours <= 2: score += 30
        elif age_hours <= 5: score += 20
        elif age_hours <= 12: score += 10
    else:
        score += 5

    # ── Source Quality (0-5) ──
    priority = article.get("priority", 4)
    score += max(0, 6 - priority)

    final = min(100, score)
    article["relevance_score"] = final
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
    raw = []
    for feed_cfg in ALL_FEEDS:
        print(f"[SPORTS] 📡 {feed_cfg['name']}", flush=True)
        raw.extend(_fetch_feed(feed_cfg, max_age_hours))

    seen, deduped = set(), []
    for art in raw:
        key = re.sub(r'\W+', '', art["title"].lower())[:60]
        if key not in seen:
            seen.add(key)
            deduped.append(art)

    for art in deduped:
        score_article(art)

    deduped.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

    print(f"[SPORTS] ✅ {len(deduped)} articles scored. Top: {deduped[0]['relevance_score'] if deduped else 0}", flush=True)
    return deduped

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 6 — SMART STORY SELECTION
# ═══════════════════════════════════════════════════════════════════════════

def get_top_sports_story(
    prefer_match_end: bool = False,
    max_age_hours: int = 24,
    story_slot: int = 1,
) -> Optional[dict]:
    articles = fetch_all_sports_news(max_age_hours)
    if not articles:
        return None

    unposted = [a for a in articles if not _is_on_cooldown(a["url"])]
    if not unposted:
        return None

    if prefer_match_end:
        threshold = SPORTS_CONFIG.get("realtime_score_threshold", 65)
        results   = [a for a in unposted if a["is_match_end"] and a.get("relevance_score", 0) >= threshold]
        if results:
            return results[0]
        return None

    idx = story_slot - 1
    if idx >= len(unposted):
        idx = len(unposted) - 1

    chosen = unposted[idx]
    print(f"[SPORTS] 🏆 Slot {story_slot} (Score={chosen.get('relevance_score',0)}): {chosen['title'][:60]}", flush=True)
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
    if not is_sports_theme(theme): return {}
    content = theme.replace("SPORTS_NEWS:", "").strip()
    parts   = content.split(" | ")
    result  = {}
    if parts: result["title"] = parts[0].strip()
    for part in parts[1:]:
        if ": " in part:
            k, v = part.split(": ", 1)
            result[k.strip()] = v.strip()
    return result