"""Wallet-bound social connections, scheduled collection, and Social RAG.

The collector intentionally uses supported provider APIs.  It does not log in
to X with a browser session or scrape X HTML, which would make credentials and
the production deployment unsafe.  Telegram identity is verified with the
official Login Widget signature; group analytics is collected by the project
bot after an explicit group binding.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from database import get_connection
from telegram_mtproto import (
    collect_public_telegram_asset,
    is_authorized_telegram_handle,
    mtproto_provider_status,
)


X_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
X_API_URL = "https://api.x.com/2"
TELEGRAM_BOT_API = "https://api.telegram.org"
DEFAULT_SCOPES = "tweet.read users.read follows.read offline.access"
TELEGRAM_LOGIN_MAX_AGE_SECONDS = max(
    600, int(os.getenv("TELEGRAM_LOGIN_MAX_AGE_SECONDS", "86400"))
)
SOCIAL_INLINE_TIMEOUT_SECONDS = max(
    2.0, float(os.getenv("SOCIAL_INLINE_TIMEOUT_SECONDS", "8"))
)
_TELEGRAM_BOT_VALIDATION_CACHE: dict[str, Any] = {}
_SOCIAL_DIAGNOSTICS_CACHE: dict[str, tuple[float, dict]] = {}
_BACKGROUND_COLLECTION_TASKS: set[asyncio.Task] = set()
_BACKGROUND_REFRESH_TASKS: dict[tuple[str, str, str], asyncio.Task] = {}


class SocialConfigurationError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _fernet() -> Fernet:
    key = os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY", "").strip()
    if not key:
        raise SocialConfigurationError(
            "SOCIAL_TOKEN_ENCRYPTION_KEY is required before social accounts can be connected"
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as error:
        raise SocialConfigurationError(
            "SOCIAL_TOKEN_ENCRYPTION_KEY must be a URL-safe base64 Fernet key"
        ) from error


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise SocialConfigurationError(
            "Stored social credential cannot be decrypted with the configured key"
        ) from error


def social_provider_status() -> dict:
    encryption_configured = bool(
        os.getenv("SOCIAL_TOKEN_ENCRYPTION_KEY", "").strip()
    )
    x_client_id_configured = bool(os.getenv("X_CLIENT_ID", "").strip())
    x_client_secret_configured = bool(os.getenv("X_CLIENT_SECRET", "").strip())
    x_public_client = os.getenv(
        "X_OAUTH_PUBLIC_CLIENT", "false"
    ).strip().lower() == "true"
    x_bearer_configured = bool(os.getenv("X_BEARER_TOKEN", "").strip())
    telegram_token_configured = bool(
        os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    telegram_username_configured = bool(
        os.getenv("TELEGRAM_BOT_USERNAME", "").strip()
    )
    telegram_webhook_configured = bool(
        os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    )
    return {
        "encryption_configured": encryption_configured,
        "x": {
            "oauth_configured": (
                x_client_id_configured
                and (x_client_secret_configured or x_public_client)
            ),
            "shared_collector_configured": x_bearer_configured,
            "client_id_configured": x_client_id_configured,
            "client_secret_configured": x_client_secret_configured,
            "public_client": x_public_client,
            "bearer_token_configured": x_bearer_configured,
            "mode": "official-api",
        },
        "telegram": {
            "login_configured": (
                telegram_token_configured and telegram_username_configured
            ),
            "bot_token_configured": telegram_token_configured,
            "bot_username_configured": telegram_username_configured,
            "webhook_configured": telegram_webhook_configured,
            "mode": "login-widget-and-bot",
            "auto_webhook": os.getenv("TELEGRAM_AUTO_SET_WEBHOOK", "false").lower() == "true",
            "mtproto": mtproto_provider_status(),
        },
        "scheduler": {
            "enabled": (
                os.getenv("SOCIAL_SCHEDULER_ENABLED", "false").lower() == "true"
                and not demo_social_enabled()
            ),
            "universe_size": max(100, int(os.getenv("SOCIAL_ASSET_UNIVERSE_SIZE", "100"))),
            "interval_seconds": max(300, int(os.getenv("SOCIAL_SCHEDULER_INTERVAL_SECONDS", "900"))),
        },
        "demo_social": {
            "enabled": demo_social_enabled(),
            "limit": max(1, min(int(os.getenv("DEMO_SOCIAL_DATA_LIMIT", "10")), 100)),
            "mode": "synthetic-not-live",
        },
    }


def demo_social_enabled() -> bool:
    """Use explicit configuration, or default on for this Railway demo service."""
    configured = os.getenv("DEMO_SOCIAL_DATA_ENABLED")
    if configured is not None:
        return configured.strip().lower() == "true"
    return bool(
        os.getenv("RAILWAY_ENVIRONMENT_ID")
        or os.getenv("RAILWAY_PROJECT_ID")
    )


def _save_oauth_state(
    owner_address: str,
    provider: str,
    verifier: str | None = None,
    redirect_path: str = "#/settings",
) -> str:
    state = secrets.token_urlsafe(32)
    expires = _utcnow() + timedelta(minutes=10)
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM social_oauth_states WHERE expires_at < ?",
            (_iso(),),
        )
        conn.execute(
            """INSERT INTO social_oauth_states
               (state, owner_address, provider, verifier_encrypted, redirect_path, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                state,
                owner_address.lower(),
                provider,
                encrypt_secret(verifier),
                redirect_path[:120],
                _iso(expires),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return state


def _consume_oauth_state(
    state: str, provider: str, expected_owner: str | None = None,
) -> dict:
    conn = get_connection()
    try:
        query = (
            "SELECT * FROM social_oauth_states "
            "WHERE state = ? AND provider = ? AND expires_at >= ?"
        )
        params: list[Any] = [state, provider, _iso()]
        if expected_owner:
            query += " AND owner_address = ?"
            params.append(expected_owner.lower())
        row = conn.execute(query, tuple(params)).fetchone()
        if not row:
            raise ValueError("Login state is invalid, expired, or already used")
        conn.execute("DELETE FROM social_oauth_states WHERE state = ?", (state,))
        conn.commit()
        result = dict(row)
        result["verifier"] = decrypt_secret(result.pop("verifier_encrypted", None))
        return result
    finally:
        conn.close()


def _public_app_url(request_base_url: str | None = None) -> str:
    configured = os.getenv("APP_PUBLIC_URL", "").strip()
    return (configured or request_base_url or "http://127.0.0.1:8788").rstrip("/")


def begin_x_connection(
    owner_address: str, request_base_url: str | None = None,
) -> dict:
    client_id = os.getenv("X_CLIENT_ID", "").strip()
    if not client_id:
        raise SocialConfigurationError("X_CLIENT_ID is not configured")
    client_secret = os.getenv("X_CLIENT_SECRET", "").strip()
    public_client = os.getenv(
        "X_OAUTH_PUBLIC_CLIENT", "false"
    ).strip().lower() == "true"
    if not client_secret and not public_client:
        raise SocialConfigurationError(
            "X_CLIENT_SECRET is not configured. Set it in Railway for a Web App, "
            "or set X_OAUTH_PUBLIC_CLIENT=true only when the X app is explicitly "
            "configured as a public PKCE client."
        )
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    state = _save_oauth_state(owner_address, "x", verifier)
    callback = f"{_public_app_url(request_base_url)}/api/social/x/callback"
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback,
        "scope": os.getenv("X_OAUTH_SCOPES", DEFAULT_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {"authorization_url": f"{X_AUTHORIZE_URL}?{urlencode(params)}"}


def _upsert_connection(
    owner_address: str,
    provider: str,
    provider_user_id: str,
    username: str | None,
    access_token: str | None,
    refresh_token: str | None = None,
    scopes: str = "",
    expires_at: str | None = None,
    metadata: dict | None = None,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO social_connections
               (owner_address, provider, provider_user_id, username,
                access_token_encrypted, refresh_token_encrypted, scopes,
                expires_at, status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'connected', ?)
               ON CONFLICT(owner_address, provider) DO UPDATE SET
                   provider_user_id = excluded.provider_user_id,
                   username = excluded.username,
                   access_token_encrypted = excluded.access_token_encrypted,
                   refresh_token_encrypted = excluded.refresh_token_encrypted,
                   scopes = excluded.scopes,
                   expires_at = excluded.expires_at,
                   status = 'connected',
                   metadata_json = excluded.metadata_json,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                owner_address.lower(),
                provider,
                str(provider_user_id),
                username,
                encrypt_secret(access_token),
                encrypt_secret(refresh_token),
                scopes,
                expires_at,
                _json(metadata or {}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def complete_x_connection(
    code: str, state: str, request_base_url: str | None = None,
) -> dict:
    pending = _consume_oauth_state(state, "x")
    client_id = os.getenv("X_CLIENT_ID", "").strip()
    client_secret = os.getenv("X_CLIENT_SECRET", "").strip()
    callback = f"{_public_app_url(request_base_url)}/api/social/x/callback"
    data = {
        "code": code,
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": callback,
        "code_verifier": pending["verifier"],
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = (client_id, client_secret) if client_secret else None
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            f"{X_API_URL}/oauth2/token", data=data, headers=headers, auth=auth,
        )
        if token_response.status_code == 401:
            raise SocialConfigurationError(
                "X token exchange was rejected (401). Verify that Railway "
                "X_CLIENT_ID and X_CLIENT_SECRET belong to the same X app and "
                "that its callback URL exactly matches APP_PUBLIC_URL."
            )
        token_response.raise_for_status()
        tokens = token_response.json()
        user_response = await client.get(
            f"{X_API_URL}/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            params={"user.fields": "id,name,username,profile_image_url"},
        )
        user_response.raise_for_status()
        x_user = user_response.json()["data"]
    expires_at = None
    if tokens.get("expires_in"):
        expires_at = _iso(_utcnow() + timedelta(seconds=int(tokens["expires_in"])))
    _upsert_connection(
        pending["owner_address"],
        "x",
        x_user["id"],
        x_user.get("username"),
        tokens.get("access_token"),
        tokens.get("refresh_token"),
        tokens.get("scope", ""),
        expires_at,
        {"name": x_user.get("name"), "profile_image_url": x_user.get("profile_image_url")},
    )
    return {"owner_address": pending["owner_address"], "redirect_path": pending["redirect_path"]}


def begin_telegram_connection(
    owner_address: str, request_base_url: str | None = None,
) -> dict:
    username = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if not username or not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        raise SocialConfigurationError(
            "TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_TOKEN are required"
        )
    state = _save_oauth_state(owner_address, "telegram")
    return {
        "bot_username": username,
        "callback_url": f"{_public_app_url(request_base_url)}/api/social/telegram/callback?state={state}",
        "state": state,
    }


async def validate_telegram_bot_configuration(force: bool = False) -> dict:
    """Verify that the configured username and sealed token belong to one bot."""
    username = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not username or not token:
        raise SocialConfigurationError(
            "TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_TOKEN are required"
        )
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    cached_at = float(_TELEGRAM_BOT_VALIDATION_CACHE.get("checked_at", 0) or 0)
    if (
        not force
        and _TELEGRAM_BOT_VALIDATION_CACHE.get("fingerprint") == fingerprint
        and time.monotonic() - cached_at < 300
    ):
        return dict(_TELEGRAM_BOT_VALIDATION_CACHE["result"])
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{TELEGRAM_BOT_API}/bot{token}/getMe")
        payload = response.json() if response.content else {}
    except Exception as error:
        raise SocialConfigurationError(
            "Telegram could not verify the configured Bot Token. Try again after checking Railway connectivity."
        ) from error
    if response.status_code != 200 or not payload.get("ok"):
        raise SocialConfigurationError(
            "TELEGRAM_BOT_TOKEN was rejected by Telegram. Re-enter the current token from @BotFather."
        )
    actual = str((payload.get("result") or {}).get("username") or "").lstrip("@")
    if actual.lower() != username.lower():
        raise SocialConfigurationError(
            f"Telegram configuration mismatch: TELEGRAM_BOT_USERNAME is @{username}, "
            f"but TELEGRAM_BOT_TOKEN belongs to @{actual or 'unknown'}. Use values from the same bot."
        )
    result = {"username": actual, "bot_id": str((payload.get("result") or {}).get("id") or "")}
    _TELEGRAM_BOT_VALIDATION_CACHE.update({
        "fingerprint": fingerprint,
        "checked_at": time.monotonic(),
        "result": result,
    })
    return result


def telegram_login_verification_error(
    payload: dict, bot_token: str | None = None,
) -> str | None:
    supplied_hash = str(payload.get("hash") or "")
    auth_date = str(payload.get("auth_date") or "")
    if not supplied_hash or not auth_date:
        return "Telegram did not return a complete login proof. Reopen Connect Telegram."
    try:
        age_seconds = abs(int(_utcnow().timestamp()) - int(auth_date))
        if age_seconds > TELEGRAM_LOGIN_MAX_AGE_SECONDS:
            return (
                "Telegram login proof is expired. Close the Telegram authorization window "
                "and start Connect Telegram again."
            )
    except ValueError:
        return "Telegram returned an invalid login timestamp. Reopen Connect Telegram."
    data_check = "\n".join(
        f"{key}={payload[key]}"
        for key in sorted(payload)
        if key not in {"hash", "state"} and payload[key] is not None
    )
    configured_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not configured_token:
        return "Telegram Bot Token is not configured on the server."
    secret = hashlib.sha256(configured_token.encode("utf-8")).digest()
    expected = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied_hash):
        return (
            "Telegram signature does not match the configured Bot Token. Confirm that "
            "TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_TOKEN belong to the same @BotFather bot."
        )
    return None


def verify_telegram_login(payload: dict, bot_token: str | None = None) -> bool:
    return telegram_login_verification_error(payload, bot_token) is None


def complete_telegram_connection(
    payload: dict, expected_owner: str | None = None,
) -> dict:
    state = str(payload.get("state") or "")
    verification_error = telegram_login_verification_error(payload)
    if verification_error:
        raise ValueError(verification_error)
    # Consume the one-time wallet state only after Telegram's proof is valid so
    # a malformed callback cannot burn an otherwise usable login attempt.
    pending = _consume_oauth_state(
        state, "telegram", expected_owner=expected_owner,
    )
    telegram_id = str(payload.get("id") or "")
    if not telegram_id:
        raise ValueError("Telegram identity is missing")
    _upsert_connection(
        pending["owner_address"],
        "telegram",
        telegram_id,
        payload.get("username"),
        None,
        metadata={
            "first_name": payload.get("first_name"),
            "last_name": payload.get("last_name"),
            "photo_url": payload.get("photo_url"),
        },
    )
    return {"owner_address": pending["owner_address"], "redirect_path": pending["redirect_path"]}


def list_connections(owner_address: str) -> dict:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT provider, provider_user_id, username, scopes, expires_at,
                      status, metadata_json, created_at, updated_at
               FROM social_connections WHERE owner_address = ?
               ORDER BY provider""",
            (owner_address.lower(),),
        ).fetchall()
        communities = conn.execute(
            """SELECT id, provider, external_community_id, community_name,
                      asset_key, permission_level, status, last_sync_at
               FROM social_communities WHERE owner_address = ?
               ORDER BY updated_at DESC""",
            (owner_address.lower(),),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _loads(item.pop("metadata_json", None), {})
            result.append(item)
        return {
            "connections": result,
            "communities": [dict(row) for row in communities],
            "provider_status": social_provider_status(),
        }
    finally:
        conn.close()


def disconnect_provider(owner_address: str, provider: str) -> bool:
    if provider not in {"x", "telegram"}:
        return False
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM social_connections WHERE owner_address = ? AND provider = ?",
            (owner_address.lower(), provider),
        )
        if provider == "telegram":
            conn.execute(
                "DELETE FROM social_communities WHERE owner_address = ? AND provider = 'telegram'",
                (owner_address.lower(),),
            )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def create_telegram_link_code(owner_address: str, asset_key: str | None = None) -> dict:
    connection = _get_connection(owner_address, "telegram")
    if not connection:
        raise ValueError("Connect your Telegram identity first")
    code = secrets.token_hex(4).upper()
    expires = _utcnow() + timedelta(minutes=15)
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO social_link_codes
               (code, owner_address, provider, asset_key, expires_at)
               VALUES (?, ?, 'telegram', ?, ?)""",
            (code, owner_address.lower(), asset_key, _iso(expires)),
        )
        conn.commit()
    finally:
        conn.close()
    username = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    return {
        "code": code,
        "expires_at": _iso(expires),
        "instruction": f"Add @{username} to the group, then send /connect {code}.",
    }


def _get_connection(owner_address: str, provider: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM social_connections
               WHERE owner_address = ? AND provider = ? AND status = 'connected'""",
            (owner_address.lower(), provider),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


async def _wallet_x_access_token(owner_address: str) -> str:
    connection = _get_connection(owner_address, "x")
    if not connection:
        return ""
    access_token = decrypt_secret(connection.get("access_token_encrypted")) or ""
    expires_at = connection.get("expires_at")
    still_valid = True
    if expires_at:
        try:
            still_valid = datetime.fromisoformat(str(expires_at)) > _utcnow() + timedelta(seconds=60)
        except ValueError:
            still_valid = False
    if still_valid:
        return access_token
    refresh_token = decrypt_secret(connection.get("refresh_token_encrypted"))
    if not refresh_token:
        return ""
    client_id = os.getenv("X_CLIENT_ID", "").strip()
    client_secret = os.getenv("X_CLIENT_SECRET", "").strip()
    data = {
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "client_id": client_id,
    }
    auth = (client_id, client_secret) if client_secret else None
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{X_API_URL}/oauth2/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            auth=auth,
        )
        response.raise_for_status()
        tokens = response.json()
    new_expiry = None
    if tokens.get("expires_in"):
        new_expiry = _iso(_utcnow() + timedelta(seconds=int(tokens["expires_in"])))
    metadata = _loads(connection.get("metadata_json"), {})
    _upsert_connection(
        owner_address,
        "x",
        connection["provider_user_id"],
        connection.get("username"),
        tokens.get("access_token"),
        tokens.get("refresh_token") or refresh_token,
        tokens.get("scope") or connection.get("scopes") or "",
        new_expiry,
        metadata,
    )
    return str(tokens.get("access_token") or "")


async def process_telegram_webhook(update: dict) -> dict:
    message = update.get("message") or update.get("channel_post") or {}
    text = str(message.get("text") or "").strip()
    if not text.lower().startswith("/connect "):
        return _record_telegram_activity(update, message)
    code = text.split(None, 1)[1].strip().upper()
    sender_id = str((message.get("from") or {}).get("id") or "")
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id or chat.get("type") not in {"group", "supergroup", "channel"}:
        raise ValueError("Run /connect inside the Telegram group or channel")
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM social_link_codes
               WHERE code = ? AND provider = 'telegram' AND used_at IS NULL
                 AND expires_at >= ?""",
            (code, _iso()),
        ).fetchone()
        if not row:
            raise ValueError("Telegram group link code is invalid or expired")
        link = dict(row)
        identity = conn.execute(
            """SELECT provider_user_id FROM social_connections
               WHERE owner_address = ? AND provider = 'telegram'
                 AND status = 'connected'""",
            (link["owner_address"],),
        ).fetchone()
        if not identity or str(identity["provider_user_id"]) != sender_id:
            raise ValueError("Only the wallet's connected Telegram user can use this code")
        if not await _telegram_user_is_admin(chat_id, sender_id):
            raise ValueError(
                "The connected Telegram user must be a group/channel administrator"
            )
        conn.execute(
            """INSERT INTO social_communities
               (owner_address, provider, external_community_id, community_name,
                asset_key, permission_level, metadata_json)
               VALUES (?, 'telegram', ?, ?, ?, 'confirmed-by-user', ?)
               ON CONFLICT(owner_address, provider, external_community_id) DO UPDATE SET
                   community_name = excluded.community_name,
                   asset_key = excluded.asset_key,
                   status = 'connected',
                   updated_at = CURRENT_TIMESTAMP""",
            (
                link["owner_address"],
                chat_id,
                chat.get("title") or chat.get("username") or "Telegram community",
                link.get("asset_key"),
                _json({"chat_type": chat.get("type"), "username": chat.get("username")}),
            ),
        )
        conn.execute(
            "UPDATE social_link_codes SET used_at = ? WHERE code = ?",
            (_iso(), code),
        )
        conn.commit()
    finally:
        conn.close()
    await _telegram_send_message(
        chat_id,
        "meme_ops connected this community. Read-only metric collection is now enabled.",
    )
    return {"accepted": True, "action": "community-linked"}


def _record_telegram_activity(update: dict, message: dict) -> dict:
    """Store aggregate-only activity for explicitly bound communities."""
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not chat_id:
        return {"accepted": True, "action": "ignored"}
    conn = get_connection()
    try:
        communities = conn.execute(
            """SELECT id FROM social_communities
               WHERE provider = 'telegram' AND external_community_id = ?
                 AND status = 'connected'""",
            (chat_id,),
        ).fetchall()
        if not communities:
            return {"accepted": True, "action": "unbound-community"}
        sender = message.get("from") or message.get("sender_chat") or {}
        sender_id = str(sender.get("id") or "")
        sender_hash = (
            hashlib.sha256(f"{chat_id}:{sender_id}".encode("utf-8")).hexdigest()
            if sender_id else None
        )
        event_id = str(
            update.get("update_id")
            or f"{chat_id}:{message.get('message_id') or secrets.token_hex(8)}"
        )
        created_at = _iso()
        try:
            if message.get("date") is not None:
                created_at = _iso(datetime.fromtimestamp(
                    int(message["date"]), tz=timezone.utc,
                ))
        except (TypeError, ValueError, OSError):
            pass
        event_type = "channel_post" if update.get("channel_post") else "message"
        for community in communities:
            conn.execute(
                """INSERT OR IGNORE INTO telegram_activity_events
                   (community_id, external_event_id, sender_hash, event_type, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    community["id"], event_id, sender_hash,
                    event_type, created_at,
                ),
            )
        conn.commit()
        return {
            "accepted": True,
            "action": "activity-recorded",
            "community_count": len(communities),
        }
    finally:
        conn.close()


async def _telegram_user_is_admin(chat_id: str, user_id: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return False
    async with httpx.AsyncClient(timeout=12) as client:
        response = await client.get(
            f"{TELEGRAM_BOT_API}/bot{token}/getChatMember",
            params={"chat_id": chat_id, "user_id": user_id},
        )
    if response.status_code != 200:
        return False
    status = str(((response.json().get("result") or {}).get("status")) or "")
    return status in {"administrator", "creator"}


async def _telegram_send_message(chat_id: str, text: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    async with httpx.AsyncClient(timeout=12) as client:
        await client.post(
            f"{TELEGRAM_BOT_API}/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


async def configure_telegram_webhook() -> dict | None:
    if os.getenv("TELEGRAM_AUTO_SET_WEBHOOK", "false").lower() != "true":
        return None
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    public_url = _public_app_url()
    if not token or not secret or not public_url.startswith("https://"):
        raise SocialConfigurationError(
            "Automatic Telegram webhook setup requires an HTTPS APP_PUBLIC_URL, "
            "TELEGRAM_BOT_TOKEN, and TELEGRAM_WEBHOOK_SECRET"
        )
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{TELEGRAM_BOT_API}/bot{token}/setWebhook",
            json={
                "url": f"{public_url}/api/social/telegram/webhook",
                "secret_token": secret,
                "allowed_updates": ["message", "channel_post"],
            },
        )
        response.raise_for_status()
        return response.json()


def make_asset_key(
    chain: str | None = None,
    contract_address: str | None = None,
    coin_id: str | None = None,
) -> str:
    if chain and contract_address:
        return f"{chain.lower()}:{contract_address.lower()}"
    if coin_id:
        return f"coingecko:{coin_id.lower()}"
    raise ValueError("An asset requires chain + contract or CoinGecko ID")


def upsert_social_asset(asset: dict, rank: int | None = None) -> str:
    key = asset.get("asset_key") or make_asset_key(
        asset.get("chain"), asset.get("contract_address"), asset.get("coin_id") or asset.get("id")
    )
    rank_value = max(1, int(rank or asset.get("rank") or 999))
    market_cap = float(asset.get("market_cap") or 0)
    volume = float(asset.get("volume_24h") or asset.get("total_volume") or 0)
    change = float(asset.get("change_24h") or asset.get("price_change_percentage_24h") or 0)
    liquidity_component = math.log10(max(market_cap, 1)) * 3
    activity_component = math.log10(max(volume, 1)) * 2
    market_score = round(max(0, 110 - rank_value) + liquidity_component + activity_component + min(abs(change), 30) / 6, 3)
    tier = 1 if rank_value <= 20 else 2 if rank_value <= 100 else 3
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO social_assets
               (asset_key, coin_id, name, symbol, chain, contract_address,
                image_url, market_cap, volume_24h, change_24h, market_score,
                priority_tier, official_x, telegram_chat, metadata_json,
                last_market_sync_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(asset_key) DO UPDATE SET
                   coin_id = COALESCE(excluded.coin_id, social_assets.coin_id),
                   name = excluded.name,
                   symbol = excluded.symbol,
                   chain = excluded.chain,
                   contract_address = COALESCE(excluded.contract_address, social_assets.contract_address),
                   image_url = COALESCE(excluded.image_url, social_assets.image_url),
                   market_cap = excluded.market_cap,
                   volume_24h = excluded.volume_24h,
                   change_24h = excluded.change_24h,
                   market_score = excluded.market_score,
                   priority_tier = excluded.priority_tier,
                   official_x = COALESCE(excluded.official_x, social_assets.official_x),
                   telegram_chat = COALESCE(excluded.telegram_chat, social_assets.telegram_chat),
                   metadata_json = excluded.metadata_json,
                   last_market_sync_at = excluded.last_market_sync_at,
                   updated_at = CURRENT_TIMESTAMP""",
            (
                key,
                asset.get("coin_id") or asset.get("id"),
                asset.get("name") or asset.get("symbol") or "Unknown",
                str(asset.get("symbol") or "").upper(),
                asset.get("chain") or "unknown",
                asset.get("contract_address"),
                asset.get("image_url") or asset.get("image"),
                market_cap,
                volume,
                change,
                market_score,
                tier,
                asset.get("official_x"),
                asset.get("telegram_chat"),
                _json(asset.get("metadata") or {"rank": rank_value}),
                _iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return key


def list_social_assets(limit: int = 100) -> list[dict]:
    conn = get_connection()
    try:
        return [
            dict(row) for row in conn.execute(
                """SELECT * FROM social_assets
                   ORDER BY priority_tier, market_score DESC LIMIT ?""",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        ]
    finally:
        conn.close()


def update_social_asset_sources(
    asset_key: str,
    official_x: str | None = None,
    telegram_chat: str | None = None,
) -> None:
    """Attach reviewed/discovered public handles without replacing known ones."""
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE social_assets
               SET official_x = COALESCE(official_x, ?),
                   telegram_chat = COALESCE(telegram_chat, ?),
                   source_discovery_at = ?,
                   updated_at = CURRENT_TIMESTAMP
               WHERE asset_key = ?""",
            (official_x, telegram_chat, _iso(), asset_key),
        )
        conn.commit()
    finally:
        conn.close()


def _cache_ttl_seconds(provider: str) -> int:
    env_name = (
        "SOCIAL_X_CACHE_TTL_SECONDS"
        if provider == "x" else "SOCIAL_TELEGRAM_CACHE_TTL_SECONDS"
    )
    default = "900" if provider == "x" else "1800"
    return max(60, int(os.getenv(env_name, default)))


def _as_utc_datetime(value: str | datetime | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def social_cache_state(
    asset_key: str,
    provider: str,
    owner_address: str | None = None,
    max_age_seconds: int | None = None,
    fallback_asset_key: str | None = None,
) -> dict:
    """Return a provider-specific cache decision without exposing private data."""
    owner = owner_address.lower() if owner_address else None
    asset_keys = [asset_key]
    if fallback_asset_key and fallback_asset_key != asset_key:
        asset_keys.append(fallback_asset_key)
    placeholders = ",".join("?" for _ in asset_keys)
    conn = get_connection()
    try:
        row = conn.execute(
            f"""SELECT MAX(collected_at) AS collected_at
               FROM social_metric_snapshots
               WHERE asset_key IN ({placeholders}) AND provider = ?
                 AND (owner_address IS NULL OR owner_address = ?)""",
            (*asset_keys, provider, owner),
        ).fetchone()
    finally:
        conn.close()
    collected = _as_utc_datetime(row["collected_at"] if row else None)
    if not collected:
        return {
            "state": "missing", "fresh": False, "stale": False,
            "age_seconds": None, "collected_at": None,
        }
    age = max(0, int((_utcnow() - collected).total_seconds()))
    ttl = max_age_seconds or _cache_ttl_seconds(provider)
    fresh = age <= ttl
    return {
        "state": "fresh" if fresh else "stale",
        "fresh": fresh,
        "stale": not fresh,
        "age_seconds": age,
        "collected_at": collected.isoformat(),
        "ttl_seconds": ttl,
    }


def latest_social_context(
    asset_key: str,
    limit: int = 8,
    owner_address: str | None = None,
    fallback_asset_key: str | None = None,
) -> dict:
    conn = get_connection()
    try:
        owner = owner_address.lower() if owner_address else None
        asset_keys = [asset_key]
        if fallback_asset_key and fallback_asset_key != asset_key:
            asset_keys.append(fallback_asset_key)
        placeholders = ",".join("?" for _ in asset_keys)
        connections: dict[str, dict] = {}
        telegram_communities: list[dict] = []
        if owner:
            connections = {
                str(row["provider"]): dict(row)
                for row in conn.execute(
                    """SELECT provider, username, scopes, expires_at, status,
                              metadata_json
                       FROM social_connections
                       WHERE owner_address = ? AND status = 'connected'""",
                    (owner,),
                ).fetchall()
            }
            telegram_communities = [
                dict(row) for row in conn.execute(
                    """SELECT id, community_name, asset_key, status, last_sync_at
                       FROM social_communities
                       WHERE owner_address = ? AND provider = 'telegram'
                         AND status = 'connected'
                         AND (asset_key = ? OR asset_key IS NULL)""",
                    (owner, asset_key),
                ).fetchall()
            ]
        metrics = [
            dict(row) for row in conn.execute(
                f"""SELECT m.* FROM social_metric_snapshots m
                   JOIN (
                     SELECT provider, owner_address, MAX(collected_at) collected_at
                     FROM social_metric_snapshots WHERE asset_key IN ({placeholders})
                       AND (owner_address IS NULL OR owner_address = ?)
                     GROUP BY provider, owner_address
                   ) latest
                   ON latest.provider = m.provider
                  AND COALESCE(latest.owner_address, '') = COALESCE(m.owner_address, '')
                  AND latest.collected_at = m.collected_at
                   WHERE m.asset_key IN ({placeholders})
                   ORDER BY m.confidence DESC""",
                (*asset_keys, owner, *asset_keys),
            ).fetchall()
        ]
        if owner:
            preferred: dict[str, dict] = {}
            for item in metrics:
                provider = str(item.get("provider") or "")
                current = preferred.get(provider)
                if not current or (
                    item.get("owner_address") == owner
                    and current.get("owner_address") != owner
                ):
                    preferred[provider] = item
            metrics = list(preferred.values())
        documents = [
            dict(row) for row in conn.execute(
                f"""SELECT title, content, platform, document_type, keywords_json,
                          confidence, source_mode, collected_at
                   FROM social_rag_documents WHERE asset_key IN ({placeholders})
                     AND (owner_address IS NULL OR owner_address = ?)
                     AND (expires_at IS NULL OR expires_at >= ?)
                   ORDER BY collected_at DESC LIMIT ?""",
                (*asset_keys, owner, _iso(), max(1, min(limit, 20))),
            ).fetchall()
        ]
        for item in metrics:
            item["raw_summary"] = _loads(item.pop("raw_summary_json", None), {})
        for item in documents:
            item["keywords"] = _loads(item.pop("keywords_json", None), [])
        metric_providers = {
            str(item.get("provider") or "").lower() for item in metrics
        }
        x_identity = "x" in connections
        telegram_identity = "telegram" in connections
        provider_states = {
            "x": {
                "identity_connected": x_identity,
                "metrics_available": "x" in metric_providers,
                "status": (
                    "ready" if "x" in metric_providers
                    else "connected_no_data" if x_identity
                    else "not_connected"
                ),
                "username": connections.get("x", {}).get("username"),
            },
            "telegram": {
                "identity_connected": telegram_identity,
                "community_count": len(telegram_communities),
                "metrics_available": "telegram" in metric_providers,
                "status": (
                    "ready" if "telegram" in metric_providers
                    else "group_not_bound"
                    if telegram_identity and not telegram_communities
                    else "connected_no_data" if telegram_identity
                    else "not_connected"
                ),
                "username": connections.get("telegram", {}).get("username"),
            },
            "reddit": {
                "identity_connected": False,
                "metrics_available": False,
                "status": "not_configured",
            },
        }
        for provider in ("x", "telegram"):
            provider_states[provider]["cache"] = social_cache_state(
                asset_key, provider, owner_address=owner,
                fallback_asset_key=fallback_asset_key,
            )
        demo_metrics = [
            item for item in metrics
            if str(item.get("source_mode") or "").startswith("demo-synthetic")
        ]
        return {
            "asset_key": asset_key,
            "fallback_asset_key": fallback_asset_key,
            "connected": bool(metrics),
            "binding_connected": x_identity or telegram_identity,
            "cache_hit": bool(metrics),
            "demo_mode": bool(demo_metrics),
            "data_provenance": (
                "synthetic-demo-not-live" if demo_metrics else "provider-collected"
            ),
            "stale": any(
                state.get("cache", {}).get("stale")
                for state in provider_states.values()
            ),
            "providers": provider_states,
            "metrics": metrics,
            "rag_documents": documents,
        }
    finally:
        conn.close()


def _save_snapshot(
    asset_key: str,
    provider: str,
    source_mode: str,
    metrics: dict,
    community_id: int | None = None,
    owner_address: str | None = None,
) -> None:
    collected_at = _iso()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO social_metric_snapshots
               (asset_key, provider, source_mode, owner_address, community_id, followers, members,
                mentions_24h, posts_24h, active_authors_24h, engagements_24h,
                engagement_rate, sentiment, confidence, raw_summary_json, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                asset_key,
                provider,
                source_mode,
                owner_address.lower() if owner_address else None,
                community_id,
                metrics.get("followers"),
                metrics.get("members"),
                metrics.get("mentions_24h"),
                metrics.get("posts_24h"),
                metrics.get("active_authors_24h"),
                metrics.get("engagements_24h"),
                metrics.get("engagement_rate"),
                metrics.get("sentiment"),
                metrics.get("confidence", 0.7),
                _json(metrics.get("raw_summary") or {}),
                collected_at,
            ),
        )
        period_key = _utcnow().strftime("%Y-%m-%dT%H")
        known = [
            f"mentions/24h={metrics['mentions_24h']}"
            for _ in [0] if metrics.get("mentions_24h") is not None
        ]
        if metrics.get("members") is not None:
            known.append(f"members={metrics['members']}")
        if metrics.get("followers") is not None:
            known.append(f"followers={metrics['followers']}")
        if metrics.get("engagements_24h") is not None:
            known.append(f"engagements/24h={metrics['engagements_24h']}")
        if metrics.get("posts_24h") is not None:
            known.append(f"posts/24h={metrics['posts_24h']}")
        if metrics.get("active_authors_24h") is not None:
            known.append(f"active authors/24h={metrics['active_authors_24h']}")
        content = (
            f"{provider.upper()} social snapshot for {asset_key}. "
            + (", ".join(known) if known else "No supported numeric metric was returned.")
            + f". Source mode: {source_mode}. Collected at {collected_at}."
        )
        conn.execute(
            """INSERT INTO social_rag_documents
               (asset_key, platform, document_type, period_key, title, content,
                keywords_json, confidence, source_mode, owner_scope, owner_address,
                collected_at, expires_at)
               VALUES (?, ?, 'hourly-summary', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(asset_key, platform, document_type, period_key, owner_scope) DO UPDATE SET
                   title = excluded.title,
                   content = excluded.content,
                   keywords_json = excluded.keywords_json,
                   confidence = excluded.confidence,
                   source_mode = excluded.source_mode,
                   collected_at = excluded.collected_at,
                   expires_at = excluded.expires_at""",
            (
                asset_key,
                provider,
                period_key,
                f"{provider.upper()} hourly signal",
                content,
                _json([provider, asset_key, "mentions", "engagement", "community"]),
                metrics.get("confidence", 0.7),
                source_mode,
                owner_address.lower() if owner_address else "shared",
                owner_address.lower() if owner_address else None,
                collected_at,
                _iso(_utcnow() + timedelta(days=14)),
            ),
        )
        conn.commit()
    finally:
        conn.close()


DEMO_SOCIAL_SOURCE_MODE = "demo-synthetic-v1"


def _demo_jitter(asset_key: str, provider: str) -> float:
    digest = hashlib.sha256(f"{asset_key}:{provider}".encode("utf-8")).digest()
    return 0.85 + (int.from_bytes(digest[:2], "big") / 65535) * 0.30


def seed_demo_social_asset(asset: dict, rank: int = 10) -> dict:
    """Seed one searched asset during demo mode without replacing real evidence."""
    asset_key = str(asset.get("asset_key") or "").strip()
    if not asset_key:
        return {"snapshot_count": 0, "skipped_real": 0, "skipped_existing": 0}
    safe_rank = max(1, min(int(rank or 10), 100))
    inserted = skipped_real = skipped_existing = 0
    for provider in ("x", "telegram"):
        conn = get_connection()
        try:
            rows = conn.execute(
                """SELECT source_mode FROM social_metric_snapshots
                   WHERE asset_key = ? AND provider = ?
                   ORDER BY collected_at DESC""",
                (asset_key, provider),
            ).fetchall()
        finally:
            conn.close()
        modes = [str(row["source_mode"] or "") for row in rows]
        if any(not mode.startswith("demo-synthetic") for mode in modes):
            skipped_real += 1
            continue
        if any(mode.startswith("demo-synthetic") for mode in modes):
            skipped_existing += 1
            continue

        jitter = _demo_jitter(asset_key, provider)
        if provider == "x":
            mentions = max(120, round(18_000 * jitter / (safe_rank ** 0.78)))
            active_authors = max(
                30, round(mentions * (0.30 + safe_rank * 0.008))
            )
            engagements = max(mentions, round(mentions * (3.8 + jitter)))
            metrics = {
                "followers": max(
                    8_000, round(1_500_000 * jitter / (safe_rank ** 0.72))
                ),
                "mentions_24h": mentions,
                "posts_24h": mentions,
                "active_authors_24h": active_authors,
                "engagements_24h": engagements,
                "engagement_rate": round(engagements / max(mentions, 1), 6),
                "sentiment": round(-0.10 + (jitter - 0.85) * 1.2, 4),
                "confidence": 0.15,
            }
        else:
            posts = max(12, round(180 * jitter / (safe_rank ** 0.48)))
            authors = max(8, round(posts * (0.45 + safe_rank * 0.01)))
            engagements = max(posts, round(posts * (8 + jitter * 5)))
            metrics = {
                "members": max(
                    3_000, round(420_000 * jitter / (safe_rank ** 0.66))
                ),
                "posts_24h": posts,
                "active_authors_24h": authors,
                "engagements_24h": engagements,
                "engagement_rate": round(engagements / max(posts, 1), 6),
                "confidence": 0.15,
            }
        metrics["raw_summary"] = {
            "synthetic": True,
            "demo_only": True,
            "method": "deterministic-rank-weighted-fixture",
            "warning": "Not collected from X or Telegram; do not use for decisions.",
        }
        _save_snapshot(asset_key, provider, DEMO_SOCIAL_SOURCE_MODE, metrics)
        inserted += 1
    return {
        "snapshot_count": inserted,
        "skipped_real": skipped_real,
        "skipped_existing": skipped_existing,
    }


def seed_demo_social_snapshots(limit: int = 10) -> dict:
    """Seed deterministic demo metrics without superseding real snapshots."""
    assets = list_social_assets(max(1, min(int(limit), 100)))
    inserted = skipped_real = skipped_existing = 0
    seeded_assets: list[str] = []
    for rank, asset in enumerate(assets, 1):
        asset_seeded = False
        for provider in ("x", "telegram"):
            conn = get_connection()
            try:
                rows = conn.execute(
                    """SELECT source_mode FROM social_metric_snapshots
                       WHERE asset_key = ? AND provider = ?
                       ORDER BY collected_at DESC""",
                    (asset["asset_key"], provider),
                ).fetchall()
            finally:
                conn.close()
            modes = [str(row["source_mode"] or "") for row in rows]
            if any(not mode.startswith("demo-synthetic") for mode in modes):
                skipped_real += 1
                continue
            if any(mode.startswith("demo-synthetic") for mode in modes):
                skipped_existing += 1
                continue

            jitter = _demo_jitter(asset["asset_key"], provider)
            if provider == "x":
                mentions = max(120, round(18_000 * jitter / (rank ** 0.78)))
                active_authors = max(30, round(mentions * (0.30 + rank * 0.008)))
                engagements = max(mentions, round(mentions * (3.8 + jitter)))
                metrics = {
                    "followers": max(
                        8_000, round(1_500_000 * jitter / (rank ** 0.72))
                    ),
                    "mentions_24h": mentions,
                    "posts_24h": mentions,
                    "active_authors_24h": active_authors,
                    "engagements_24h": engagements,
                    "engagement_rate": round(engagements / max(mentions, 1), 6),
                    "sentiment": round(-0.10 + (jitter - 0.85) * 1.2, 4),
                    "confidence": 0.15,
                }
            else:
                posts = max(12, round(180 * jitter / (rank ** 0.48)))
                authors = max(8, round(posts * (0.45 + rank * 0.01)))
                engagements = max(posts, round(posts * (8 + jitter * 5)))
                metrics = {
                    "members": max(
                        3_000, round(420_000 * jitter / (rank ** 0.66))
                    ),
                    "posts_24h": posts,
                    "active_authors_24h": authors,
                    "engagements_24h": engagements,
                    "engagement_rate": round(engagements / max(posts, 1), 6),
                    "confidence": 0.15,
                }
            metrics["raw_summary"] = {
                "synthetic": True,
                "demo_only": True,
                "method": "deterministic-rank-weighted-fixture",
                "warning": "Not collected from X or Telegram; do not use for decisions.",
            }
            _save_snapshot(
                asset["asset_key"], provider, DEMO_SOCIAL_SOURCE_MODE, metrics,
            )
            inserted += 1
            asset_seeded = True
        if asset_seeded:
            seeded_assets.append(asset["asset_key"])
    return {
        "mode": DEMO_SOCIAL_SOURCE_MODE,
        "asset_count": len(seeded_assets),
        "snapshot_count": inserted,
        "skipped_real": skipped_real,
        "skipped_existing": skipped_existing,
        "assets": seeded_assets,
    }


def _x_collection_error(response: httpx.Response, endpoint: str) -> dict:
    """Return an actionable error without exposing credentials or raw headers."""
    try:
        payload = response.json() if response.content else {}
    except ValueError:
        payload = {}
    detail = str(payload.get("detail") or payload.get("title") or "").lower()
    if response.status_code == 402 or "credits depleted" in detail:
        code = "credits_depleted"
        message = (
            "X API credits are depleted. Add credits to the X developer Project/App; "
            "account OAuth binding alone does not include API data credits."
        )
    elif response.status_code == 401:
        code = "credential_rejected"
        message = "X API rejected the configured credential. Regenerate the App credential and reconnect X."
    elif response.status_code == 403:
        code = "access_tier_denied"
        message = "The current X API access tier does not permit this endpoint."
    elif response.status_code == 429:
        code = "rate_limited"
        message = "X API rate limit reached. Retry after the provider reset window."
    else:
        code = "provider_error"
        message = f"X API returned HTTP {response.status_code} for {endpoint}."
    return {
        "code": code,
        "http_status": response.status_code,
        "endpoint": endpoint,
        "message": message,
    }


def _x_asset_query(asset: dict) -> str:
    symbol = "".join(
        char for char in str(asset.get("symbol") or "") if char.isalnum()
    )[:20]
    name = " ".join(
        "".join(
            char for char in str(asset.get("name") or "")
            if char.isalnum() or char in {" ", "-", "_"}
        ).split()
    )[:80]
    terms = []
    if symbol:
        terms.append(f"${symbol}")
    if name and name.lower() != symbol.lower():
        terms.append(f'"{name}"')
    official_x = "".join(
        char for char in str(asset.get("official_x") or "")
        if char.isalnum() or char == "_"
    )[:30]
    if official_x:
        terms.append(f"@{official_x}")
    if not terms:
        terms.append(f'"{str(asset.get("asset_key") or "meme")[:80]}"')
    return f"({' OR '.join(terms)}) -is:retweet"


async def social_connection_diagnostics(
    owner_address: str, force: bool = False,
) -> dict:
    """Test provider data paths and return only non-secret, actionable state."""
    owner = owner_address.lower()
    cached = _SOCIAL_DIAGNOSTICS_CACHE.get(owner)
    if not force and cached and time.monotonic() - cached[0] < 300:
        return dict(cached[1])
    connections = list_connections(owner)
    by_provider = {
        item["provider"]: item for item in connections.get("connections") or []
    }
    result: dict[str, Any] = {
        "checked_at": _iso(),
        "x": {
            "identity_connected": "x" in by_provider,
            "collector": "not_configured",
        },
        "telegram": {
            "identity_connected": "telegram" in by_provider,
            "community_count": len(connections.get("communities") or []),
            "bot": "not_configured",
        },
    }
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if token:
        start_time = (_utcnow() - timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(
                f"{X_API_URL}/tweets/counts/recent",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "query": '($DOGE OR "Dogecoin") -is:retweet',
                    "granularity": "hour",
                    "start_time": start_time,
                },
            )
        if response.status_code == 200:
            result["x"].update({
                "collector": "ready",
                "message": "App-level recent-counts access is working.",
            })
        else:
            result["x"].update({
                "collector": "action_required",
                "error": _x_collection_error(response, "recent-counts"),
            })
    try:
        bot = await validate_telegram_bot_configuration(force=force)
        result["telegram"].update({
            "bot": "ready",
            "bot_username": bot.get("username"),
            "message": "Bot Token and Bot Username were verified by Telegram getMe.",
        })
    except SocialConfigurationError as error:
        result["telegram"].update({
            "bot": "action_required", "message": str(error),
        })
    _SOCIAL_DIAGNOSTICS_CACHE[owner] = (time.monotonic(), result)
    return dict(result)


class SocialCollector:
    """Refresh a ranked meme universe and collect supported social signals."""

    def __init__(self) -> None:
        self.universe_size = max(100, int(os.getenv("SOCIAL_ASSET_UNIVERSE_SIZE", "100")))

    def _providers_due(self, asset: dict) -> set[str]:
        due: set[str] = set()
        tier = int(asset.get("priority_tier") or 3)
        x_age = (15 if tier == 1 else 60 if tier == 2 else 360) * 60
        if os.getenv("X_BEARER_TOKEN", "").strip() and not social_cache_state(
            asset["asset_key"], "x", max_age_seconds=x_age,
        )["fresh"]:
            due.add("x")
        mtproto = mtproto_provider_status()
        if (
            mtproto["configured"]
            and asset.get("telegram_chat")
            and is_authorized_telegram_handle(asset.get("telegram_chat"))
            and not social_cache_state(
                asset["asset_key"], "telegram",
                max_age_seconds=_cache_ttl_seconds("telegram"),
            )["fresh"]
        ):
            due.add("telegram")
        return due

    def _is_due(self, asset: dict) -> bool:
        return bool(self._providers_due(asset))

    async def refresh_asset_universe(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd",
                    "category": "meme-token",
                    "order": "market_cap_desc",
                    "per_page": min(self.universe_size, 250),
                    "page": 1,
                    "sparkline": "false",
                    "price_change_percentage": "24h",
                },
            )
            response.raise_for_status()
            coins = response.json()
            for rank, coin in enumerate(coins, 1):
                upsert_social_asset(
                    {
                        "coin_id": coin.get("id"),
                        "name": coin.get("name"),
                        "symbol": coin.get("symbol"),
                        "image_url": coin.get("image"),
                        "market_cap": coin.get("market_cap"),
                        "volume_24h": coin.get("total_volume"),
                        "change_24h": coin.get("price_change_percentage_24h"),
                        "chain": "unknown",
                        "metadata": {"rank": rank},
                    },
                    rank,
                )
            # Fill a bounded number of missing public-community handles per run.
            # This avoids a 100-request burst while eventually building the
            # shared source registry used by both scheduled and on-demand paths.
            discovery_batch = max(
                0, min(int(os.getenv("SOCIAL_SOURCE_DISCOVERY_BATCH", "5")), 20)
            )
            discovery_cutoff = _utcnow() - timedelta(days=7)
            missing = []
            for asset in list_social_assets(self.universe_size):
                last_discovery = _as_utc_datetime(asset.get("source_discovery_at"))
                if (
                    asset.get("coin_id")
                    and (not asset.get("official_x") or not asset.get("telegram_chat"))
                    and (not last_discovery or last_discovery <= discovery_cutoff)
                ):
                    missing.append(asset)
                if len(missing) >= discovery_batch:
                    break
            for asset in missing:
                try:
                    details_response = await client.get(
                        f"https://api.coingecko.com/api/v3/coins/{asset['coin_id']}",
                        params={
                            "localization": "false", "tickers": "false",
                            "market_data": "false", "community_data": "false",
                            "developer_data": "false", "sparkline": "false",
                        },
                    )
                    if details_response.status_code != 200:
                        continue
                    links = (details_response.json() or {}).get("links") or {}
                    update_social_asset_sources(
                        asset["asset_key"],
                        official_x=str(links.get("twitter_screen_name") or "").lstrip("@") or None,
                        telegram_chat=str(links.get("telegram_channel_identifier") or "").lstrip("@") or None,
                    )
                except Exception:
                    continue
        return list_social_assets(self.universe_size)

    async def collect_asset(
        self,
        asset: dict,
        owner_address: str | None = None,
        providers: set[str] | None = None,
    ) -> dict:
        requested = providers or {"x", "telegram"}
        shared_token = os.getenv("X_BEARER_TOKEN", "").strip()
        wallet_token = (
            await _wallet_x_access_token(owner_address)
            if owner_address else ""
        )
        results: dict[str, Any] = {
            "asset_key": asset["asset_key"], "providers": {}, "errors": {},
        }
        if "x" in requested and (shared_token or wallet_token):
            query = _x_asset_query(asset)
            start_time = (_utcnow() - timedelta(hours=24)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            mentions: int | None = None
            posts: list[dict] | None = None
            counts_ok = False
            search_ok = False
            async with httpx.AsyncClient(timeout=20) as client:
                if shared_token:
                    response = await client.get(
                        f"{X_API_URL}/tweets/counts/recent",
                        headers={"Authorization": f"Bearer {shared_token}"},
                        params={
                            "query": query,
                            "granularity": "hour",
                            "start_time": start_time,
                        },
                    )
                    if response.status_code == 200:
                        payload = response.json()
                        mentions = int(
                            (payload.get("meta") or {}).get("total_tweet_count")
                            or sum(
                                int(item.get("tweet_count") or 0)
                                for item in payload.get("data") or []
                            )
                        )
                        counts_ok = True
                    else:
                        results["errors"]["x_counts"] = _x_collection_error(
                            response, "recent-counts",
                        )
                search_token = wallet_token or shared_token
                if search_token and (
                    owner_address or int(asset.get("priority_tier") or 3) == 1
                ):
                    detail_response = await client.get(
                        f"{X_API_URL}/tweets/search/recent",
                        headers={"Authorization": f"Bearer {search_token}"},
                        params={
                            "query": query,
                            "start_time": start_time,
                            "max_results": max(
                                10, min(
                                    int(os.getenv("X_RECENT_SEARCH_MAX_RESULTS", "10")),
                                    100,
                                ),
                            ),
                            "tweet.fields": "author_id,public_metrics,created_at",
                        },
                    )
                    if detail_response.status_code == 200:
                        posts = detail_response.json().get("data") or []
                        search_ok = True
                    else:
                        results["errors"]["x_search"] = _x_collection_error(
                            detail_response, "recent-search",
                        )
            if mentions is None and posts is not None:
                # A user OAuth search is a useful fallback when App-level
                # counts are unavailable. The value is explicitly a sample.
                mentions = len(posts)
            active_authors = (
                len({item.get("author_id") for item in posts if item.get("author_id")})
                if posts is not None else None
            )
            engagements = (
                sum(
                    sum(
                        int((item.get("public_metrics") or {}).get(key) or 0)
                        for key in ("like_count", "reply_count", "retweet_count", "quote_count")
                    )
                    for item in posts
                )
                if posts is not None else None
            )
            if counts_ok or search_ok:
                source_mode = (
                    "shared-counts+wallet-search"
                    if counts_ok and wallet_token and search_ok
                    else "wallet-oauth-search"
                    if wallet_token and search_ok
                    else "shared-api"
                )
                owner_scope = owner_address if wallet_token and search_ok else None
                confidence = 0.85 if counts_ok else 0.65
                window = "24h-counts" if counts_ok else "24h-search-sample"
                metrics = {
                    "mentions_24h": mentions,
                    "posts_24h": mentions,
                    "active_authors_24h": active_authors,
                    "engagements_24h": engagements,
                    "engagement_rate": (
                        round(engagements / max(mentions or 0, 1), 6)
                        if engagements is not None else None
                    ),
                    "confidence": confidence,
                    "raw_summary": {
                        "query": query,
                        "provider_window": window,
                        "sample_size": len(posts) if posts is not None else None,
                    },
                }
                _save_snapshot(
                    asset["asset_key"], "x", source_mode, metrics,
                    owner_address=owner_scope,
                )
                results["providers"]["x"] = metrics

        if "telegram" in requested and owner_address:
            await self._collect_telegram_for_owner(asset, owner_address, results)
        if (
            "telegram" in requested
            and asset.get("telegram_chat")
            and mtproto_provider_status()["configured"]
            and is_authorized_telegram_handle(asset.get("telegram_chat"))
        ):
            try:
                metrics = await collect_public_telegram_asset(asset)
                if metrics:
                    _save_snapshot(
                        asset["asset_key"], "telegram", "shared-mtproto",
                        metrics,
                    )
                    results["providers"]["telegram"] = metrics
            except Exception as error:
                results["errors"]["telegram_mtproto"] = {
                    "code": "mtproto_collection_failed",
                    "message": (
                        "Telegram public-community refresh failed. Check the "
                        f"registered handle and sealed MTProto session ({type(error).__name__})."
                    ),
                }
        return results

    async def _collect_telegram_for_owner(
        self, asset: dict, owner_address: str, results: dict,
    ) -> None:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            return
        conn = get_connection()
        try:
            communities = [
                dict(row) for row in conn.execute(
                    """SELECT * FROM social_communities
                       WHERE owner_address = ? AND provider = 'telegram'
                         AND status = 'connected'
                         AND (asset_key = ? OR asset_key IS NULL)""",
                    (owner_address.lower(), asset["asset_key"]),
                ).fetchall()
            ]
        finally:
            conn.close()
        if not communities:
            return
        members_total = 0
        success = 0
        async with httpx.AsyncClient(timeout=15) as client:
            for community in communities:
                response = await client.get(
                    f"{TELEGRAM_BOT_API}/bot{token}/getChatMemberCount",
                    params={"chat_id": community["external_community_id"]},
                )
                if response.status_code != 200:
                    continue
                members_total += int(response.json().get("result") or 0)
                success += 1
        if success:
            community_ids = [int(item["id"]) for item in communities]
            placeholders = ",".join("?" for _ in community_ids)
            activity_count = 0
            active_authors = 0
            conn = get_connection()
            try:
                if community_ids:
                    row = conn.execute(
                        f"""SELECT COUNT(*) AS post_count,
                                   COUNT(DISTINCT sender_hash) AS author_count
                            FROM telegram_activity_events
                            WHERE community_id IN ({placeholders})
                              AND created_at >= ?""",
                        (*community_ids, _iso(_utcnow() - timedelta(hours=24))),
                    ).fetchone()
                    activity_count = int(row["post_count"] or 0)
                    active_authors = int(row["author_count"] or 0)
                    conn.execute(
                        f"""UPDATE social_communities SET last_sync_at = ?
                            WHERE id IN ({placeholders})""",
                        (_iso(), *community_ids),
                    )
                    conn.commit()
            finally:
                conn.close()
            metrics = {
                "members": members_total,
                "posts_24h": activity_count,
                "active_authors_24h": active_authors,
                "confidence": 0.95,
                "raw_summary": {
                    "connected_communities": success,
                    "privacy_mode": "aggregate-only-no-message-text",
                },
            }
            _save_snapshot(
                asset["asset_key"], "telegram", "wallet-bot", metrics,
                owner_address=owner_address,
            )
            results["providers"]["telegram"] = metrics

    async def run(self, limit: int | None = None) -> dict:
        started = _iso()
        conn = get_connection()
        try:
            run_id = conn.execute(
                "INSERT INTO social_sync_runs(mode, provider) VALUES ('scheduled', 'all')"
            ).lastrowid
            conn.commit()
        finally:
            conn.close()
        errors: list[str] = []
        success = 0
        try:
            assets = await self.refresh_asset_universe()
        except Exception as error:
            assets = list_social_assets(limit or self.universe_size)
            errors.append(f"asset-universe: {error}")
        assets = assets[: max(1, min(limit or self.universe_size, self.universe_size))]
        collection_plan = [
            (asset, self._providers_due(asset)) for asset in assets
        ]
        collection_plan = [item for item in collection_plan if item[1]]
        semaphore = asyncio.Semaphore(max(1, int(os.getenv("SOCIAL_COLLECTOR_CONCURRENCY", "4"))))

        async def collect_one(asset: dict, providers: set[str]) -> None:
            nonlocal success
            async with semaphore:
                try:
                    await self.collect_asset(asset, providers=providers)
                    success += 1
                except Exception as error:
                    errors.append(f"{asset.get('asset_key')}: {error}")

        await asyncio.gather(*(
            collect_one(asset, providers)
            for asset, providers in collection_plan
        ))
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE social_sync_runs SET asset_count = ?, success_count = ?,
                   error_count = ?, status = ?, details_json = ?, finished_at = ?
                   WHERE id = ?""",
                (
                    len(collection_plan),
                    success,
                    len(errors),
                    "completed" if not errors else "partial",
                    _json({"started_at": started, "errors": errors[:30]}),
                    _iso(),
                    run_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return {
            "asset_count": len(collection_plan),
            "success_count": success,
            "error_count": len(errors),
            "errors": errors[:30],
        }


async def enrich_raw_data_with_social(raw_data: dict, owner_address: str | None) -> dict:
    pairs = (raw_data.get("dexscreener") or {}).get("pairs") or []
    coin = raw_data.get("coingecko") or {}
    first = pairs[0] if pairs else {}
    base = first.get("baseToken") or {}
    try:
        asset_key = make_asset_key(
            first.get("chainId") or raw_data.get("chain_hint"),
            base.get("address"),
            coin.get("id"),
        )
    except ValueError:
        raw_data["social"] = {
            "connected": False,
            "status": "asset-unresolved",
            "message": "Resolve an exact chain/contract before collecting social signals.",
        }
        return raw_data
    existing = next(
        (item for item in list_social_assets(500) if item["asset_key"] == asset_key),
        None,
    )
    registry_asset_key = (
        f"coingecko:{str(coin.get('id')).lower()}" if coin.get("id") else None
    )
    registry_asset = next(
        (
            item for item in list_social_assets(500)
            if registry_asset_key and item["asset_key"] == registry_asset_key
        ),
        None,
    )
    source_asset = existing or registry_asset or {}
    coin_links = coin.get("links") or {}
    discovered_x = (
        str(coin_links.get("twitter_screen_name") or "").strip().lstrip("@")
        or None
    )
    discovered_telegram = (
        str(coin_links.get("telegram_channel_identifier") or "")
        .strip()
        .lstrip("@")
        or None
    )
    asset = {
        "asset_key": asset_key,
        "coin_id": coin.get("id"),
        "name": base.get("name") or coin.get("name") or raw_data.get("search_query") or "Unknown",
        "symbol": base.get("symbol") or coin.get("symbol"),
        "chain": first.get("chainId") or raw_data.get("chain_hint") or "unknown",
        "contract_address": base.get("address"),
        "image_url": ((coin.get("image") or {}).get("small") if isinstance(coin.get("image"), dict) else None),
        "market_cap": (((coin.get("market_data") or {}).get("market_cap") or {}).get("usd")),
        "volume_24h": (((coin.get("market_data") or {}).get("total_volume") or {}).get("usd")),
        "change_24h": (coin.get("market_data") or {}).get("price_change_percentage_24h"),
        # A user search already fetched the full CoinGecko asset document.
        # Reuse its reviewed community links immediately so non-Top-100 assets
        # do not need to wait for the scheduled source-discovery batch.
        "official_x": source_asset.get("official_x") or discovered_x,
        "telegram_chat": source_asset.get("telegram_chat") or discovered_telegram,
        "priority_tier": source_asset.get("priority_tier") or 3,
    }
    rank = 999
    if source_asset:
        metadata = _loads(source_asset.get("metadata_json"), {})
        rank = int(metadata.get("rank") or 999)
    upsert_social_asset(asset, rank=rank)
    if demo_social_enabled():
        # The hosted demo starts with Top 10 fixtures, then creates the same
        # clearly-labelled low-confidence fixture for a directly searched asset.
        # This keeps non-Top-10 demos usable without ever replacing real data.
        seed_demo_social_asset(asset, rank=rank if rank < 999 else 10)
    context = latest_social_context(
        asset_key, owner_address=owner_address,
        fallback_asset_key=registry_asset_key,
    )
    provider_status = social_provider_status()
    provider_states = context.get("providers") or {}
    refresh_providers: set[str] = set()
    demo_context_active = bool(context.get("demo_mode") and demo_social_enabled())
    if (
        not demo_context_active
        and not (provider_states.get("x") or {}).get("cache", {}).get("fresh")
    ):
        if (
            provider_status["x"]["shared_collector_configured"]
            or (provider_states.get("x") or {}).get("identity_connected")
        ):
            refresh_providers.add("x")
    telegram_state = provider_states.get("telegram") or {}
    telegram_source_available = bool(
        telegram_state.get("community_count")
        or (
            asset.get("telegram_chat")
            and provider_status["telegram"]["mtproto"]["configured"]
            and is_authorized_telegram_handle(asset.get("telegram_chat"))
        )
    )
    if (
        not demo_context_active
        and not telegram_state.get("cache", {}).get("fresh")
        and telegram_source_available
    ):
        refresh_providers.add("telegram")

    if refresh_providers:
        refresh_has_missing_data = any(
            (provider_states.get(provider) or {}).get("cache", {}).get("state")
            == "missing"
            for provider in refresh_providers
        )
        refresh_key = (
            asset_key,
            owner_address.lower() if owner_address else "shared",
            ",".join(sorted(refresh_providers)),
        )
        collection_task = _BACKGROUND_REFRESH_TASKS.get(refresh_key)
        created_refresh_task = False
        if not collection_task or collection_task.done():
            collection_task = asyncio.create_task(
                SocialCollector().collect_asset(
                    asset, owner_address, providers=refresh_providers,
                )
            )
            _BACKGROUND_REFRESH_TASKS[refresh_key] = collection_task
            _BACKGROUND_COLLECTION_TASKS.add(collection_task)
            created_refresh_task = True

        def finish_collection(task: asyncio.Task) -> None:
            _BACKGROUND_COLLECTION_TASKS.discard(task)
            if _BACKGROUND_REFRESH_TASKS.get(refresh_key) is task:
                _BACKGROUND_REFRESH_TASKS.pop(refresh_key, None)
            if not task.cancelled():
                try:
                    task.exception()
                except Exception:
                    pass

        if created_refresh_task:
            collection_task.add_done_callback(finish_collection)
        context["refresh_triggered"] = sorted(refresh_providers)
        if not refresh_has_missing_data:
            # Stale-while-revalidate: return the stored snapshot immediately
            # and refresh it in the background so analysis remains responsive.
            context["collection_pending"] = True
            context["served_from_cache"] = True
        else:
            try:
                collection_result = await asyncio.wait_for(
                    asyncio.shield(collection_task),
                    timeout=SOCIAL_INLINE_TIMEOUT_SECONDS,
                )
                context = latest_social_context(
                    asset_key, owner_address=owner_address,
                    fallback_asset_key=registry_asset_key,
                )
                context["refresh_triggered"] = sorted(refresh_providers)
                if collection_result.get("errors"):
                    context["collection_errors"] = collection_result["errors"]
                    first_error = next(iter(collection_result["errors"].values()))
                    context["collection_error"] = first_error.get("message")
            except asyncio.TimeoutError:
                context = latest_social_context(
                    asset_key, owner_address=owner_address,
                    fallback_asset_key=registry_asset_key,
                )
                context["collection_pending"] = True
                context["refresh_triggered"] = sorted(refresh_providers)
            except Exception as error:
                context = latest_social_context(
                    asset_key, owner_address=owner_address,
                    fallback_asset_key=registry_asset_key,
                )
                context["collection_error"] = str(error)
                context["refresh_triggered"] = sorted(refresh_providers)
    context["status"] = (
        "ready" if context["connected"]
        else "connected-no-data" if context.get("binding_connected")
        else "not-connected"
    )
    context["requires_user_binding"] = not context.get("binding_connected", False)
    context["requires_telegram_group"] = (
        (context.get("providers") or {}).get("telegram", {}).get("status")
        == "group_not_bound"
    )
    raw_data["social"] = context
    if context["connected"]:
        raw_data.setdefault("_sources", []).append("Social intelligence")
    return raw_data


_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop() -> None:
    interval = max(300, int(os.getenv("SOCIAL_SCHEDULER_INTERVAL_SECONDS", "900")))
    while True:
        try:
            await SocialCollector().run()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(interval)


def start_social_scheduler() -> asyncio.Task | None:
    global _scheduler_task
    if demo_social_enabled():
        return None
    if os.getenv("SOCIAL_SCHEDULER_ENABLED", "false").lower() != "true":
        return None
    if _scheduler_task and not _scheduler_task.done():
        return _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler_loop(), name="social-collector")
    return _scheduler_task


async def stop_social_scheduler() -> None:
    global _scheduler_task
    if not _scheduler_task:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None
