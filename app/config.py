# app/config.py
# =====================================================
# AGENT CONFIGURATION v3
# Posts per day: 3
#   - 9AM  → Web Dev (developer teaching style)
#   - 2PM  → Sports Top Story #1 (Malayalam news reader)
#   - 8PM  → Sports Top Story #2 (Malayalam news reader)
# + Real-time match watcher every 15 mins
# =====================================================

AGENT_CONFIG = {

    # ── ACCOUNT IDENTITY ──────────────────────────────
    "account_niche": "Web Development Education + Sports News",

    "account_description": (
        "A web development education page that teaches everything "
        "from HTML basics to advanced full-stack concepts. "
        "Also covers the most important sports news of the day — "
        "cricket, football, and more — curated for an Indian audience."
    ),

    "target_audience": (
        "Beginner to intermediate developers, CS students, "
        "self-taught coders, and tech career switchers aged 16-35. "
        "Also sports fans across India, especially cricket and ISL followers."
    ),

    # Web dev posts use this voice
    "brand_voice": (
        "Friendly, clear, and encouraging — like a senior developer "
        "mentoring a junior. Simple language, practical examples, "
        "no unnecessary jargon."
    ),

    # ── POSTING SCHEDULE ──────────────────────────────
    "post_times": [
        {
            "label":        "morning_webdev",
            "hour":         9,
            "minute":       0,
            "content_type": "webdev",
        },
        {
            "label":        "afternoon_sports",
            "hour":         14,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   1,   # highest scored unposted story
        },
        {
            "label":        "evening_sports",
            "hour":         20,
            "minute":       0,
            "content_type": "sports",
            "story_slot":   2,   # second highest scored unposted story
        },
    ],
    "timezone": "Asia/Kolkata",

    # ── SEASON AWARENESS ──────────────────────────────
    # Controls which sport gets priority in scoring.
    # Checked by sports_fetcher.py at runtime using current month.
    "sport_seasons": {
        # month (1-12): {"cricket": priority, "football": priority}
        # Lower number = higher priority
        1:  {"cricket": 5, "football": 6},   # Jan: Rabi/tests, ISL active
        2:  {"cricket": 5, "football": 6},   # Feb: Tests/ODIs
        3:  {"cricket": 4, "football": 7},   # Mar: Champions Trophy / ODI season
        4:  {"cricket": 1, "football": 8},   # Apr: IPL starts 🔥
        5:  {"cricket": 1, "football": 8},   # May: IPL peak 🔥
        6:  {"cricket": 3, "football": 7},   # Jun: ICC events possible
        7:  {"cricket": 3, "football": 7},   # Jul: England tours etc.
        8:  {"cricket": 4, "football": 6},   # Aug: Asia Cup possible
        9:  {"cricket": 4, "football": 5},   # Sep: ODI/T20 series
        10: {"cricket": 5, "football": 3},   # Oct: ISL starts 🔥
        11: {"cricket": 5, "football": 2},   # Nov: ISL peak 🔥
        12: {"cricket": 5, "football": 2},   # Dec: ISL + Tests
    },

    # ── WEB DEV THEMES ────────────────────────────────
    "themes": [
        # Basics
        "How HTML works — the skeleton of every website",
        "CSS basics — making websites look beautiful",
        "JavaScript fundamentals every beginner must know",
        "What is the DOM and how browsers render web pages",
        "How to build your first webpage from scratch",
        "Understanding HTML semantic tags and why they matter",
        "CSS Flexbox explained simply with real examples",
        "CSS Grid layout — the most powerful layout tool",
        "JavaScript variables, data types and functions basics",
        "How the internet works — HTTP, DNS and browsers explained",
        # Intermediate
        "Responsive design — making websites work on all screens",
        "JavaScript ES6 features every developer should know",
        "What is React and why developers love it",
        "Understanding APIs — how websites talk to each other",
        "Git and GitHub basics for every developer",
        "CSS animations and transitions to make UI feel alive",
        "Async JavaScript — callbacks, promises and async/await",
        "Node.js explained — JavaScript on the server side",
        "How to use browser DevTools like a pro",
        "Understanding RESTful APIs and JSON data",
        # Advanced
        "TypeScript — why you should stop writing plain JavaScript",
        "Next.js and server-side rendering explained",
        "Web performance optimization tips for faster websites",
        "Database basics — SQL vs NoSQL for web developers",
        "Authentication and JWT tokens explained simply",
        "Docker for web developers — containers made simple",
        "CI/CD pipelines — how pro teams ship code fast",
        "Web security basics — XSS, CSRF and SQL injection",
        "Microservices vs monolith — which architecture to choose",
        "System design basics every senior developer must know",
    ],

    # ══════════════════════════════════════════════════
    #  SPORTS CONFIG
    # ══════════════════════════════════════════════════
    "sports": {

        "max_news_age_hours":               24,
        "cooldown_hours":                   3,
        "realtime_check_interval_minutes":  15,

        # Minimum relevance score to trigger a real-time post
        # (prevents low-quality articles from posting outside schedule)
        "realtime_score_threshold":         65,

        # India-specific keyword boost (used in scorer)
        "india_keywords": [
            "india", "indian", "bcci", "ipl", "team india",
            "virat", "kohli", "rohit", "sharma", "bumrah",
            "hardik", "pandya", "dhoni", "shubman", "gill",
            "isl", "indian super league", "chennaiyin", "mohun bagan",
            "mumbai city", "bengaluru fc", "kerala blasters",
            "champions trophy", "asia cup", "t20 world cup",
        ],

        # ── Caption style ──────────────────────────────
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

        # ── Hashtags ───────────────────────────────────
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

        # ── Image style for generated slides ───────────
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

        # ── 8-Slide carousel config ────────────────────
        "carousel": {
            "total_slides": 8,
            "slide_plan": [
                # slot: what each slide should contain
                # type: "generated" = NVIDIA/HF | "scraped" = og:image from web | "stat_card" = generated text graphic
                {"slot": 1, "role": "headline_card",  "type": "generated",  "desc": "Breaking news headline with score/result"},
                {"slot": 2, "role": "main_photo",     "type": "scraped",    "desc": "Main match photo from primary source"},
                {"slot": 3, "role": "action_photo",   "type": "scraped",    "desc": "Action photo from secondary source"},
                {"slot": 4, "role": "reaction_photo", "type": "scraped",    "desc": "Celebration/reaction photo from third source"},
                {"slot": 5, "role": "context_photo",  "type": "scraped",    "desc": "Pre-match or squad photo from fourth source"},
                {"slot": 6, "role": "key_stat_1",     "type": "stat_card",  "desc": "Top player performance stat"},
                {"slot": 7, "role": "key_stat_2",     "type": "stat_card",  "desc": "Match summary / result card"},
                {"slot": 8, "role": "outro_card",     "type": "generated",  "desc": "Follow us CTA card with branding"},
            ],
            # If scraped image fails → try NVIDIA → if NVIDIA fails → try HuggingFace
            "image_fallback_order": ["scrape", "nvidia", "huggingface"],
        },

        # ── Voice — Malayalam News Reader Style ────────
        # Changed from "teaching" to "news broadcast" style
        "voice": {
            "tts_voice": "ml-IN-MidhunNeural",
            "script_style": (
                "Professional Malayalam news anchor / sports news reader. "
                "Authoritative, clear, and fast-paced like a TV news broadcast. "
                "Reads like a breaking news bulletin — not a conversation. "
                "Structure: [Opening hook] → [What happened] → [Key stats/score] → [Significance for India] → [Closing line]. "
                "Uses formal Malayalam (like Asianet/Manorama news style). "
                "Player names, team names, and scores stay in English. "
                "No filler words. No 'um' or 'so'. Confident and direct."
            ),
            "script_length":  "55-65 seconds when spoken (about 110-130 words)",
            "script_example": (
                "ഇന്ത്യ ഓസ്‌ട്രേലിയയെ 6 വിക്കറ്റിന് തോൽപ്പിച്ചു. "
                "Rohit Sharma-യുടെ നേതൃത്വത്തിൽ Team India Champions Trophy final ജയിച്ചു. "
                "Virat Kohli 87 റൺസ് നേടി Man of the Match ആയി. "
                "ഇത് India-യുടെ ഈ ടൂർണമെന്റിലെ മൂന്നാം കിരീടം. "
                "Sports update-കൾക്ക് follow ചെയ്യൂ."
            ),
        }
    },

    # ══════════════════════════════════════════════════
    #  WEB DEV CONFIG (unchanged — developer teaching style)
    # ══════════════════════════════════════════════════

    "caption_style": {
        "tone":           "Educational, encouraging, and practical",
        "length":         "short",
        "use_emojis":     True,
        "emoji_count":    "2-3",
        "call_to_action": True,
        "cta_examples": [
            "Follow to learn web dev from scratch! 💻",
            "Save this — you'll need it later! 🔖",
            "Share with someone learning to code! 🚀",
            "Comment your questions below! 👇",
            "Follow for daily web dev tips! ⚡",
            "Tag a friend who wants to learn coding! 👨‍💻",
        ]
    },

    "hashtags": {
        "count": 12,
        "fixed": [
            "#WebDevelopment", "#Coding", "#Programming",
            "#LearnToCode", "#WebDev",
        ],
        "variable": [
            "#HTML", "#CSS", "#JavaScript", "#Python",
            "#React", "#NodeJS", "#Frontend", "#Backend",
            "#FullStack", "#SoftwareEngineering", "#Developer",
            "#CodeNewbie", "#TechEducation", "#100DaysOfCode",
            "#OpenSource", "#GitHub", "#API", "#NextJS",
            "#TypeScript", "#DevTips", "#CodingLife",
            "#SoftwareDeveloper", "#TechCareer", "#CodeDaily",
        ]
    },

    "image_style": {
        "aesthetic": "ultra clean dark mode code editor, professional developer workspace",
        "colors":    "pure black background, electric blue syntax highlighting, white clean text, subtle purple accents",
        "mood":      "focused, professional, modern developer aesthetic",
        "elements": (
            "clean readable code snippet centered on screen, "
            "VS Code dark theme interface, "
            "single focused concept shown in code, "
            "subtle grid lines, minimal design, "
            "no clutter, no people, no logos"
        )
    },

    # Web dev voice — teaching style (unchanged)
    "voice": {
        "tts_voice": "ml-IN-MidhunNeural",
        "script_style": (
            "Passionate Malayalam coding teacher. "
            "Warm, clear, encouraging — like a knowledgeable friend explaining a concept. "
            "Structure: [Relatable hook] → [What it is, simply] → [Why it matters] → [Quick example] → [Encouragement + CTA]. "
            "Uses conversational Malayalam. "
            "English technical terms stay in English but are explained in Malayalam. "
            "Friendly tone — not formal, not stiff."
        ),
        "script_length":  "70-80 seconds when spoken (about 140-160 words)",
        "script_example": (
            "CSS Flexbox അറിയാതെ developer ആകാൻ പറ്റില്ല. "
            "ഒരു row-ൽ elements arrange ചെയ്യണോ? justify-content: center മതി. "
            "ഇത് ഒരു line of code — പക്ഷേ hours of frustration save ചെയ്യും. "
            "Save ചെയ്ത് practice ചെയ്തോളൂ. Follow for more! 💻"
        ),
    },
}