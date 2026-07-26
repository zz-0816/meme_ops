"""Sequential multi-asset comparison built on the analysis report agent."""

import json
from datetime import datetime

from agent import DEEPSEEK_API_KEY, DEEPSEEK_MODEL


PERSONA_NAMES = {
    "investor": "Investor",
    "operator": "Community Operator",
    "builder": "Project Builder",
    "researcher": "Researcher",
}


def comparison_title(reports: list[dict]) -> str:
    names = [str((report.get("token") or {}).get("name") or "Unknown") for report in reports]
    if len(names) <= 3:
        return " vs ".join(names)
    return " vs ".join(names[:2]) + f" vs … ({len(names)} assets)"


def _dimension_map(report: dict) -> dict:
    return {
        str(item.get("key") or item.get("dimension")): item
        for item in report.get("dimensions") or []
    }


def _asset_summary(report: dict) -> dict:
    dimensions = sorted(
        report.get("dimensions") or [],
        key=lambda item: float(item.get("score") or 0),
        reverse=True,
    )
    token = report.get("token") or {}
    return {
        "name": token.get("name") or "Unknown",
        "symbol": token.get("symbol"),
        "chain": token.get("chain") or "unknown",
        "contract_addr": token.get("contract_addr") or token.get("address"),
        "icon": token.get("icon"),
        "overall_score": float(report.get("overall_score") or 0),
        "risk_level": report.get("risk_level") or "unknown",
        "strengths": [
            f"{item.get('dimension')}: {float(item.get('score') or 0):.1f}/10"
            for item in dimensions[:2]
        ],
        "weaknesses": [
            f"{item.get('dimension')}: {float(item.get('score') or 0):.1f}/10"
            for item in list(reversed(dimensions[-2:]))
        ],
        "report_keywords": report.get("report_keywords") or [],
        "generation_mode": report.get("generation_mode"),
        "generation_model": report.get("generation_model"),
        "source_report": report,
    }


def _deterministic_comparison(reports: list[dict], persona: str) -> dict:
    assets = [_asset_summary(report) for report in reports]
    ranking = sorted(assets, key=lambda asset: asset["overall_score"], reverse=True)
    all_keys = []
    for report in reports:
        for key in _dimension_map(report):
            if key not in all_keys:
                all_keys.append(key)

    dimension_comparison = []
    for key in all_keys:
        entries = []
        for report, asset in zip(reports, assets):
            dimension = _dimension_map(report).get(key) or {}
            entries.append({
                "name": asset["name"],
                "chain": asset["chain"],
                "score": float(dimension.get("score") or 0),
                "detail": dimension.get("detail") or "",
            })
        leader = max(entries, key=lambda entry: entry["score"])
        dimension_comparison.append({
            "key": key,
            "dimension": (
                (_dimension_map(reports[0]).get(key) or {}).get("dimension")
                or key.replace("_", " ").title()
            ),
            "leader": leader["name"],
            "leader_score": leader["score"],
            "assets": entries,
            "insight": (
                f"{leader['name']} leads this dimension at {leader['score']:.1f}/10. "
                f"Compare the evidence and data limitations shown for every asset."
            ),
        })

    winner = ranking[0]
    return {
        "title": comparison_title(reports),
        "persona": persona,
        "persona_name": PERSONA_NAMES.get(persona, persona),
        "assets": assets,
        "dimension_comparison": dimension_comparison,
        "ranking": [
            {
                "rank": index + 1,
                "name": asset["name"],
                "chain": asset["chain"],
                "overall_score": asset["overall_score"],
                "risk_level": asset["risk_level"],
            }
            for index, asset in enumerate(ranking)
        ],
        "winner": {
            "name": winner["name"],
            "chain": winner["chain"],
            "score": winner["overall_score"],
            "reason": (
                f"{winner['name']} has the highest weighted score in this selected "
                f"{PERSONA_NAMES.get(persona, persona)} comparison."
            ),
        },
        "summary": (
            f"{winner['name']} ranks first at {winner['overall_score']:.1f}/10. "
            f"This horizontal comparison uses the same {PERSONA_NAMES.get(persona, persona)} "
            f"weights for every asset; missing source fields remain limitations rather than "
            f"being inferred."
        ),
        "generated_at": datetime.now().isoformat(),
        "generation_mode": "rules",
        "generation_model": None,
    }


