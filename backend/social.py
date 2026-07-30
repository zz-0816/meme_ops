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
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken

from database import get_connection


X_AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
X_API_URL = "https://api.x.com/2"
TELEGRAM_BOT_API = "https://api.telegram.org"
DEFAULT_SCOPES = "tweet.read users.read follows.read offline.access"


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
            "oauth_configured": x_client_id_configured,
            "shared_collector_configured": x_bearer_configured,
            "client_id_configured": x_client_id_configured,
            "client_secret_configured": x_client_secret_configured,
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
        },
        "scheduler": {
            "enabled": os.getenv("SOCIAL_SCHEDULER_ENABLED", "false").lower() == "true",
            "universe_size": max(100, int(os.getenv("SOCIAL_ASSET_UNIVERSE_SIZE", "100"))),
            "interval_seconds": max(300, int(os.getenv("SOCIAL_SCHEDULER_INTERVAL_SECONDS", "900"))),
        },
    }


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


def _consume_oauth_state(state: str, provider: str) -> dict:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM social_oauth_states
               WHERE state = ? AND provider = ? AND expires_at >= ?""",
            (state, provider, _iso()),
        ).fetchone()
        if not row:
            raise ValueError("Login state is invalid, expired, or already used")
        conn.execute("DELETE FROM social_oauth_states WHERE state = ?", (state,))
        conn.commit()
        result = dict(row)
        result["verifier"] = decrypt_secret(result.pop("verifier_encrypted", None))
        return result
    finally:
        conn.close()


def _public_app_url() -> str:
    return os.getenv("APP_PUBLIC_URL", "http://127.0.0.1:8788").rstrip("/")


def begin_x_connection(owner_address: str) -> dict:
    client_id = os.getenv("X_CLIENT_ID", "").strip()
    if not client_id:
        raise SocialConfigurationError("X_CLIENT_ID is not configured")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    state = _save_oauth_state(owner_address, "x", verifier)
    callback = f"{_public_app_url()}/api/social/x/callback"
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


async def complete_x_connection(code: str, state: str) -> dict:
    pending = _consume_oauth_state(state, "x")
    client_id = os.getenv("X_CLIENT_ID", "").strip()
    client_secret = os.getenv("X_CLIENT_SECRET", "").strip()
    callback = f"{_public_app_url()}/api/social/x/callback"
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


def begin_telegram_connection(owner_address: str) -> dict:
    username = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    if not username or not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
        raise SocialConfigurationError(
            "TELEGRAM_BOT_USERNAME and TELEGRAM_BOT_TOKEN are required"
        )
    state = _save_oauth_state(owner_address, "telegram")
    return {
        "bot_username": username,
        "callback_url": f"{_public_app_url()}/api/social/telegram/callback?state={state}",
        "state": state,
    }


def verify_telegram_login(payload: dict, bot_token: str | None = None) -> bool:
    supplied_hash = str(payload.get("hash") or "")
    auth_date = str(payload.get("auth_date") or "")
    if not supplied_hash or not auth_date:
        return False
    try:
        if abs(int(_utcnow().timestamp()) - int(auth_date)) > 600:
            return False
    except ValueError:
        return False
    data_check = "\n".join(
        f"{key}={payload[key]}"
        for key in sorted(payload)
        if key not in {"hash", "state"} and payload[key] is not None
    )
    secret = hashlib.sha256(
        (bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")).encode("utf-8")
    ).digest()
    expected = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, supplied_hash)


def complete_telegram_connection(payload: dict) -> dict:
    state = str(payload.get("state") or "")
    pending = _consume_oauth_state(state, "telegram")
    if not verify_telegram_login(payload):
        raise ValueError("Telegram login signature is invalid or expired")
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
        return {"accepted": True, "action": "ignored"}
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


def latest_social_context(
    asset_key: str, limit: int = 8, owner_address: str | None = None,
) -> dict:
    conn = get_connection()
    try:
        owner = owner_address.lower() if owner_address else None
        metrics = [
            dict(row) for row in conn.execute(
                """SELECT m.* FROM social_metric_snapshots m
                   JOIN (
                     SELECT provider, owner_address, MAX(collected_at) collected_at
                     FROM social_metric_snapshots WHERE asset_key = ?
                       AND (owner_address IS NULL OR owner_address = ?)
                     GROUP BY provider, owner_address
                   ) latest
                   ON latest.provider = m.provider
                  AND COALESCE(latest.owner_address, '') = COALESCE(m.owner_address, '')
                  AND latest.collected_at = m.collected_at
                   WHERE m.asset_key = ?
                   ORDER BY m.confidence DESC""",
                (asset_key, owner, asset_key),
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
                """SELECT title, content, platform, document_type, keywords_json,
                          confidence, source_mode, collected_at
                   FROM social_rag_documents WHERE asset_key = ?
                     AND (owner_address IS NULL OR owner_address = ?)
                     AND (expires_at IS NULL OR expires_at >= ?)
                   ORDER BY collected_at DESC LIMIT ?""",
                (asset_key, owner, _iso(), max(1, min(limit, 20))),
            ).fetchall()
        ]
        for item in metrics:
            item["raw_summary"] = _loads(item.pop("raw_summary_json", None), {})
        for item in documents:
            item["keywords"] = _loads(item.pop("keywords_json", None), [])
        return {
            "asset_key": asset_key,
            "connected": bool(metrics),
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


class SocialCollector:
    """Refresh a ranked meme universe and collect supported social signals."""

    def __init__(self) -> None:
        self.universe_size = max(100, int(os.getenv("SOCIAL_ASSET_UNIVERSE_SIZE", "100")))

    def _is_due(self, asset: dict) -> bool:
        if not os.getenv("X_BEARER_TOKEN", "").strip():
            return True
        tier = int(asset.get("priority_tier") or 3)
        minutes = 15 if tier == 1 else 60 if tier == 2 else 360
        conn = get_connection()
        try:
            row = conn.execute(
                """SELECT MAX(collected_at) AS collected_at
                   FROM social_metric_snapshots
                   WHERE asset_key = ? AND provider = 'x' AND owner_address IS NULL""",
                (asset["asset_key"],),
            ).fetchone()
        finally:
            conn.close()
        if not row or not row["collected_at"]:
            return True
        try:
            return datetime.fromisoformat(str(row["collected_at"])) <= _utcnow() - timedelta(minutes=minutes)
        except ValueError:
            return True

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
        return list_social_assets(self.universe_size)

    async def collect_asset(
        self,
        asset: dict,
        owner_address: str | None = None,
    ) -> dict:
        token = os.getenv("X_BEARER_TOKEN", "").strip()
        source_mode = "shared-api"
        if not token and owner_address:
            token = await _wallet_x_access_token(owner_address)
            if token:
                source_mode = "wallet-oauth"
        results: dict[str, Any] = {"asset_key": asset["asset_key"], "providers": {}}
        if token:
            query_parts = []
            symbol = str(asset.get("symbol") or "").strip()
            name = str(asset.get("name") or "").strip()
            if symbol:
                query_parts.append(f'"${symbol}"')
            if name and name.lower() != symbol.lower():
                query_parts.append(f'"{name}"')
            query = " OR ".join(query_parts) or f'"{asset["asset_key"]}"'
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    f"{X_API_URL}/tweets/counts/recent",
                    headers={"Authorization": f"Bearer {token}"},
                    params={"query": query, "granularity": "day"},
                )
                response.raise_for_status()
                payload = response.json()
                engagements = None
                active_authors = None
                if int(asset.get("priority_tier") or 3) == 1:
                    detail_response = await client.get(
                        f"{X_API_URL}/tweets/search/recent",
                        headers={"Authorization": f"Bearer {token}"},
                        params={
                            "query": query,
                            "max_results": 10,
                            "tweet.fields": "author_id,public_metrics,created_at",
                        },
                    )
                    if detail_response.status_code == 200:
                        posts = detail_response.json().get("data") or []
                        active_authors = len({
                            item.get("author_id") for item in posts if item.get("author_id")
                        })
                        engagements = sum(
                            sum(
                                int((item.get("public_metrics") or {}).get(key) or 0)
                                for key in ("like_count", "reply_count", "retweet_count", "quote_count")
                            )
                            for item in posts
                        )
            mentions = int(
                (payload.get("meta") or {}).get("total_tweet_count")
                or sum(int(item.get("tweet_count") or 0) for item in payload.get("data") or [])
            )
            metrics = {
                "mentions_24h": mentions,
                "posts_24h": mentions,
                "active_authors_24h": active_authors,
                "engagements_24h": engagements,
                "engagement_rate": (
                    round(engagements / max(mentions, 1), 6)
                    if engagements is not None else None
                ),
                "confidence": 0.85,
                "raw_summary": {"query": query, "provider_window": "recent-counts"},
            }
            _save_snapshot(
                asset["asset_key"], "x", source_mode, metrics,
                owner_address=owner_address if source_mode == "wallet-oauth" else None,
            )
            results["providers"]["x"] = metrics

        if owner_address:
            await self._collect_telegram_for_owner(asset, owner_address, results)
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
            metrics = {
                "members": members_total,
                "confidence": 0.95,
                "raw_summary": {"connected_communities": success},
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
        assets = [asset for asset in assets if self._is_due(asset)]
        semaphore = asyncio.Semaphore(max(1, int(os.getenv("SOCIAL_COLLECTOR_CONCURRENCY", "4"))))

        async def collect_one(asset: dict) -> None:
            nonlocal success
            async with semaphore:
                try:
                    await self.collect_asset(asset)
                    success += 1
                except Exception as error:
                    errors.append(f"{asset.get('asset_key')}: {error}")

        await asyncio.gather(*(collect_one(asset) for asset in assets))
        conn = get_connection()
        try:
            conn.execute(
                """UPDATE social_sync_runs SET asset_count = ?, success_count = ?,
                   error_count = ?, status = ?, details_json = ?, finished_at = ?
                   WHERE id = ?""",
                (
                    len(assets),
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
            "asset_count": len(assets),
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
    }
    rank = 999
    if existing:
        metadata = _loads(existing.get("metadata_json"), {})
        rank = int(metadata.get("rank") or 999)
    upsert_social_asset(asset, rank=rank)
    context = latest_social_context(asset_key, owner_address=owner_address)
    if not context["connected"] and owner_address:
        has_connection = _get_connection(owner_address, "x") or _get_connection(owner_address, "telegram")
        if has_connection:
            try:
                await SocialCollector().collect_asset(asset, owner_address)
                context = latest_social_context(asset_key, owner_address=owner_address)
            except Exception as error:
                context["collection_error"] = str(error)
    context["status"] = "connected" if context["connected"] else "not-connected"
    context["requires_user_binding"] = not context["connected"]
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
