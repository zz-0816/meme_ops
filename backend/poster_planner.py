"""Turn report facts plus a user's art direction into a validated poster copy plan."""

from __future__ import annotations

import json
import os
import re

from config import load_project_env


load_project_env()


def _style_flags(style: str) -> dict:
    lowered = str(style or "").lower()
    return {
        "academic": any(word in lowered for word in (
            "academic", "research report", "professional terminology",
            "学术", "论文", "专业术语", "研究报告",
        )),
        "concise": any(word in lowered for word in (
            "concise", "minimal", "brief", "简洁", "极简", "精简",
        )),
        "sides": any(word in lowered for word in (
            "both sides", "two sides", "side columns", "两侧", "左右", "双栏",
        )),
    }


def fallback_poster_plan(report: dict, style: str) -> dict:
    flags = _style_flags(style)
    facts = report.get("poster_facts") or []
    fact_ids = [str(fact.get("id")) for fact in facts]
    narrative = report.get("poster_narrative") or {}
    if flags["academic"]:
        context = [
            "Market-microstructure interpretation covers depth, turnover efficiency, venue concentration, and execution risk.",
            "Snapshot limitations require chain-level holder validation and longitudinal liquidity monitoring.",
        ]
    elif flags["concise"]:
        context = ["Verified market facts, reduced to the strongest decision signals."]
    else:
        context = ["Source-grounded market signals with immutable report metrics."]
    return {
        "layout": "sides" if flags["sides"] else "academic" if flags["academic"] else "editorial",
        "copy_density": "academic" if flags["academic"] else "minimal" if flags["concise"] else "balanced",
        "headline": str(narrative.get("headline") or "Meme Market Signal")[:64],
        "subheadline": (
            "Market Microstructure & Risk Methodology"
            if flags["academic"]
            else str(narrative.get("subheadline") or "Verified trend intelligence")
        )[:120],
        "selected_fact_ids": fact_ids if flags["academic"] else fact_ids[:4],
        "context_lines": context,
        "visual_keywords": [str(item) for item in (report.get("report_keywords") or [])[:8]],
        "planner_provider": "deterministic",
        "planner_model": "validated-style-rules",
    }


def _validate_plan(candidate: dict, report: dict, style: str) -> dict:
    fallback = fallback_poster_plan(report, style)
    flags = _style_flags(style)
    available = {
        str(fact.get("id")) for fact in (report.get("poster_facts") or [])
    }
    selected = [
        str(item) for item in (candidate.get("selected_fact_ids") or [])
        if str(item) in available
    ]
    if not selected:
        selected = fallback["selected_fact_ids"]
    if flags["academic"]:
        selected = fallback["selected_fact_ids"]
    elif flags["concise"]:
        selected = selected[:4]

    context = []
    for line in candidate.get("context_lines") or []:
        clean = re.sub(r"\s+", " ", str(line)).strip()
        # Numeric facts are rendered only from server-owned poster_facts.
        if clean and not re.search(r"\d", clean):
            context.append(clean[:150])
    if not context:
        context = fallback["context_lines"]

    layout = str(candidate.get("layout") or fallback["layout"]).lower()
    if layout not in {"editorial", "sides", "academic"}:
        layout = fallback["layout"]
    if flags["sides"]:
        layout = "sides"
    if flags["academic"]:
        layout = "academic"

    density = str(candidate.get("copy_density") or fallback["copy_density"]).lower()
    if density not in {"minimal", "balanced", "academic"}:
        density = fallback["copy_density"]
    if flags["academic"]:
        density = "academic"
    elif flags["concise"]:
        density = "minimal"

    visual_keywords = []
    for item in [
        *(candidate.get("visual_keywords") or []),
        *(report.get("report_keywords") or []),
    ]:
        clean = re.sub(r"\s+", " ", str(item)).strip()
        if clean and clean.lower() not in {value.lower() for value in visual_keywords}:
            visual_keywords.append(clean[:60])

    return {
        "layout": layout,
        "copy_density": density,
        "headline": str(candidate.get("headline") or fallback["headline"])[:64],
        "subheadline": str(candidate.get("subheadline") or fallback["subheadline"])[:120],
        "selected_fact_ids": selected,
        "context_lines": context[:3],
        "visual_keywords": visual_keywords[:10],
        "planner_provider": candidate.get("planner_provider") or "deepseek",
        "planner_model": candidate.get("planner_model") or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
    }


async def build_poster_plan(report: dict, style: str) -> dict:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return fallback_poster_plan(report, style)
    from openai import AsyncOpenAI
    facts = [
        {
            "id": fact.get("id"),
            "label": fact.get("label"),
            "formatted": fact.get("formatted"),
        }
        for fact in (report.get("poster_facts") or [])
    ]
    prompt = {
        "user_art_and_copy_direction": style or "Cyberpunk, concise editorial copy",
        "asset": report.get("token") or {},
        "report_keywords": report.get("report_keywords") or [],
        "immutable_facts": facts,
        "report_narrative": report.get("poster_narrative") or {},
    }
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/"),
    )
    try:
        response = await client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a poster art director. Return JSON only with layout "
                        "(editorial|sides|academic), copy_density "
                        "(minimal|balanced|academic), headline, subheadline, "
                        "selected_fact_ids, context_lines, and visual_keywords. "
                        "Interpret the user's direction; never repeat it verbatim. "
                        "Do not write or alter numeric facts in prose. Select fact IDs "
                        "only; the renderer inserts their verified values. Academic "
                        "direction should add professional terminology and methodology "
                        "in context_lines without inventing numbers. A request for text "
                        "on both sides must use layout=sides."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.3,
            max_tokens=1000,
            response_format={"type": "json_object"},
        )
        candidate = json.loads(response.choices[0].message.content)
        candidate["planner_provider"] = "deepseek"
        candidate["planner_model"] = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        return _validate_plan(candidate, report, style)
    except Exception:
        return fallback_poster_plan(report, style)
