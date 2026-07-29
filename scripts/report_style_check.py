"""Live QA matrix for provider, writing-style, persona, and missing-data behavior."""

import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(ROOT / "backend"))

from agent import (  # noqa: E402
    DEEPSEEK_API_KEY, DEEPSEEK_MODEL, MemeOpsAgent,
)
from intent import infer_writing_profile  # noqa: E402


CASES = [
    (
        "operator_friendly",
        "operator",
        "Friendly and concise. Tell a new community lead what to do next in plain language.",
    ),
    (
        "operator_academic",
        "operator",
        "Academic community-operations report with cohort-retention methodology, "
        "participation-loop terminology, causal limitations, and a detailed plan.",
    ),
    (
        "investor_concise",
        "investor",
        "Concise risk/reward screening note with direct exposure limitations.",
    ),
    (
        "researcher_method",
        "researcher",
        "Methodology-led research memo with construct validity, confidence, data gaps, "
        "alternative explanations, and follow-up hypotheses.",
    ),
]


def compact_view(report: dict) -> dict:
    detail = " ".join(
        str(item.get("detail") or "") for item in report.get("dimensions") or []
    )
    return {
        "mode": report.get("generation_mode"),
        "model": report.get("generation_model"),
        "error": report.get("_llm_error"),
        "persona": report.get("persona"),
        "profile": report.get("writing_profile"),
        "decision_label": report.get("decision_label"),
        "conclusion": report.get("executive_conclusion"),
        "sections": [
            item.get("title") for item in report.get("report_sections") or []
        ],
        "action_count": len(report.get("action_plan") or []),
        "dimension_words": len(detail.split()),
        "first_detail": (report.get("dimensions") or [{}])[0].get("detail"),
        "data_gaps": report.get("data_gaps"),
    }


async def main() -> None:
    fetch_agent = MemeOpsAgent()
    raw_data = await fetch_agent._fetch_raw_data(
        "Dogecoin", "solana", "Dogecoin solana",
    )
    semaphore = asyncio.Semaphore(2)

    async def run_case(name, persona, style):
        agent = MemeOpsAgent()
        agent.set_persona(persona)
        intent = {
            **agent._extract_request_intent("Dogecoin solana"),
            "style_instruction": style,
            "writing_profile": infer_writing_profile(style),
            "rag_context": [],
        }
        core = agent._fallback_analyze("Dogecoin solana", raw_data, intent)
        try:
            if not DEEPSEEK_API_KEY:
                raise RuntimeError("DEEPSEEK_API_KEY is not configured")
            report = await agent._llm_analyze(
                "Dogecoin solana", raw_data, intent, core,
            )
            report = agent._enforce_analysis_core(report, core)
            report["generation_mode"] = "deepseek"
            report["generation_model"] = DEEPSEEK_MODEL
        except Exception as error:
            report = core
            report["generation_mode"] = "rules"
            report["generation_model"] = DEEPSEEK_MODEL if DEEPSEEK_API_KEY else None
            report["_llm_error"] = str(error)
        report = agent._enrich_report_content(report, raw_data, intent)
        return name, compact_view(report)

    async def limited(case):
        async with semaphore:
            return await run_case(*case)

    requested_case = os.getenv("REPORT_STYLE_CASE", "").strip()
    selected_cases = [
        case for case in CASES if not requested_case or case[0] == requested_case
    ]
    results = dict(await asyncio.gather(*(limited(case) for case in selected_cases)))
    print(json.dumps({
        "provider_configured": bool(DEEPSEEK_API_KEY),
        "cases": results,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
