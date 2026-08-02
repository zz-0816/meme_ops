"""Optional official Telegram Client API collector for public communities.

The collector uses a deployment-secret StringSession and never stores the
Telegram account session, phone number, OTP, or raw messages in the app DB.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone


def mtproto_provider_status() -> dict:
    required = ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_MTPROTO_SESSION")
    allowed = authorized_telegram_handles()
    return {
        "configured": (
            all(os.getenv(name, "").strip() for name in required)
            and bool(allowed)
        ),
        "api_id_configured": bool(os.getenv("TELEGRAM_API_ID", "").strip()),
        "api_hash_configured": bool(os.getenv("TELEGRAM_API_HASH", "").strip()),
        "session_configured": bool(os.getenv("TELEGRAM_MTPROTO_SESSION", "").strip()),
        "authorized_chat_count": len(allowed),
        "mode": "consented-community-client-api",
    }


def normalize_telegram_handle(value: str | None) -> str:
    handle = str(value or "").strip()
    handle = re.sub(r"^https?://(?:www\.)?t\.me/", "", handle, flags=re.I)
    handle = handle.split("?", 1)[0].split("/", 1)[0].strip().lstrip("@")
    return handle if re.fullmatch(r"[A-Za-z0-9_]{4,64}", handle) else ""


def authorized_telegram_handles() -> set[str]:
    """Return only explicitly consented communities; never expose sessions."""
    return {
        handle
        for value in os.getenv("TELEGRAM_MTPROTO_ALLOWED_CHATS", "").split(",")
        if (handle := normalize_telegram_handle(value))
    }


def is_authorized_telegram_handle(value: str | None) -> bool:
    handle = normalize_telegram_handle(value)
    return bool(handle and handle in authorized_telegram_handles())


def _top_terms(texts: list[str], limit: int = 12) -> list[str]:
    ignored = {
        "https", "http", "www", "com", "the", "and", "for", "this",
        "that", "with", "from", "your", "you", "are", "our", "will",
        "token", "coin", "meme",
    }
    counts: Counter[str] = Counter()
    for text in texts:
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,24}|[\u4e00-\u9fff]{2,8}", text):
            normalized = term.lower()
            if normalized not in ignored:
                counts[normalized] += 1
    return [term for term, _ in counts.most_common(limit)]


async def collect_public_telegram_asset(asset: dict) -> dict | None:
    """Collect a bounded aggregate from an explicitly authorized community."""
    handle = normalize_telegram_handle(asset.get("telegram_chat"))
    if (
        not mtproto_provider_status()["configured"]
        or not handle
        or not is_authorized_telegram_handle(handle)
    ):
        return None
    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError as error:
        raise RuntimeError(
            "Telegram MTProto is configured but Telethon is not installed"
        ) from error

    max_messages = max(
        10, min(int(os.getenv("TELEGRAM_MTPROTO_MAX_MESSAGES", "200")), 1000)
    )
    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_MTPROTO_SESSION"].strip()),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"].strip(),
        receive_updates=False,
        connection_retries=2,
    )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    texts: list[str] = []
    authors: set[int] = set()
    posts = views = replies = forwards = reactions = 0
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError(
                "Telegram MTProto session is not authorized; regenerate the sealed session"
            )
        entity = await client.get_entity(handle)
        async for message in client.iter_messages(entity, limit=max_messages):
            message_date = message.date
            if message_date and message_date.tzinfo is None:
                message_date = message_date.replace(tzinfo=timezone.utc)
            if message_date and message_date < cutoff:
                break
            posts += 1
            if message.sender_id:
                authors.add(int(message.sender_id))
            views += int(message.views or 0)
            forwards += int(message.forwards or 0)
            replies += int(getattr(message.replies, "replies", 0) or 0)
            reaction_results = getattr(message.reactions, "results", None) or []
            reactions += sum(int(getattr(item, "count", 0) or 0) for item in reaction_results)
            if message.message:
                texts.append(str(message.message)[:2000])
        members = getattr(entity, "participants_count", None)
        if members is None:
            try:
                participants = await client.get_participants(entity, limit=0)
                members = getattr(participants, "total", None)
            except Exception:
                # Some broadcast channels do not expose a member total to the
                # collector account. Activity metrics remain usable without it.
                members = None
    finally:
        await client.disconnect()

    engagements = replies + forwards + reactions
    return {
        "members": int(members) if members is not None else None,
        "posts_24h": posts,
        "active_authors_24h": len(authors),
        "engagements_24h": engagements,
        "engagement_rate": round(engagements / max(posts, 1), 6),
        "confidence": 0.82,
        "raw_summary": {
            "community": f"@{handle}",
            "provider_window": "24h-bounded-public-sample",
            "sample_size": posts,
            "views_24h_sample": views,
            "replies_24h_sample": replies,
            "forwards_24h_sample": forwards,
            "reactions_24h_sample": reactions,
            "top_terms": _top_terms(texts),
            "privacy_mode": "public-aggregate-no-raw-message-storage",
        },
    }
