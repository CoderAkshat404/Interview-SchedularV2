"""
Run once to authenticate your Telegram account and save the session to .env.
Usage:  python setup_telegram.py
"""

import asyncio, re, os
from dotenv import load_dotenv

load_dotenv()

api_id   = int(os.environ.get("TELEGRAM_API_ID")   or input("API ID:   ").strip())
api_hash = os.environ.get("TELEGRAM_API_HASH")      or input("API Hash: ").strip()

print(f"\nUsing API ID: {api_id}")
print("Telegram will send a login code to your Telegram app...\n")


async def main():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()

        # Update TELEGRAM_SESSION in .env
        env_path = ".env"
        try:
            content = open(env_path).read()
        except FileNotFoundError:
            content = ""

        if re.search(r"^TELEGRAM_SESSION=.*$", content, re.MULTILINE):
            content = re.sub(r"^TELEGRAM_SESSION=.*$", f"TELEGRAM_SESSION={session_string}",
                             content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\nTELEGRAM_SESSION={session_string}\n"

        with open(env_path, "w") as f:
            f.write(content)

        print("\n✅ Session saved to .env")
        print("Now run:  python app.py")


asyncio.run(main())
