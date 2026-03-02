# app/config.py
# =====================================================
# AGENT CONFIGURATION v4 - SPORTS ONLY
# Posts per day: 3 (Sports News Cycle)
#   - 9AM  → Morning News Wrap
#   - 2PM  → Afternoon Sports Story
#   - 8PM  → Evening Prime Story
# + Real-time match watcher every 15 mins
# =====================================================

AGENT_CONFIG = {

    # ── ACCOUNT IDENTITY ──────────────────────────────
    "account_niche": "Sports News",

    "account_description": (
        "Covers the most important sports news of the day — "
        "cricket, football, and more — curated for an Indian audience."
    ),

    "target_audience": (
        "Sports fans across India, especially cricket and ISL followers."
    ),

    "brand_voice": (
        "Authoritative, high-energy, and professional — like a "
        "TV news broadcaster. Direct, factual, and exciting."
    ),

    # ── POSTING SCHEDULE ──────────────────────────────
    "post_times": [
        {
            "label":        "morning_sports",
            "hour":         9,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   1, 
        },
        {
            "label":        "afternoon_sports",
            "hour":         14,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   2, 
        },
        {
            "label":        "evening_sports",
            "hour":         20,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   3, 
        },
    ],
    "timezone": "Asia/Kolkata",

    # ── SEASON AWARENESS ──────────────────────────────
    "sport_seasons": {
        1:  {"cricket": 5, "football": 6}, 
        2:  {"cricket": 5, "football": 6}, 
        3:  {"cricket": 4, "football": 7}, 
        4:  {"cricket": 1, "football": 8},   # Apr: IPL Peak 🔥
        5:  {"cricket": 1, "football": 8},   # May: IPL Peak 🔥
        6:  {"cricket": 3, "football": 7}, 
        7:  {"cricket": 3, "football": 7}, 
        8:  {"cricket": 4, "football": 6}, 
        9:  {"cricket": 4, "football": 5}, 
        10: {"cricket": 5, "football": 3},   # Oct: ISL starts
        11: {"cricket": 5, "football": 2}, 
        12: {"cricket": 5, "football": 2}, 
    },

    # ══════════════════════════════════════════════════
    #  SPORTS CONFIG
    # ══════════════════════════════════════════════════
    "sports": {

        "max_news_age_hours":               24,
        "cooldown_hours":                   3,
        "realtime_check_interval_minutes":  15,
        "realtime_score_threshold":         65,

        "india_keywords": [
            "india", "indian", "bcci", "ipl", "team india",
            "virat", "kohli", "rohit", "sharma", "bumrah",
            "hardik", "pandya", "dhoni", "shubman", "gill",
            "isl", "indian super league", "chennaiyin", "mohun bagan",
            "mumbai city", "bengaluru fc", "kerala blasters",
            "champions trophy", "asia cup", "t20 world cup",
        ],

        "caption_style": {
            "tone":           "Breaking news — urgent, factual, exciting",
            "length":         "short",
            "use_emojis":     True,
            "emoji_count":    "3-4",
            "call_to_action": True,
            "cta_examples": [
                "Follow for live sports updates! 🔴",
                "Save this post! 🔖",
                "Tag a fan! 🏆",
                "Comment your reaction! 👇",
                "Share the news! 📢",
            ]
        },

        "hashtags": {
            "count": 15,
            "fixed": [
                "#Cricket", "#IndianSports", "#SportNews",
                "#BreakingNews", "#SportsUpdate",
            ],
            "variable": [
                "#ICC", "#BCCI", "#IPL", "#IPL2025",
                "#T20", "#T20WorldCup", "#ChampionsTrophy",
                "#TeamIndia", "#IndianCricketTeam",
                "#ISL", "#IndianSuperLeague", "#KeralaBlasters",
                "#UEFA", "#FIFA", "#PremierLeague",
                "#MatchDay", "#CricketFans", "#FootballFans",
                "#LiveCricket", "#CricketHighlights",
                "#SportsFan", "#Football",
            ]
        },

        "image_style": {
            "aesthetic": "professional sports broadcast graphic, breaking news style",
            "colors":    "deep navy background, bold gold and white text, red breaking-news accent bar",
            "mood":      "urgent, high-energy, professional broadcast",
            "elements": (
                "bold headline text centered, "
                "score or key stat highlighted in large font, "
                "clean modern sans-serif typography, "
                "subtle stadium crowd blur in background, "
                "thin red ticker bar at bottom with source name, "
                "no real people faces, no club/board logos, no copyrighted imagery"
            )
        },

        "carousel": {
            "total_slides": 8,
            "slide_plan": [
                {"slot": 1, "role": "headline_card",  "type": "generated",  "desc": "Breaking news headline with score/result"},
                {"slot": 2, "role": "main_photo",     "type": "scraped",    "desc": "Main match photo from primary source"},
                {"slot": 3, "role": "action_photo",   "type": "scraped",    "desc": "Action photo from secondary source"},
                {"slot": 4, "role": "reaction_photo", "type": "scraped",    "desc": "Celebration/reaction photo from third source"},
                {"slot": 5, "role": "context_photo",  "type": "scraped",    "desc": "Pre-match or squad photo from fourth source"},
                {"slot": 6, "role": "key_stat_1",     "type": "stat_card",  "desc": "Top player performance stat"},
                {"slot": 7, "role": "key_stat_2",     "type": "stat_card",  "desc": "Match summary / result card"},
                {"slot": 8, "role": "outro_card",     "type": "generated",  "desc": "Follow us CTA card with branding"},
            ],
            "image_fallback_order": ["scrape", "nvidia", "huggingface"],
        },

        "voice": {
            "tts_voice": "ml-IN-MidhunNeural",
            "script_style": (
                "Professional Malayalam news anchor / sports news reader. "
                "Authoritative, clear, and fast-paced like a TV news broadcast. "
                "Structure: [Opening hook] → [What happened] → [Key stats/score] → [Significance for India] → [Closing line]."
            ),
            "script_length":  "55-65 seconds",
            "script_example": (
                "ഇന്ത്യ ഓസ്‌ട്രേലിയയെ 6 വിക്കറ്റിന് തോൽപ്പിച്ചു. "
                "Rohit Sharma-യുടെ നേതൃത്വത്തിൽ Team India Champions Trophy final ജയിച്ചു."
            ),
        }
    },
}