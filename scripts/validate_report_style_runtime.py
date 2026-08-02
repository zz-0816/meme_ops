"""Manual live check for report latency and visible writing-style differentiation.

This script uses the configured market providers and DeepSeek key. It prints only
report metadata and user-facing excerpts; credentials are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from agent import MemeOpsAgent  # noqa: E402


async def run(asset: str, persona: str, selected_case: str) -> None:
    agent = MemeOpsAgent()
    agent.set_persona(persona)
    all_cases = (
        ("default", None),
        ("concise_plain", "简洁、清晰、容易理解"),
    )
    cases = tuple(
        item for item in all_cases
        if selected_case == "both" or item[0] == selected_case
    )
    results = []
    for label, style in cases:
        started = time.perf_counter()
        report = await agent.analyze(asset, style)
        section = (report.get("report_sections") or [{}])[0]
        results.append({
            "case": label,
            "elapsed_seconds": round(time.perf_counter() - started, 2),
            "generation_mode": report.get("generation_mode"),
            "style_label": (report.get("style_applied") or {}).get("label"),
            "conclusion": report.get("executive_conclusion"),
            "recommendation": report.get("recommendation"),
            "first_section": section.get("content"),
            "performance_ms": report.get("performance_ms"),
        })
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("asset", nargs="?", default="BitShiba on BSC")
    parser.add_argument("--persona", default="community_operator")
    parser.add_argument(
        "--case", choices=("both", "default", "concise_plain"), default="both",
    )
    args = parser.parse_args()
    asyncio.run(run(args.asset, args.persona, args.case))
