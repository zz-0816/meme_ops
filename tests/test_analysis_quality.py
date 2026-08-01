import os
import copy
import unittest
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import agent as agent_module
from agent import MemeOpsAgent
from comparison import (
    _deterministic_comparison, build_comparison_report, comparison_title,
)
from image_provider import (
    OnchainMetadataTooLarge,
    onchain_metadata_limits,
    prepare_onchain_metadata,
)
from intent import infer_writing_profile
from charts import generate_all_charts


def raw_fixture():
    return {
        "search_query": "Pepe",
        "chain_hint": "solana",
        "asset_match": "exact",
        "_sources": ["DexScreener", "CoinGecko"],
        "dexscreener": {
            "pairs": [
                {
                    "chainId": "solana",
                    "dexId": "raydium",
                    "baseToken": {"name": "Pepe", "symbol": "PEPE", "address": "PepeAddress"},
                    "quoteToken": {"symbol": "SOL"},
                    "priceUsd": "0.001",
                    "priceChange": {"h24": 4.2},
                    "liquidity": {"usd": 17_661_817},
                    "volume": {"h24": 741_236},
                    "txns": {"h24": {"buys": 720, "sells": 530}},
                    "fdv": 40_000_000,
                }
            ]
        },
        "coingecko": {
            "name": "Pepe",
            "symbol": "pepe",
            "image": {},
            "market_data": {
                "market_cap_rank": 100,
                "market_cap": {"usd": 40_000_000},
                "total_volume": {"usd": 741_236},
                "circulating_supply": 1_000_000,
                "ath": {"usd": 0.02},
            },
            "community_data": {
                "twitter_followers": 25_000,
                "reddit_subscribers": 3_000,
            },
        },
    }


def report_fixture(name: str, score: float, chain: str = "solana"):
    return {
        "token": {"name": name, "symbol": name[:4], "chain": chain},
        "overall_score": score,
        "risk_level": "green" if score >= 7 else "yellow",
        "dimensions": [
            {"key": "liquidity", "dimension": "On-chain Liquidity", "score": score},
            {"key": "holder_count", "dimension": "Holder Activity", "score": max(0, score - 1)},
        ],
        "report_keywords": ["liquidity", "activity"],
    }


