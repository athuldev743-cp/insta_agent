import asyncio
from app.engine import generate_content, generate_voice

async def main():
    theme = "IPL match update: West Indies vs India, big win today"
    content = await generate_content(theme)
    print("\nRAW VOICE_SCRIPT:\n", content["voice_script"])
    await generate_voice(content["voice_script"], is_sports=True)

asyncio.run(main())