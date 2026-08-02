import hashlib
import hmac
import asyncio
import copy
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import database
import social
import telegram_mtproto


class SocialIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tempdir.name) / "social-integrations.db"
        database.init_db()
        self.key = Fernet.generate_key().decode("ascii")
        self.env = patch.dict(
            os.environ,
            {
                "SOCIAL_TOKEN_ENCRYPTION_KEY": self.key,
                "TELEGRAM_BOT_TOKEN": "123456:test-bot-token",
                "TELEGRAM_BOT_USERNAME": "meme_ops_test_bot",
                "X_CLIENT_ID": "test-client-id",
                "X_CLIENT_SECRET": "test-client-secret",
                "X_OAUTH_PUBLIC_CLIENT": "false",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        database.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_social_token_is_encrypted_and_never_returned(self):
        social._upsert_connection(
            "0xAAA", "x", "42", "operator", "plain-access-token",
            "plain-refresh-token", "tweet.read",
        )
        conn = database.get_connection()
        row = conn.execute("SELECT * FROM social_connections").fetchone()
        conn.close()
        self.assertNotIn("plain-access-token", row["access_token_encrypted"])
        self.assertEqual(
            social.decrypt_secret(row["access_token_encrypted"]),
            "plain-access-token",
        )
        public = social.list_connections("0xaaa")["connections"][0]
        self.assertNotIn("access_token_encrypted", public)
        self.assertNotIn("refresh_token_encrypted", public)

    def test_connection_status_is_wallet_isolated(self):
        social._upsert_connection("0xaaa", "x", "1", "a", "token-a")
        social._upsert_connection("0xbbb", "x", "2", "b", "token-b")
        self.assertEqual(
            social.list_connections("0xaaa")["connections"][0]["username"], "a"
        )
        self.assertEqual(
            social.list_connections("0xbbb")["connections"][0]["username"], "b"
        )

    def test_provider_status_identifies_missing_telegram_requirement(self):
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_BOT_USERNAME": "meme_ops_test_bot",
                "TELEGRAM_BOT_TOKEN": "",
            },
            clear=False,
        ):
            status = social.social_provider_status()
        self.assertTrue(status["encryption_configured"])
        self.assertTrue(status["x"]["client_id_configured"])
        self.assertTrue(status["telegram"]["bot_username_configured"])
        self.assertFalse(status["telegram"]["bot_token_configured"])
        self.assertFalse(status["telegram"]["login_configured"])

    def test_x_web_app_requires_client_secret_unless_public_pkce_is_explicit(self):
        with patch.dict(
            os.environ,
            {
                "X_CLIENT_ID": "test-client-id",
                "X_CLIENT_SECRET": "",
                "X_OAUTH_PUBLIC_CLIENT": "false",
            },
            clear=False,
        ):
            status = social.social_provider_status()
            self.assertFalse(status["x"]["oauth_configured"])
            with self.assertRaises(social.SocialConfigurationError):
                social.begin_x_connection("0xaaa", "https://example.com/")

        with patch.dict(
            os.environ,
            {
                "X_CLIENT_ID": "test-client-id",
                "X_CLIENT_SECRET": "",
                "X_OAUTH_PUBLIC_CLIENT": "true",
            },
            clear=False,
        ):
            status = social.social_provider_status()
            self.assertTrue(status["x"]["oauth_configured"])

    def test_oauth_state_is_temporary_and_one_time(self):
        pending = social._save_oauth_state("0xaaa", "x", "verifier")
        result = social._consume_oauth_state(pending, "x")
        self.assertEqual(result["owner_address"], "0xaaa")
        self.assertEqual(result["verifier"], "verifier")
        with self.assertRaises(ValueError):
            social._consume_oauth_state(pending, "x")

    def test_social_callbacks_use_request_domain_when_public_url_is_unset(self):
        with patch.dict(os.environ, {"APP_PUBLIC_URL": ""}, clear=False):
            x = social.begin_x_connection(
                "0xaaa", "https://memeops-production.up.railway.app/",
            )
            redirect_uri = parse_qs(
                urlparse(x["authorization_url"]).query
            )["redirect_uri"][0]
            telegram = social.begin_telegram_connection(
                "0xaaa", "https://memeops-production.up.railway.app/",
            )
        self.assertEqual(
            redirect_uri,
            "https://memeops-production.up.railway.app/api/social/x/callback",
        )
        self.assertTrue(
            telegram["callback_url"].startswith(
                "https://memeops-production.up.railway.app/api/social/telegram/callback"
            )
        )

    def test_telegram_login_signature_is_verified(self):
        payload = {
            "id": "123",
            "first_name": "Alice",
            "username": "alice",
            "auth_date": str(int(time.time())),
        }
        check = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
        secret = hashlib.sha256(b"123456:test-bot-token").digest()
        payload["hash"] = hmac.new(
            secret, check.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        self.assertTrue(social.verify_telegram_login(payload))
        payload["username"] = "mallory"
        self.assertFalse(social.verify_telegram_login(payload))

    def test_telegram_recent_reusable_proof_is_not_rejected_after_ten_minutes(self):
        payload = {
            "id": "123",
            "first_name": "Alice",
            "username": "alice",
            "auth_date": str(int(time.time()) - 3600),
        }
        check = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
        secret = hashlib.sha256(b"123456:test-bot-token").digest()
        payload["hash"] = hmac.new(
            secret, check.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        self.assertTrue(social.verify_telegram_login(payload))

    def test_invalid_telegram_proof_does_not_consume_wallet_state(self):
        state = social._save_oauth_state("0xaaa", "telegram")
        with self.assertRaisesRegex(ValueError, "same @BotFather bot"):
            social.complete_telegram_connection({
                "state": state,
                "id": "123",
                "auth_date": str(int(time.time())),
                "hash": "invalid",
            })
        pending = social._consume_oauth_state(state, "telegram")
        self.assertEqual(pending["owner_address"], "0xaaa")

    def test_inline_telegram_callback_is_bound_to_authenticated_wallet(self):
        state = social._save_oauth_state("0xaaa", "telegram")
        payload = {
            "state": state,
            "id": "123",
            "first_name": "Alice",
            "username": "alice",
            "auth_date": str(int(time.time())),
        }
        check = "\n".join(
            f"{key}={payload[key]}" for key in sorted(payload) if key != "state"
        )
        secret = hashlib.sha256(b"123456:test-bot-token").digest()
        payload["hash"] = hmac.new(
            secret, check.encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        with self.assertRaisesRegex(ValueError, "Login state"):
            social.complete_telegram_connection(payload, expected_owner="0xbbb")
        result = social.complete_telegram_connection(
            payload, expected_owner="0xaaa",
        )
        self.assertEqual(result["owner_address"], "0xaaa")
        connection = social.list_connections("0xaaa")["connections"][0]
        self.assertEqual(connection["username"], "alice")

    def test_telegram_bot_username_and_token_must_belong_to_same_bot(self):
        class FakeResponse:
            status_code = 200
            content = b"{}"

            @staticmethod
            def json():
                return {"ok": True, "result": {"id": 9, "username": "another_bot"}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, *args, **kwargs):
                return FakeResponse()

        social._TELEGRAM_BOT_VALIDATION_CACHE.clear()
        with patch.object(social.httpx, "AsyncClient", return_value=FakeClient()):
            with self.assertRaisesRegex(
                social.SocialConfigurationError, "configuration mismatch",
            ):
                asyncio.run(social.validate_telegram_bot_configuration(force=True))

    def test_same_symbol_on_different_chains_has_different_asset_key(self):
        eth = social.make_asset_key("ethereum", "0xABC")
        sol = social.make_asset_key("solana", "0xABC")
        self.assertNotEqual(eth, sol)

    def test_private_social_rag_is_visible_only_to_owner(self):
        asset_key = social.upsert_social_asset(
            {
                "coin_id": "dogecoin",
                "name": "Dogecoin",
                "symbol": "DOGE",
                "chain": "dogecoin",
            },
            rank=1,
        )
        social._save_snapshot(
            asset_key, "x", "shared-api",
            {"mentions_24h": 500, "confidence": 0.8},
        )
        social._save_snapshot(
            asset_key, "telegram", "wallet-bot",
            {"members": 1200, "confidence": 0.95},
            owner_address="0xaaa",
        )
        public = social.latest_social_context(asset_key)
        owner_a = social.latest_social_context(asset_key, owner_address="0xaaa")
        owner_b = social.latest_social_context(asset_key, owner_address="0xbbb")
        self.assertEqual({item["provider"] for item in public["metrics"]}, {"x"})
        self.assertEqual(
            {item["provider"] for item in owner_a["metrics"]}, {"x", "telegram"}
        )
        self.assertEqual({item["provider"] for item in owner_b["metrics"]}, {"x"})
        self.assertNotIn(
            "TELEGRAM",
            " ".join(item["content"] for item in owner_b["rag_documents"]),
        )

    def test_bound_identities_are_not_reported_as_disconnected_without_metrics(self):
        asset_key = social.upsert_social_asset(
            {
                "coin_id": "dogecoin",
                "name": "Dogecoin",
                "symbol": "DOGE",
                "chain": "dogecoin",
            },
            rank=1,
        )
        social._upsert_connection("0xaaa", "x", "1", "operator", "token")
        social._upsert_connection("0xaaa", "telegram", "2", "operator_tg", None)

        context = social.latest_social_context(asset_key, owner_address="0xaaa")

        self.assertFalse(context["connected"])
        self.assertTrue(context["binding_connected"])
        self.assertEqual(context["providers"]["x"]["status"], "connected_no_data")
        self.assertEqual(context["providers"]["telegram"]["status"], "group_not_bound")
        self.assertEqual(context["providers"]["reddit"]["status"], "not_configured")

    def test_collector_universe_never_drops_below_one_hundred(self):
        with patch.dict(os.environ, {"SOCIAL_ASSET_UNIVERSE_SIZE": "10"}):
            self.assertEqual(social.SocialCollector().universe_size, 100)

    def test_provider_cache_state_distinguishes_fresh_stale_and_missing(self):
        asset_key = social.upsert_social_asset(
            {"coin_id": "dogecoin", "name": "Dogecoin", "symbol": "DOGE"},
            rank=1,
        )
        self.assertEqual(
            social.social_cache_state(asset_key, "x")["state"], "missing",
        )
        social._save_snapshot(
            asset_key, "x", "shared-api",
            {"mentions_24h": 12, "confidence": 0.8},
        )
        self.assertEqual(
            social.social_cache_state(
                asset_key, "x", max_age_seconds=60,
            )["state"],
            "fresh",
        )
        conn = database.get_connection()
        conn.execute(
            "UPDATE social_metric_snapshots SET collected_at = ? WHERE asset_key = ?",
            ("2020-01-01T00:00:00+00:00", asset_key),
        )
        conn.commit()
        conn.close()
        self.assertEqual(
            social.social_cache_state(
                asset_key, "x", max_age_seconds=60,
            )["state"],
            "stale",
        )

    def test_scheduler_refreshes_stale_providers_independently(self):
        asset_key = social.upsert_social_asset(
            {
                "coin_id": "dogecoin", "name": "Dogecoin", "symbol": "DOGE",
                "telegram_chat": "dogecoin",
            },
            rank=1,
        )
        social._save_snapshot(
            asset_key, "x", "shared-api",
            {"mentions_24h": 12, "confidence": 0.8},
        )
        asset = social.list_social_assets(1)[0]
        with patch.dict(
            os.environ,
            {
                "X_BEARER_TOKEN": "app-token",
                "TELEGRAM_API_ID": "123",
                "TELEGRAM_API_HASH": "hash",
                "TELEGRAM_MTPROTO_SESSION": "session",
                "TELEGRAM_MTPROTO_ALLOWED_CHATS": "dogecoin",
            },
            clear=False,
        ):
            due = social.SocialCollector()._providers_due(asset)
        self.assertNotIn("x", due)
        self.assertIn("telegram", due)

    def test_mtproto_handle_normalization_and_secret_free_status(self):
        self.assertEqual(
            telegram_mtproto.normalize_telegram_handle(
                "https://t.me/dogecoin?start=1"
            ),
            "dogecoin",
        )
        self.assertEqual(
            telegram_mtproto.normalize_telegram_handle("Saved Messages"), "",
        )
        with patch.dict(
            os.environ,
            {
                "TELEGRAM_API_ID": "123",
                "TELEGRAM_API_HASH": "super-secret-hash",
                "TELEGRAM_MTPROTO_SESSION": "super-secret-session",
                "TELEGRAM_MTPROTO_ALLOWED_CHATS": "dogecoin",
            },
            clear=False,
        ):
            status = telegram_mtproto.mtproto_provider_status()
            self.assertTrue(status["configured"])
            self.assertEqual(status["authorized_chat_count"], 1)
            self.assertNotIn("super-secret", str(status))
            self.assertTrue(
                telegram_mtproto.is_authorized_telegram_handle("@dogecoin")
            )
            self.assertFalse(
                telegram_mtproto.is_authorized_telegram_handle("@unknown")
            )

    def test_empty_source_discovery_is_timestamped_to_avoid_request_loops(self):
        asset_key = social.upsert_social_asset(
            {"coin_id": "unknown-meme", "name": "Unknown", "symbol": "UNK"},
            rank=100,
        )
        social.update_social_asset_sources(asset_key)
        asset = next(
            item for item in social.list_social_assets(100)
            if item["asset_key"] == asset_key
        )
        self.assertIsNotNone(asset["source_discovery_at"])
        self.assertIsNone(asset["official_x"])
        self.assertIsNone(asset["telegram_chat"])

    def test_parallel_cache_misses_share_one_provider_refresh(self):
        asset_key = social.upsert_social_asset(
            {"coin_id": "dogecoin", "name": "Dogecoin", "symbol": "DOGE"},
            rank=1,
        )
        social._upsert_connection("0xaaa", "x", "1", "operator", "token")
        raw = {
            "search_query": "dogecoin",
            "coingecko": {"id": "dogecoin", "name": "Dogecoin"},
            "dexscreener": {"pairs": []},
        }
        calls = []

        async def fake_collect(_collector, _asset, owner_address=None, providers=None):
            calls.append((owner_address, providers))
            await asyncio.sleep(0.02)
            social._save_snapshot(
                asset_key, "x", "wallet-oauth-search",
                {"mentions_24h": 9, "confidence": 0.8},
                owner_address=owner_address,
            )
            return {"providers": {"x": {}}, "errors": {}}

        async def run_parallel():
            return await asyncio.gather(
                social.enrich_raw_data_with_social(copy.deepcopy(raw), "0xaaa"),
                social.enrich_raw_data_with_social(copy.deepcopy(raw), "0xaaa"),
            )

        social._BACKGROUND_REFRESH_TASKS.clear()
        with patch.object(social.SocialCollector, "collect_asset", new=fake_collect):
            results = asyncio.run(run_parallel())
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(item["social"]["connected"] for item in results))

    def test_chain_asset_can_reuse_same_coingecko_registry_without_symbol_merge(self):
        registry_key = social.upsert_social_asset(
            {"coin_id": "dogecoin", "name": "Dogecoin", "symbol": "DOGE"},
            rank=1,
        )
        social._save_snapshot(
            registry_key, "x", "shared-api",
            {"mentions_24h": 77, "confidence": 0.8},
        )
        exact_key = social.upsert_social_asset(
            {
                "chain": "solana", "contract_address": "DOGE123",
                "name": "Dogecoin", "symbol": "DOGE",
            },
            rank=999,
        )
        reused = social.latest_social_context(
            exact_key, fallback_asset_key=registry_key,
        )
        unrelated_key = social.upsert_social_asset(
            {
                "chain": "base", "contract_address": "OTHER123",
                "name": "Another Doge", "symbol": "DOGE",
            },
            rank=999,
        )
        unrelated = social.latest_social_context(unrelated_key)
        self.assertTrue(reused["connected"])
        self.assertEqual(reused["metrics"][0]["mentions_24h"], 77)
        self.assertFalse(unrelated["connected"])

    def test_user_search_persists_discovered_public_social_sources(self):
        raw = {
            "search_query": "new-meme",
            "coingecko": {
                "id": "new-meme",
                "name": "New Meme",
                "symbol": "NEW",
                "links": {
                    "twitter_screen_name": "new_meme_official",
                    "telegram_channel_identifier": "new_meme_chat",
                },
            },
            "dexscreener": {"pairs": []},
        }
        enriched = asyncio.run(social.enrich_raw_data_with_social(raw, None))
        asset = next(
            item for item in social.list_social_assets(500)
            if item["asset_key"] == "coingecko:new-meme"
        )
        self.assertEqual(asset["official_x"], "new_meme_official")
        self.assertEqual(asset["telegram_chat"], "new_meme_chat")
        self.assertEqual(enriched["social"]["status"], "not-connected")

    def test_demo_seed_is_labelled_idempotent_and_never_replaces_real_data(self):
        keys = []
        for rank in range(1, 11):
            keys.append(social.upsert_social_asset({
                "coin_id": f"demo-{rank}",
                "name": f"Demo {rank}",
                "symbol": f"D{rank}",
                "market_cap": 1_000_000 / rank,
                "volume_24h": 100_000 / rank,
            }, rank=rank))
        social._save_snapshot(
            keys[0], "x", "shared-api",
            {"mentions_24h": 99, "confidence": 0.8},
        )

        first = social.seed_demo_social_snapshots(10)
        second = social.seed_demo_social_snapshots(10)

        self.assertEqual(first["snapshot_count"], 19)
        self.assertEqual(first["skipped_real"], 1)
        self.assertEqual(second["snapshot_count"], 0)
        context = social.latest_social_context(keys[0])
        self.assertTrue(context["demo_mode"])
        x_metric = next(
            item for item in context["metrics"] if item["provider"] == "x"
        )
        telegram_metric = next(
            item for item in context["metrics"] if item["provider"] == "telegram"
        )
        self.assertEqual(x_metric["source_mode"], "shared-api")
        self.assertEqual(telegram_metric["source_mode"], "demo-synthetic-v1")
        self.assertEqual(
            telegram_metric["raw_summary"]["synthetic"], True,
        )

    def test_railway_demo_default_can_be_explicitly_disabled(self):
        previous = os.environ.pop("DEMO_SOCIAL_DATA_ENABLED", None)
        try:
            with patch.dict(
                os.environ, {"RAILWAY_ENVIRONMENT_ID": "railway-demo"},
                clear=False,
            ):
                self.assertTrue(social.demo_social_enabled())
                os.environ["DEMO_SOCIAL_DATA_ENABLED"] = "false"
                self.assertFalse(social.demo_social_enabled())
        finally:
            os.environ.pop("DEMO_SOCIAL_DATA_ENABLED", None)
            if previous is not None:
                os.environ["DEMO_SOCIAL_DATA_ENABLED"] = previous

    def test_x_payment_error_is_actionable_and_secret_free(self):
        class FakeResponse:
            status_code = 402
            content = b'{"title":"Payment Required","detail":"credits depleted"}'

            @staticmethod
            def json():
                return {"title": "Payment Required", "detail": "credits depleted"}

        error = social._x_collection_error(FakeResponse(), "recent-counts")
        self.assertEqual(error["code"], "credits_depleted")
        self.assertIn("Add credits", error["message"])
        self.assertNotIn("test-bot-token", str(error))

    def test_bound_telegram_activity_is_aggregated_without_message_text(self):
        conn = database.get_connection()
        community_id = conn.execute(
            """INSERT INTO social_communities
               (owner_address, provider, external_community_id, community_name)
               VALUES ('0xaaa', 'telegram', '-10042', 'DOGE Ops')"""
        ).lastrowid
        conn.commit()
        conn.close()
        result = social._record_telegram_activity(
            {"update_id": 9001},
            {
                "message_id": 12,
                "date": int(time.time()),
                "text": "private message content must not be stored",
                "from": {"id": 77},
                "chat": {"id": -10042, "type": "supergroup"},
            },
        )
        self.assertEqual(result["action"], "activity-recorded")
        conn = database.get_connection()
        row = conn.execute(
            "SELECT * FROM telegram_activity_events WHERE community_id = ?",
            (community_id,),
        ).fetchone()
        columns = set(row.keys())
        conn.close()
        self.assertNotIn("text", columns)
        self.assertNotEqual(row["sender_hash"], "77")
        self.assertEqual(len(row["sender_hash"]), 64)


if __name__ == "__main__":
    unittest.main()