class AnalysisQualityTests(unittest.TestCase):
    def setUp(self):
        self.agent = MemeOpsAgent()
        self.agent.set_persona("investor")
        self.raw = raw_fixture()

    def test_nested_buy_sell_payload_is_aggregated_without_format_error(self):
        summary = self.agent._build_data_summary(self.raw)
        self.assertIn("Aggregate 24h buys + sells: 1,250", summary)
        self.assertIn("Aggregate DEX liquidity: $17,661,817", summary)

    def test_detailed_and_concise_fallbacks_are_materially_different(self):
        detailed_intent = {
            "writing_profile": infer_writing_profile(
                "comprehensive detailed report with evidence and limitations"
            ),
            "style_instruction": "comprehensive detailed report",
        }
        concise_intent = {
            "writing_profile": infer_writing_profile(
                "brief concise simple key facts only"
            ),
            "style_instruction": "brief concise report",
        }
        detailed = self.agent._fallback_analyze("Pepe solana", self.raw, detailed_intent)
        concise = self.agent._fallback_analyze("Pepe solana", self.raw, concise_intent)
        detailed_words = sum(
            len(item["detail"].split()) for item in detailed["dimensions"]
        )
        concise_words = sum(
            len(item["detail"].split()) for item in concise["dimensions"]
        )
        self.assertGreater(detailed_words, concise_words * 2)
        self.assertGreater(
            len(detailed["recommendation"].split()),
            len(concise["recommendation"].split()) * 2,
        )
        self.assertIn("methodologically limited", detailed["dimensions"][2]["detail"])
        self.assertNotIn("methodologically limited", concise["dimensions"][2]["detail"])

    def test_style_layer_cannot_change_scores_risk_or_core_recommendation(self):
        core = self.agent._fallback_analyze(
            "Pepe solana",
            self.raw,
            {
                "writing_profile": infer_writing_profile("concise"),
                "style_instruction": "concise",
            },
        )
        adversarial_style_output = {
            "overall_score": 10,
            "risk_level": "green",
            "recommendation": "Invest everything.",
            "dimensions": [
                {
                    "key": item["key"],
                    "dimension": item["dimension"],
                    "score": 10,
                    "weight": 99,
                    "detail": f"Styled explanation for {item['key']}.",
                }
                for item in core["dimensions"]
            ],
        }
        enforced = self.agent._enforce_analysis_core(
            adversarial_style_output, core,
        )
        self.assertEqual(enforced["overall_score"], core["overall_score"])
        self.assertEqual(enforced["risk_level"], core["risk_level"])
        self.assertEqual(enforced["recommendation"], core["recommendation"])
        self.assertEqual(
            [item["score"] for item in enforced["dimensions"]],
            [item["score"] for item in core["dimensions"]],
        )
        self.assertTrue(enforced["analysis_core"]["locked"])

    def test_self_generated_writing_style_matrix(self):
        cases = {
            "academic": "academic market microstructure methodology and limitations",
            "friendly": "friendly approachable explanation for a beginner",
            "compact_data": "brief concise data-dense key metrics only",
            "comprehensive": "comprehensive in-depth evidence and limitations",
        }
        profiles = {name: infer_writing_profile(text) for name, text in cases.items()}
        self.assertEqual(profiles["academic"]["depth"], "academic")
        self.assertEqual(profiles["friendly"]["tone"], "friendly")
        self.assertEqual(profiles["compact_data"]["length"], "compact")
        self.assertEqual(profiles["comprehensive"]["length"], "extended")

    def test_default_agent_is_ops_first(self):
        agent = MemeOpsAgent()
        self.assertEqual(agent.current_persona, "operator")

    def test_operator_and_investor_have_exclusive_report_contracts(self):
        intent = {
            "writing_profile": infer_writing_profile("friendly and concise"),
            "style_instruction": "friendly and concise",
        }
        self.agent.set_persona("operator")
        operator = self.agent._fallback_analyze("Pepe solana", self.raw, intent)
        self.agent.set_persona("investor")
        investor = self.agent._fallback_analyze("Pepe solana", self.raw, intent)
        self.assertTrue(operator["executive_conclusion"].startswith("Ops Verdict"))
        self.assertTrue(investor["executive_conclusion"].startswith("Investment Verdict"))
        self.assertEqual(len(operator["action_plan"]), 7)
        self.assertNotEqual(
            [item["dimension"] for item in operator["dimensions"]],
            [item["dimension"] for item in investor["dimensions"]],
        )
        self.assertIn("community validation sprint", operator["recommendation"].lower())
        self.assertNotIn("changing exposure", operator["recommendation"].lower())
        operator_detail = " ".join(item["detail"] for item in operator["dimensions"])
        self.assertNotIn("slippage", operator_detail.lower())
        self.assertNotIn("position size", operator_detail.lower())

    def test_missing_social_data_is_not_reported_as_zero(self):
        raw = raw_fixture()
        raw["coingecko"]["community_data"] = {
            "twitter_followers": 0,
            "reddit_subscribers": None,
        }
        self.agent.set_persona("operator")
        summary = self.agent._build_data_summary(raw)
        report = self.agent._fallback_analyze(
            "Pepe solana", raw,
            {"writing_profile": infer_writing_profile("concise")},
        )
        self.assertIn("X followers: not connected", summary)
        self.assertIn("Reddit subscribers: not connected", summary)
        social = next(item for item in report["dimensions"] if item["key"] == "social_volume")
        self.assertEqual(social["score"], 5.0)
        self.assertIn("not connected", social["detail"].lower())
        self.assertNotIn("0 x followers", social["detail"].lower())
        self.assertEqual(report["data_gaps"][0]["status"], "not_connected")

    def test_bound_social_identity_uses_connected_no_data_instead_of_not_connected(self):
        raw = raw_fixture()
        raw["coingecko"]["community_data"] = {}
        raw["social"] = {
            "connected": False,
            "binding_connected": True,
            "metrics": [],
            "rag_documents": [],
            "providers": {
                "x": {"status": "connected_no_data", "identity_connected": True},
                "telegram": {"status": "group_not_bound", "identity_connected": True},
                "reddit": {"status": "not_configured", "identity_connected": False},
            },
        }
        self.agent.set_persona("operator")
        report = self.agent._fallback_analyze(
            "Pepe solana", raw,
            {"writing_profile": infer_writing_profile("concise")},
        )
        gaps = {item["source"]: item for item in report["data_gaps"]}
        self.assertEqual(gaps["X community metrics"]["status"], "connected_no_data")
        self.assertEqual(gaps["Telegram community metrics"]["status"], "action_required")
        self.assertEqual(gaps["Reddit community metrics"]["status"], "not_configured")
        rendered = " ".join(item["impact"] for item in gaps.values()).lower()
        self.assertIn("identity is connected", rendered)
        self.assertNotIn("x, reddit, and channel-level", rendered)

    def test_verified_social_metrics_change_operator_conclusion_and_actions(self):
        raw = raw_fixture()
        raw["coingecko"]["community_data"] = {}
        raw["social"] = {
            "connected": True,
            "binding_connected": True,
            "metrics": [
                {
                    "provider": "x", "mentions_24h": 640,
                    "active_authors_24h": 82, "engagements_24h": 1900,
                },
                {"provider": "telegram", "members": 12500},
            ],
            "rag_documents": [],
            "providers": {
                "x": {"status": "ready", "identity_connected": True},
                "telegram": {"status": "ready", "identity_connected": True},
                "reddit": {"status": "not_configured"},
            },
        }
        self.agent.set_persona("operator")
        report = self.agent._fallback_analyze(
            "Pepe solana", raw,
            {"writing_profile": infer_writing_profile("concise")},
        )
        trending = next(
            item for item in report["report_sections"]
            if item["title"] == "What Is Trending"
        )["content"]
        self.assertIn("640 X mentions", trending)
        self.assertIn("12,500 members", trending)
        self.assertIn("verified baseline", report["recommendation"].lower())
        self.assertTrue(all(
            "connected baseline" in item["dependency"].lower()
            for item in report["action_plan"]
        ))
        self.assertNotIn(
            "X community metrics",
            {item["source"] for item in report["data_gaps"]},
        )

    def test_operator_model_cannot_label_community_when_social_is_disconnected(self):
        raw = raw_fixture()
        raw["coingecko"]["community_data"] = {}
        self.agent.set_persona("operator")
        core = self.agent._fallback_analyze(
            "Pepe solana", raw,
            {"writing_profile": infer_writing_profile("friendly concise")},
        )
        styled = {
            "executive_conclusion": "This community is sleepy and weak.",
            "report_sections": [{"title": "Community", "content": "Nobody is active."}],
            "action_plan": [{"day": "Day 1", "actions": ["Map the conversation."]}],
            "dimensions": [
                {"key": item["key"], "detail": item["detail"]}
                for item in core["dimensions"]
            ],
        }
        enforced = self.agent._enforce_analysis_core(styled, core)
        self.assertEqual(enforced["executive_conclusion"], core["executive_conclusion"])
        self.assertEqual(enforced["report_sections"], core["report_sections"])
        self.assertEqual(enforced["action_plan"], styled["action_plan"])
        self.assertEqual(
            enforced["model_executive_conclusion"],
            "This community is sleepy and weak.",
        )

    def test_model_persona_content_is_preserved_separately_from_locked_core(self):
        self.agent.set_persona("operator")
        core = self.agent._fallback_analyze(
            "Pepe solana", self.raw,
            {"writing_profile": infer_writing_profile("concise")},
        )
        styled = {
            "recommendation": "Run a contributor sprint.",
            "executive_conclusion": "Ops Verdict: validate one participation loop.",
            "action_plan": [{"day": "Day 1", "actions": ["Map accounts"]}],
            "dimensions": [
                {"key": item["key"], "detail": "Operator-specific prose."}
                for item in core["dimensions"]
            ],
        }
        enforced = self.agent._enforce_analysis_core(styled, core)
        self.assertEqual(enforced["persona_recommendation"], "Run a contributor sprint.")
        self.assertEqual(enforced["executive_conclusion"], styled["executive_conclusion"])
        self.assertEqual(enforced["action_plan"], styled["action_plan"])

    def test_model_cannot_introduce_an_unverified_numeric_claim(self):
        core = self.agent._fallback_analyze(
            "Pepe solana", self.raw,
            {"writing_profile": infer_writing_profile("concise")},
        )
        styled = {
            "dimensions": [
                {
                    "key": item["key"],
                    "detail": (
                        "Trades up to $50K have guaranteed low slippage."
                        if item["key"] == "liquidity" else item["detail"]
                    ),
                }
                for item in core["dimensions"]
            ],
        }
        enforced = self.agent._enforce_analysis_core(styled, core)
        liquidity = next(
            item for item in enforced["dimensions"] if item["key"] == "liquidity"
        )
        self.assertEqual(
            liquidity["detail"],
            next(item for item in core["dimensions"] if item["key"] == "liquidity")["detail"],
        )

    def test_all_personas_generate_three_high_resolution_charts(self):
        intent = {
            "writing_profile": infer_writing_profile("friendly concise"),
            "style_instruction": "friendly concise",
        }
        for persona in ("operator", "investor", "builder", "researcher"):
            with self.subTest(persona=persona):
                self.agent.set_persona(persona)
                report = self.agent._fallback_analyze("Pepe solana", self.raw, intent)
                charts = generate_all_charts(report)
                for index in range(1, 4):
                    value = charts[f"chart_{index}"]
                    self.assertTrue(value.startswith("data:image/png;base64,"))
                    self.assertGreater(len(value), 10_000)

    def test_comparison_titles_for_two_three_and_four_assets(self):
        two = [report_fixture("Pepe", 7), report_fixture("Doge", 6)]
        three = two + [report_fixture("Bonk", 8)]
        four = three + [report_fixture("Wif", 5)]
        self.assertEqual(comparison_title(two), "Pepe vs Doge")
        self.assertEqual(comparison_title(three), "Pepe vs Doge vs Bonk")
        self.assertEqual(comparison_title(four), "Pepe vs Doge vs … (4 assets)")

    def test_comparison_uses_same_dimensions_and_highest_score_wins(self):
        reports = [
            report_fixture("Pepe", 6.2),
            report_fixture("Doge", 7.4),
            report_fixture("Bonk", 6.8),
        ]
        comparison = _deterministic_comparison(reports, "investor")
        self.assertEqual(comparison["winner"]["name"], "Doge")
        self.assertEqual([item["rank"] for item in comparison["ranking"]], [1, 2, 3])
        self.assertEqual(len(comparison["dimension_comparison"]), 2)
        self.assertTrue(all(len(item["assets"]) == 3 for item in comparison["dimension_comparison"]))

    def test_onchain_metadata_warning_and_hard_limit_are_configurable(self):
        with patch.dict(os.environ, {
            "ONCHAIN_METADATA_WARNING_BYTES": "9000",
            "ONCHAIN_METADATA_MAX_BYTES": "18000",
        }):
            limits = onchain_metadata_limits()
        self.assertEqual(limits["warning_bytes"], 9000)
        self.assertEqual(limits["maximum_bytes"], 18000)

    def test_onchain_metadata_below_warning_has_no_gas_warning(self):
        with patch.dict(os.environ, {
            "ONCHAIN_METADATA_WARNING_BYTES": "1024",
            "ONCHAIN_METADATA_MAX_BYTES": "2048",
        }):
            token_uri, storage = prepare_onchain_metadata(
                {"name": "Pepe", "image": "data:image/svg+xml;base64,abc"}
            )
        self.assertTrue(token_uri.startswith("data:application/json;base64,"))
        self.assertEqual(storage["mode"], "onchain-json")
        self.assertIsNone(storage["warning"])

    def test_onchain_metadata_at_warning_threshold_warns_about_gas(self):
        with patch.dict(os.environ, {
            "ONCHAIN_METADATA_WARNING_BYTES": "1024",
            "ONCHAIN_METADATA_MAX_BYTES": "4096",
        }):
            _, storage = prepare_onchain_metadata(
                {"name": "Pepe", "image": "x" * 1100}
            )
        self.assertIsNotNone(storage["warning"])
        self.assertIn("Gas", storage["warning"])

    def test_onchain_metadata_over_hard_limit_is_rejected(self):
        with patch.dict(os.environ, {
            "ONCHAIN_METADATA_WARNING_BYTES": "1024",
            "ONCHAIN_METADATA_MAX_BYTES": "1200",
        }):
            with self.assertRaises(OnchainMetadataTooLarge) as caught:
                prepare_onchain_metadata({"image": "x" * 1400})
        self.assertGreater(caught.exception.payload_bytes, 1200)


class AnalysisCacheSocialRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        agent_module._ANALYSIS_DATA_CACHE.clear()

    async def asyncTearDown(self):
        agent_module._ANALYSIS_DATA_CACHE.clear()

    async def test_market_cache_never_reuses_wallet_social_state(self):
        market = raw_fixture()
        market["coingecko"]["community_data"] = {}
        calls = 0

        async def current_social(raw_data, owner_address):
            nonlocal calls
            calls += 1
            result = copy.deepcopy(raw_data)
            if calls == 1:
                result["social"] = {
                    "connected": False,
                    "binding_connected": False,
                    "metrics": [],
                    "rag_documents": [],
                    "providers": {
                        "x": {"status": "not_connected"},
                        "telegram": {"status": "not_connected"},
                        "reddit": {"status": "not_configured"},
                    },
                }
            else:
                result["social"] = {
                    "connected": True,
                    "binding_connected": True,
                    "metrics": [
                        {"provider": "x", "mentions_24h": 250, "confidence": 0.8}
                    ],
                    "rag_documents": [],
                    "providers": {
                        "x": {"status": "ready", "identity_connected": True},
                        "telegram": {"status": "not_connected"},
                        "reddit": {"status": "not_configured"},
                    },
                }
                result.setdefault("_sources", []).append("Social intelligence")
            return result

        report_agent = MemeOpsAgent()
        report_agent.set_persona("operator")
        fetch = AsyncMock(return_value=market)
        with (
            patch.object(report_agent, "_fetch_raw_data", fetch),
            patch.object(
                agent_module, "enrich_raw_data_with_social",
                side_effect=current_social,
            ) as enrich,
            patch.object(agent_module, "DEEPSEEK_API_KEY", ""),
        ):
            first = await report_agent.analyze(
                "Pepe solana", owner_address="0xaaa",
            )
            second = await report_agent.analyze(
                "Pepe solana", owner_address="0xaaa",
            )

        self.assertFalse(first["data_cache"]["hit"])
        self.assertTrue(second["data_cache"]["hit"])
        self.assertEqual(fetch.await_count, 1)
        self.assertEqual(enrich.await_count, 2)
        self.assertEqual(
            set(second["performance_ms"]),
            {"market_data", "social_data", "report_generation", "validation", "total"},
        )
        self.assertIn(
            "X community metrics",
            {item["source"] for item in first["data_gaps"]},
        )
        self.assertNotIn(
            "X community metrics",
            {item["source"] for item in second["data_gaps"]},
        )


class ComparisonPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_assets_are_analyzed_sequentially_with_same_persona(self):
        class FakeAgent:
            def __init__(self):
                self.persona = None
                self.calls = []

            def set_persona(self, persona):
                self.persona = persona

            async def analyze(self, prompt, report_style):
                self.calls.append((prompt, report_style, self.persona))
                name, chain = prompt.split()
                return report_fixture(name, 6 + len(self.calls), chain)

        fake = FakeAgent()
        items = [
            {"token_name": "Pepe", "chain": "solana"},
            {"token_name": "Doge", "chain": "solana"},
            {"token_name": "Audiera", "chain": "bsc"},
        ]
        with patch("comparison.DEEPSEEK_API_KEY", ""):
            report = await build_comparison_report(
                fake,
                items,
                "researcher",
                "methodology-led horizontal comparison",
            )
        self.assertEqual(
            fake.calls,
            [
                ("Pepe solana", "methodology-led horizontal comparison", "researcher"),
                ("Doge solana", "methodology-led horizontal comparison", "researcher"),
                ("Audiera bsc", "methodology-led horizontal comparison", "researcher"),
            ],
        )
        self.assertEqual(report["persona"], "researcher")
        self.assertEqual(report["winner"]["name"], "Audiera")


if __name__ == "__main__":
    unittest.main()
