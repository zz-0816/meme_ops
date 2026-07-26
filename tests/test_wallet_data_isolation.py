import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import database


class WalletDataIsolationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = database.DB_PATH
        database.DB_PATH = Path(self.tempdir.name) / "wallet-isolation.db"
        database.init_db()
        self.wallet_a = "0xaaa"
        self.wallet_b = "0xbbb"
        database.upsert_user(self.wallet_a)
        database.upsert_user(self.wallet_b)
        report = {
            "token": {"name": "Pepe", "chain": "solana"},
            "dimensions": [],
            "data_sources": [],
        }
        self.analysis_a = database.save_analysis(
            "Pepe", "pepe sol", report, 7.0, "green",
            owner_address=self.wallet_a,
        )
        self.watchlist_a = database.add_to_watchlist(
            self.wallet_a, "Pepe", "solana",
        )
        self.comparison_a = database.save_comparison_report(
            self.wallet_a,
            "Pepe vs Doge",
            "investor",
            {
                "assets": [{"name": "Pepe"}, {"name": "Doge"}],
                "winner": {"name": "Pepe"},
                "generation_mode": "rules",
            },
        )

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tempdir.cleanup()

    def test_history_is_visible_only_to_owner(self):
        self.assertEqual(len(database.get_history(self.wallet_a)), 1)
        self.assertEqual(database.get_history(self.wallet_b), [])

    def test_analysis_detail_is_visible_only_to_owner(self):
        self.assertIsNotNone(database.get_analysis_detail(self.analysis_a, self.wallet_a))
        self.assertIsNone(database.get_analysis_detail(self.analysis_a, self.wallet_b))

    def test_watchlist_is_visible_only_to_owner(self):
        self.assertEqual(len(database.get_watchlist(self.wallet_a)), 1)
        self.assertEqual(database.get_watchlist(self.wallet_b), [])

    def test_other_wallet_cannot_delete_or_edit_watchlist(self):
        self.assertFalse(database.delete_watchlist_item(self.watchlist_a, self.wallet_b))
        database.update_watchlist_note(self.watchlist_a, "intrusion", self.wallet_b)
        self.assertIsNone(database.get_watchlist(self.wallet_a)[0]["notes"])

    def test_comparison_reports_are_private_to_wallet(self):
        self.assertEqual(len(database.get_comparison_reports(self.wallet_a)), 1)
        self.assertEqual(database.get_comparison_reports(self.wallet_b), [])
        self.assertIsNone(
            database.get_comparison_report(self.comparison_a, self.wallet_b)
        )
        self.assertFalse(
            database.delete_comparison_report(self.comparison_a, self.wallet_b)
        )


if __name__ == "__main__":
    unittest.main()
