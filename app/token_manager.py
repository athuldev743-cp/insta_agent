import os
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

ENV_PATH = Path(__file__).parent.parent / '.env'


def refresh_long_lived_token() -> str | None:
    """Exchange current token for a fresh 60-day token"""
    print("[TOKEN] Refreshing long-lived token...")

    current_token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    app_id        = os.getenv("INSTAGRAM_APP_ID")
    app_secret    = os.getenv("INSTAGRAM_APP_SECRET")

    if not all([current_token, app_id, app_secret]):
        print("[TOKEN] ❌ Missing env vars — check .env")
        return None

    response = requests.get(
        "https://graph.facebook.com/v20.0/oauth/access_token",
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         app_id,
            "client_secret":     app_secret,
            "fb_exchange_token": current_token
        }
    )

    data = response.json()

    if "access_token" in data:
        new_token = data["access_token"]
        _update_env("INSTAGRAM_ACCESS_TOKEN", new_token)
        print("[TOKEN] ✅ Token refreshed and saved!")
        return new_token
    else:
        print(f"[TOKEN] ❌ Refresh failed: {data}")
        return None


def check_token_validity() -> int:
    """Returns how many days the token has left"""
    token      = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    app_id     = os.getenv("INSTAGRAM_APP_ID")
    app_secret = os.getenv("INSTAGRAM_APP_SECRET")

    response = requests.get(
        "https://graph.facebook.com/debug_token",
        params={
            "input_token":  token,
            "access_token": f"{app_id}|{app_secret}"
        }
    )

    data       = response.json().get("data", {})
    expires_at = data.get("expires_at", 0)

    if not expires_at:
        print("[TOKEN] ✅ Token is permanent (never expires)")
        return 999

    from datetime import datetime
    expiry    = datetime.fromtimestamp(expires_at)
    days_left = (expiry - datetime.now()).days
    print(f"[TOKEN] Expires: {expiry.strftime('%Y-%m-%d')} ({days_left} days left)")
    return days_left


def auto_refresh_if_needed():
    """Call this on startup — refreshes token if under 10 days left"""
    days = check_token_validity()
    if days < 10:
        print(f"[TOKEN] ⚠️  Only {days} days left — auto refreshing...")
        refresh_long_lived_token()
    else:
        print(f"[TOKEN] ✅ Token healthy — {days} days remaining")


def _update_env(key: str, value: str):
    """Update a single key in .env file without wiping other values"""
    env_file = ENV_PATH

    if not env_file.exists():
        env_file.write_text(f"{key}={value}\n")
        return

    lines    = env_file.read_text().splitlines()
    updated  = False
    new_lines = []

    for line in lines:
        if line.startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"{key}={value}")

    env_file.write_text("\n".join(new_lines) + "\n")


if __name__ == "__main__":
    # Run directly to manually check/refresh token
    # python app/token_manager.py
    auto_refresh_if_needed()