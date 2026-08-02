import base64
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from intent import extract_analysis_intent, infer_writing_profile
from nft import build_metadata
from asset_resolver import is_contract_address, select_exact_pairs
from poster_planner import fallback_poster_plan


class NFTAndIntentTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "token": {"name": "Doge", "symbol": "DOGE", "chain": "solana"},
            "persona": "investor",
            "overall_score": 6.8,
            "risk_level": "yellow",
            "dimensions": [
                {"dimension": "On-chain Liquidity", "score": 7.1},
                {"dimension": "Holder Activity", "score": 6.4},
            ],
        }

    def test_metadata_contains_viewable_unique_poster(self):
        metadata = build_metadata(self.report, 42, "Ocean editorial")
        self.assertTrue(metadata["image"].startswith("data:image/svg+xml;base64,"))
        svg = base64.b64decode(metadata["image"].split(",", 1)[1]).decode("utf-8")
        self.assertIn(metadata["poster_id"], svg)
        self.assertIn("On-chain Liquidity", svg)
        self.assertIn("6.8", svg)

    def test_style_changes_visual_not_report_facts(self):
        cyber = build_metadata(self.report, 42, "Cyberpunk")
        minimal = build_metadata(self.report, 42, "Minimal")
        self.assertNotEqual(cyber["image"], minimal["image"])
        self.assertEqual(
            next(a["value"] for a in cyber["attributes"] if a["trait_type"] == "Overall Score"),
            6.8,
        )
        self.assertEqual(
            next(a["value"] for a in minimal["attributes"] if a["trait_type"] == "Overall Score"),
            6.8,
        )

    def test_conversational_chinese_request_is_parsed(self):
        intent = extract_analysis_intent(
            "我想要分析的是doge 是sol链的 我想要更准确更简洁 但是又更加亲近的语气输出"
        )
        self.assertEqual(intent["token_query"].lower(), "doge")
        self.assertEqual(intent["chain"], "solana")
        self.assertIn("亲近", intent["style_instruction"])

    def test_dogecoin_solana_is_not_confused_with_play_solana(self):
        intent = extract_analysis_intent("Dogecoin solana")
        self.assertEqual(intent["token_query"].lower(), "dogecoin")
        self.assertEqual(intent["chain"], "solana")
        pairs = [
            {
                "chainId": "solana",
                "baseToken": {"name": "Play Solana", "symbol": "PLAY"},
                "liquidity": {"usd": 50_000_000},
            },
            {
                "chainId": "solana",
                "baseToken": {"name": "Dogecoin", "symbol": "DOGE"},
                "liquidity": {"usd": 100_000},
            },
            {
                "chainId": "ethereum",
                "baseToken": {"name": "Dogecoin", "symbol": "DOGE"},
                "liquidity": {"usd": 90_000_000},
            },
        ]
        selected = select_exact_pairs(pairs, "Dogecoin", "solana")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["baseToken"]["symbol"], "DOGE")

    def test_style_description_is_not_printed_verbatim_on_poster(self):
        style = "Japanese football with a futuristic technology building"
        metadata = build_metadata(self.report, 42, style)
        svg = base64.b64decode(metadata["image"].split(",", 1)[1]).decode("utf-8")
        self.assertNotIn(style, svg)

    def test_writing_profiles_are_materially_different(self):
        friendly = infer_writing_profile("friendly, concise, and approachable")
        academic = infer_writing_profile("academic report with professional terminology")
        self.assertEqual(friendly["tone"], "friendly")
        self.assertEqual(friendly["length"], "compact")
        self.assertEqual(academic["tone"], "academic")
        self.assertEqual(academic["depth"], "academic")
        self.assertNotEqual(friendly, academic)

    def test_chinese_plain_concise_keywords_are_recognized(self):
        profile = infer_writing_profile("简洁、清晰、容易理解，少用复杂术语")
        self.assertEqual(profile["tone"], "friendly")
        self.assertEqual(profile["depth"], "concise")
        self.assertEqual(profile["length"], "compact")
        self.assertEqual(profile["clarity"], "plain")

    def test_solana_contract_address_is_detected(self):
        self.assertTrue(is_contract_address("6Cd12aUdg5UyWg4L7SRsgL9UeE4azpeSbugkDiHJSxwH"))

    def test_poster_copy_plan_changes_density_and_layout(self):
        report = {
            **self.report,
            "poster_facts": [
                {"id": "score", "label": "Overall score", "formatted": "6.8/10"},
                {"id": "liquidity", "label": "DEX liquidity", "formatted": "$17,661,817"},
                {"id": "volume_24h", "label": "24h volume", "formatted": "$741,236"},
                {"id": "pair_count", "label": "Active pairs", "formatted": "7"},
                {"id": "transactions_24h", "label": "24h transactions", "formatted": "1,250"},
            ],
            "report_keywords": ["deep liquidity", "moderate turnover"],
        }
        concise = fallback_poster_plan(report, "Place concise technical copy on both sides")
        academic = fallback_poster_plan(report, "Academic research report with professional terminology")
        self.assertEqual(concise["layout"], "sides")
        self.assertEqual(concise["copy_density"], "minimal")
        self.assertEqual(academic["copy_density"], "academic")
        self.assertGreater(len(academic["selected_fact_ids"]), len(concise["selected_fact_ids"]))

        metadata = build_metadata(
            report, 99, "two side technology layout",
            content_plan=concise,
        )
        svg = base64.b64decode(metadata["image"].split(",", 1)[1]).decode("utf-8")
        self.assertIn("$17,661,817", svg)
        self.assertIn("24h volume", svg)


if __name__ == "__main__":
    unittest.main()
