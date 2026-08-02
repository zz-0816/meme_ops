"""Create a Telegram StringSession locally for the MTProto collector.

Run this only on a trusted machine. Paste the resulting value directly into a
sealed TELEGRAM_MTPROTO_SESSION deployment variable and do not save it in .env,
logs, screenshots, chat messages, or Git.
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import config  # noqa: F401,E402  (loads project-local environment when present)


async def main() -> None:
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as error:
        raise SystemExit(
            "Telethon is not installed. Run: pip install -r backend/requirements.txt"
        ) from error

    api_id = os.getenv("TELEGRAM_API_ID", "").strip() or input("Telegram API ID: ").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip() or getpass.getpass(
        "Telegram API Hash (hidden): "
    ).strip()
    if not api_id or not api_hash:
        raise SystemExit("Telegram API ID and API Hash are required")
    client = TelegramClient(StringSession(), int(api_id), api_hash)
    await client.start()
    try:
        session = client.session.save()
        print("\nTELEGRAM_MTPROTO_SESSION (secret; copy to a sealed Railway variable):")
        print(session)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
