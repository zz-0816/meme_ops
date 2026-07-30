import hashlib
import hmac
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import database
import social


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

    def test_oauth_state_is_temporary_and_one_time(self):
        pending = social._save_oauth_state("0xaaa", "x", "verifier")
        result = social._consume_oauth_state(pending, "x")
        self.assertEqual(result["owner_address"], "0xaaa")
        self.assertEqual(result["verifier"], "verifier")
        with self.assertRaises(ValueError):
            social._consume_oauth_state(pending, "x")

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

    def test_collector_universe_never_drops_below_one_hundred(self):
        with patch.dict(os.environ, {"SOCIAL_ASSET_UNIVERSE_SIZE": "10"}):
            self.assertEqual(social.SocialCollector().universe_size, 100)


if __name__ == "__main__":
    unittest.main()
