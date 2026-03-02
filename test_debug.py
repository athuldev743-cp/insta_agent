import asyncio
from app.engine import generate_voice

async def main():
    await generate_voice("ഐപിഎൽ ഇന്ന് വലിയ മത്സരം ആണ് ജയിച്ചു!", is_sports=True)

asyncio.run(main())