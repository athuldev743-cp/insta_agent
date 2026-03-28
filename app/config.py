# app/config.py
# =====================================================
# AGENT CONFIGURATION v4.1 - IPL & WEB-SCRAPE ONLY
# Posts per day: 3 (Strict IPL Cycle)
#   - 9AM  → Morning News Wrap (Match Review)
#   - 2PM  → Afternoon Lineups (Match Preview)
#   - 9PM  → Evening Prime (Match Result/Live)
# + Real-time match watcher every 20 mins
# =====================================================

AGENT_CONFIG = {

    # ── ACCOUNT IDENTITY ──────────────────────────────
    "account_niche": "IPL & Indian Sports News",

    "account_description": (
        "Real-time IPL 2026 coverage, Indian Cricket updates, and "
        "major sports news curated for the ultimate Indian fan."
    ),

    "target_audience": "Indian Sports Fans, IPL Lovers, Cricket Enthusiasts.",

    "brand_voice": "High-energy, authoritative, and fast-paced TV sports anchor style.",

    # ── POSTING SCHEDULE (IPL OPTIMISED) ──────────────
    "post_times": [
        {
            "label":        "morning_wrap",
            "hour":         9,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   1, 
        },
        {
            "label":        "noon_lineups",
            "hour":         14,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   1, 
        },
        {
            "label":        "night_results",
            "hour":         21,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   1, 
        },
    ],
    "timezone": "Asia/Kolkata",

    # ── SEASON AWARENESS ──────────────────────────────
    "sport_seasons": {
        1:  {"cricket": 5, "football": 6}, 
        2:  {"cricket": 5, "football": 6}, 
        3:  {"cricket": 2, "football": 7}, # IPL Warmup
        4:  {"cricket": 1, "football": 8}, # IPL Peak 🔥
        5:  {"cricket": 1, "football": 8}, # IPL Peak 🔥
        10: {"cricket": 5, "football": 3}, # ISL starts
    },

    # ══════════════════════════════════════════════════
    #  SPORTS CONFIG
    # ══════════════════════════════════════════════════
    "sports": {

        "max_news_age_hours":           18, # Keeping it fresher for IPL
        "cooldown_hours":               3,
        "realtime_check_interval_minutes": 20,
        "realtime_score_threshold":     70,

        "india_keywords": [
            "india", "indian", "bcci", "ipl", "ipl 2026", "team india",
            "virat", "kohli", "rohit", "sharma", "dhoni", "csk", "rcb", "mi",
            "hardik", "gt", "kkr", "srh", "lsg", "rr", "dc", "pbks",
            "probable xi", "playing 11", "toss update", "match result",
            "isl", "kerala blasters", "champions trophy",
        ],

        "caption_style": {
            "tone":           "IPL Buzz — high energy, emojis, urgent",
            "length":         "short",
            "use_emojis":     True,
            "emoji_count":    "4-5",
            "cta_examples": [
                "Follow for LIVE IPL scores! 🏏",
                "Who will win tonight? Comment below! 👇",
                "Share this with a cricket fan! 📢",
                "Team India at its best! 🏆",
            ]
        },

        "hashtags": {
            "count": 20,
            "fixed": ["#IPL2026", "#CricketIndia", "#TeamIndia", "#SportsNews"],
            "variable": [
                "#CSK", "#RCB", "#MumbaiIndians", "#ViratKohli", "#MSDhoni",
                "#IPLHighlights", "#MatchDay", "#CricketStatus", "#MalayalamSports",
                "#IPLNews", "#CricketFans", "#T20Cricket"
            ]
        },

        # ── NO-AI CAROUSEL PLAN ───────────────────────
        "carousel": {
            "total_slides": 8,
            "slide_plan": [
                {"slot": 1, "role": "headline_card",  "type": "scraped_branded", "desc": "News headline on real match photo"},
                {"slot": 2, "role": "match_action",   "type": "scraped",         "desc": "Action photo 1"},
                {"slot": 3, "role": "match_action",   "type": "scraped",         "desc": "Action photo 2"},
                {"slot": 4, "role": "match_action",   "type": "scraped",         "desc": "Action photo 3"},
                {"slot": 5, "role": "match_action",   "type": "scraped",         "desc": "Action photo 4"},
                {"slot": 6, "role": "key_stat_1",     "type": "stat_card",      "desc": "Key player/match stat card"},
                {"slot": 7, "role": "key_stat_2",     "type": "stat_card",      "desc": "Result / Standings card"},
                {"slot": 8, "role": "outro_card",     "type": "scraped_branded", "desc": "Branded follow CTA card"},
            ],
        },

        "voice": {
            "tts_voice": "ml-IN-MidhunNeural",
            "script_style": "Fast-paced TV News Anchor. Clear and exciting Malayalam.",
            "script_length":  "55-60 seconds",
        }
    },
}