"""Manual live check for visibly different report-writing directions."""

import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
for site_packages in (
    ROOT / "backend" / "venv" / "Lib" / "site-packages",
    ROOT / "backend" / "venv" / "lib" / "site-packages",
):
    if site_packages.is_dir():
        sys.path.insert(0, str(site_packages))

from agent import MemeOpsAgent  # noqa: E402
from agent import DEEPSEEK_API_KEY, DEEPSEEK_MODEL  # noqa: E402
from intent import infer_writing_profile  # noqa: E402


def view(report: dict) -> dict:
    return {
        "mode": report.get("generation_mode"),
        "error": report.get("_llm_error"),
        "profile": report.get("writing_profile"),
        "style_evidence": report.get("style_evidence"),
        "overall_score": report.get("overall_score"),
        "risk_level": report.get("risk_level"),
        "analysis_core": report.get("analysis_core"),
        "first_detail": (report.get("dimensions") or [{}])[0].get("detail"),
        "recommendation": report.get("recommendation"),
    }


async def main():
    agent = MemeOpsAgent()
    agent.set_persona("investor")
    raw_data = await agent._fetch_raw_data("Pepe", "solana", "Pepe solana")
    cases = {
        "user_full": "具体完整，包含证据、解释、方法限制和投资含义",
        "user_concise": "简洁简单，只保留关键数据和直接结论",
        "self_academic": (
            "Academic market-microstructure memo with methodology, liquidity "
            "limitations, and professional terminology"
        ),
        "self_friendly": (
            "Friendly beginner explanation using short sentences and one clear action"
        ),
    }
    results = {}
    for name, style in cases.items():
        intent = {
            **agent._extract_request_intent("Pepe solana"),
            "style_instruction": style,
            "writing_profile": infer_writing_profile(style),
        }
        analysis_core = agent._fallback_analyze(
            "Pepe solana", raw_data, intent,
        )
        try:
            if not DEEPSEEK_API_KEY:
                raise RuntimeError("DEEPSEEK_API_KEY is not configured")
            report = await agent._llm_analyze(
                "Pepe solana", raw_data, intent, analysis_core,
            )
            report = agent._enforce_analysis_core(report, analysis_core)
            report["generation_mode"] = "deepseek"
            report["generation_model"] = DEEPSEEK_MODEL
        except Exception as error:
            report = analysis_core
            report["generation_mode"] = "rules"
            report["generation_model"] = DEEPSEEK_MODEL
            report["_llm_error"] = str(error)
        report = agent._enrich_report_content(report, raw_data, intent)
        results[name] = view(report)
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