async def build_comparison_report(
    report_agent,
    watchlist_items: list[dict],
    persona: str,
    report_style: str | None = None,
) -> dict:
    """Analyze selected assets sequentially, then synthesize one horizontal report."""
    report_agent.set_persona(persona)
    reports = []
    for item in watchlist_items:
        prompt = f"{item['token_name']} {item.get('chain') or ''}".strip()
        report = await report_agent.analyze(prompt, report_style)
        reports.append(report)

    comparison = _deterministic_comparison(reports, persona)
    if not DEEPSEEK_API_KEY:
        return comparison

    compact_assets = [
        {
            "name": asset["name"],
            "chain": asset["chain"],
            "overall_score": asset["overall_score"],
            "risk_level": asset["risk_level"],
            "strengths": asset["strengths"],
            "weaknesses": asset["weaknesses"],
            "dimensions": [
                {
                    "dimension": item.get("dimension"),
                    "score": item.get("score"),
                    "detail": item.get("detail"),
                }
                for item in (asset["source_report"].get("dimensions") or [])
            ],
        }
        for asset in comparison["assets"]
    ]
    prompt = f"""Create an evidence-grounded horizontal comparison from the same
{PERSONA_NAMES.get(persona, persona)} perspective.
Requested report style: {report_style or 'Detailed, clear, and comparative'}

Asset reports:
{json.dumps(compact_assets, ensure_ascii=False)}

Return JSON only:
{{
  "summary": "A comparative conclusion explaining material differences",
  "winner_reason": "Why the highest-scoring asset leads without changing scores",
  "asset_commentary": [
    {{"name":"exact asset name","strengths":["2-4 comparative strengths"],"weaknesses":["2-4 comparative weaknesses"]}}
  ],
  "dimension_insights": [
    {{"dimension":"exact dimension name","insight":"cross-asset interpretation"}}
  ]
}}
Do not invent or change scores, token identities, chains, or market figures."""
    try:
        response = await report_agent._get_llm().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the comparison stage of a Web3 analysis report agent. "
                        "Compare only supplied reports and return valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        model_result = json.loads(response.choices[0].message.content)
        asset_commentary = {
            str(item.get("name", "")).lower(): item
            for item in model_result.get("asset_commentary") or []
        }
        for asset in comparison["assets"]:
            commentary = asset_commentary.get(str(asset["name"]).lower()) or {}
            if commentary.get("strengths"):
                asset["strengths"] = [str(value) for value in commentary["strengths"][:4]]
            if commentary.get("weaknesses"):
                asset["weaknesses"] = [str(value) for value in commentary["weaknesses"][:4]]
        insights = {
            str(item.get("dimension", "")).lower(): str(item.get("insight") or "")
            for item in model_result.get("dimension_insights") or []
        }
        for dimension in comparison["dimension_comparison"]:
            dimension["insight"] = (
                insights.get(str(dimension["dimension"]).lower())
                or dimension["insight"]
            )
        comparison["summary"] = str(model_result.get("summary") or comparison["summary"])
        comparison["winner"]["reason"] = str(
            model_result.get("winner_reason") or comparison["winner"]["reason"]
        )
        comparison["generation_mode"] = "deepseek"
        comparison["generation_model"] = DEEPSEEK_MODEL
    except Exception as error:
        comparison["_llm_error"] = str(error)
        comparison["generation_model"] = DEEPSEEK_MODEL
    return comparison
