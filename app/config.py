# app/config.py
# =====================================================
# AGENT CONFIGURATION — Web Development Education
# =====================================================

AGENT_CONFIG = {

    # ── ACCOUNT IDENTITY ──────────────────────────────
    "account_niche": "Web Development Education",

    "account_description": (
        "A web development education page that teaches everything "
        "from absolute HTML basics to advanced full-stack concepts. "
        "Daily tips, tutorials, and insights for beginners and "
        "experienced developers alike."
    ),

    "target_audience": (
        "Beginner to intermediate developers, CS students, "
        "self-taught coders, and tech career switchers aged 16-35"
    ),

    "brand_voice": (
        "Friendly, clear, and encouraging — like a senior developer "
        "mentoring a junior. Simple language, practical examples, "
        "no unnecessary jargon."
    ),

    # ── POSTING SCHEDULE ──────────────────────────────
    "post_times": [
        {"hour": 9,  "minute": 0,  "label": "morning"},
        {"hour": 21, "minute": 0,  "label": "evening"},
    ],
    "timezone": "Asia/Kolkata",

    # ── CONTENT THEMES ────────────────────────────────
    # Ordered ground-up: basics → intermediate → advanced
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

    # ── CAPTION STYLE ─────────────────────────────────
    "caption_style": {
        "tone": "Educational, encouraging, and practical",
        "length": "short",
        "use_emojis": True,
        "emoji_count": "2-3",
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

    # ── HASHTAG STRATEGY ──────────────────────────────
    "hashtags": {
        "count": 12,
        "fixed": [
            "#WebDevelopment", "#Coding", "#Programming",
            "#LearnToCode", "#WebDev"
        ],
        "variable": [
            "#HTML", "#CSS", "#JavaScript", "#Python",
            "#React", "#NodeJS", "#Frontend", "#Backend",
            "#FullStack", "#SoftwareEngineering", "#Developer",
            "#CodeNewbie", "#TechEducation", "#100DaysOfCode",
            "#OpenSource", "#GitHub", "#API", "#NextJS",
            "#TypeScript", "#DevTips", "#CodingLife",
            "#SoftwareDeveloper", "#TechCareer", "#CodeDaily"
        ]
    },

    # ── IMAGE STYLE ───────────────────────────────────
    "image_style": {
        "aesthetic": "clean, modern, developer-themed dark mode",
        "colors": "dark background (#0d1117), purple and blue accents, white text elements",
        "mood": "focused, professional, inspiring for coders",
        "elements": (
            "code snippets, terminal windows, browser mockups, "
            "dark IDE themes, subtle grid patterns, glowing text"
        )
    },

    # ── VOICEOVER STYLE ───────────────────────────────
    "voice": {
        "tts_voice": "en-US-AriaNeural",
        # Alternatives:
        # "en-US-GuyNeural"       — American male
        # "en-IN-NeerjaNeural"    — Indian female
        # "en-IN-PrabhatNeural"   — Indian male
        "script_style": (
            "Clear, friendly tech teacher — like a YouTube tutorial "
            "intro. Simple words, one key concept per reel."
        ),
        "script_length": "15 seconds when spoken (about 35-40 words)"
    }
}