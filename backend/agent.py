"""
Agent 分析引擎 — 接入 DexScreener + CoinGecko + DeepSeek LLM
"""

import json
import os
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

import httpx
from openai import AsyncOpenAI
from config import load_project_env
from intent import extract_analysis_intent, infer_writing_profile
from asset_resolver import is_contract_address, requested_asset_terms, select_exact_pairs

load_project_env()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMORY_PROMPT_PATH = PROJECT_ROOT / "MEMORY_PROMPT.md"
PERSONAS_DIR = PROJECT_ROOT / "personas"

# 维度定义（共享层）
DIMENSIONS = [
    {"name": "On-chain Liquidity", "key": "liquidity", "weight": 5.0},
    {"name": "Holder Activity", "key": "holder_count", "weight": 4.0},
    {"name": "Holder Distribution", "key": "holder_distribution", "weight": 4.0},
    {"name": "Community Discussion", "key": "social_volume", "weight": 3.0},
    {"name": "Social Momentum", "key": "social_trending", "weight": 3.0},
]

PERSONA_WEIGHT_ADJUST = {
    "operator": {"liquidity": -1, "social_volume": +1, "social_trending": +1},
    "builder": {"holder_distribution": +1},
    "investor": {},
    "researcher": {},
}

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or ""
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


def analysis_provider_status() -> dict:
    return {
        "provider": "deepseek" if DEEPSEEK_API_KEY else "rules",
        "configured": bool(DEEPSEEK_API_KEY),
        "model": DEEPSEEK_MODEL if DEEPSEEK_API_KEY else None,
    }


class MemeOpsAgent:
    """Web3 Meme 投研 Agent — 真实数据 + LLM 分析"""

    def __init__(self):
        self.memory_prompt = ""
        self.persona_prompt = ""
        self.current_persona = "investor"
        self.llm_client = None
        self.reload_memory()

    def reload_memory(self) -> str:
        if MEMORY_PROMPT_PATH.exists():
            self.memory_prompt = MEMORY_PROMPT_PATH.read_text(encoding="utf-8")
        return self.memory_prompt

    def set_persona(self, persona: str):
        if persona not in ("investor", "operator", "builder", "researcher"):
            persona = "investor"
        self.current_persona = persona
        persona_path = PERSONAS_DIR / f"{persona}.md"
        self.persona_prompt = persona_path.read_text(encoding="utf-8") if persona_path.exists() else ""

    def _get_llm(self) -> AsyncOpenAI:
        if self.llm_client is None:
            if not DEEPSEEK_API_KEY:
                raise RuntimeError("DEEPSEEK_API_KEY is not configured")
            self.llm_client = AsyncOpenAI(
                api_key=DEEPSEEK_API_KEY,
                base_url=DEEPSEEK_BASE_URL,
            )
        return self.llm_client

    # ============ 分析主流程 ============

    async def analyze(self, prompt: str, report_style: str | None = None) -> dict:
        if not self.memory_prompt:
            self.reload_memory()
        # 每次分析强制重读 persona 文件
        self.set_persona(self.current_persona)

        request_intent = self._extract_request_intent(prompt)
        if report_style and report_style.strip():
            request_intent["style_instruction"] = report_style.strip()
            request_intent["writing_profile"] = infer_writing_profile(report_style)

        # 1. 拉取真实数据
        try:
            raw_data = await self._fetch_raw_data(
                request_intent["token_query"],
                request_intent.get("chain"),
                prompt,
            )
        except Exception as e:
            raw_data = {"query": prompt, "dexscreener": {}, "coingecko": {}, "_sources": [], "_error": str(e)}

        # 2. 用 LLM 打分 + 生成报告
        analysis_core = self._fallback_analyze(prompt, raw_data, request_intent)
        try:
            if DEEPSEEK_API_KEY:
                report = await self._llm_analyze(
                    prompt, raw_data, request_intent, analysis_core,
                )
                report = self._enforce_analysis_core(report, analysis_core)
                report["generation_mode"] = "deepseek"
                report["generation_model"] = DEEPSEEK_MODEL
            else:
                report = analysis_core
                report["generation_mode"] = "rules"
                report["generation_model"] = None
        except Exception as e:
            report = analysis_core
            report["_llm_error"] = str(e)
            report["generation_mode"] = "rules"
            report["generation_model"] = DEEPSEEK_MODEL if DEEPSEEK_API_KEY else None

        report = self._enrich_report_content(report, raw_data, request_intent)

        report["data_sources"] = raw_data["_sources"]
        report["analyzed_at"] = datetime.now().isoformat()
        report["request_intent"] = request_intent
        report["asset_match"] = raw_data.get("asset_match", "unknown")

        # 强制设置 chain：优先用链提示，其次用 DexScreener 返回的 chainId
        token = report.get("token", {})
        chain_hint = raw_data.get("chain_hint")
        if chain_hint:
            token["chain"] = chain_hint
        elif not token.get("chain") or token["chain"] == "unknown":
            pairs = raw_data.get("dexscreener", {}).get("pairs", [])
            if pairs:
                token["chain"] = pairs[0].get("chainId", "unknown")
        report["token"] = token

        return report

    # ============ 数据拉取 ============

    def _extract_request_intent(self, prompt: str) -> dict:
        """Extract asset, chain, and presentation intent from short or conversational input."""
        return extract_analysis_intent(prompt)

    async def _fetch_raw_data(
        self, query: str, chain_override: str = None, original_prompt: str = None,
    ) -> dict:
        """
        并行拉取 DexScreener + CoinGecko 数据。
        自动识别合约地址 vs 代币名称 vs 链名提示。
        例如 "pepe solana" → 搜索 PEPE 并优先 SOL 链结果。
        """
        import re
        sources = []
        dexscreener_data = {}
        coingecko_data = {}
        chain_hint = chain_override
        search_query = query.strip()

        # 检测链名提示
        chain_aliases = {
            "solana": "solana", "sol": "solana",
            "ethereum": "ethereum", "eth": "ethereum",
            "bsc": "bsc", "binance": "bsc",
            "ton": "ton",
            "monad": "monad",
        }
        if not chain_hint:
            for alias, chain_id in chain_aliases.items():
                pattern = rf'\b{alias}\b$'
                m = re.search(pattern, search_query, re.IGNORECASE)
                if m:
                    chain_hint = chain_id
                    search_query = search_query[:m.start()].strip()
                    break

        # 检测是否为合约地址 (0x开头 + 40位hex)
        is_address = is_contract_address(search_query)

        async with httpx.AsyncClient(timeout=15) as client:
            # DexScreener — 地址直接用 token 端点，名称用 search 端点
            try:
                if is_address:
                    resp = await client.get(
                        f"https://api.dexscreener.com/latest/dex/tokens/{search_query}"
                    )
                else:
                    resp = await client.get(
                        "https://api.dexscreener.com/latest/dex/search",
                        params={"q": search_query},
                    )
                if resp.status_code == 200:
                    data = resp.json()
                    pairs = data.get("pairs", [])
                    # Never substitute a similarly named or more liquid asset.
                    # Identity and requested chain must match before liquidity
                    # is used as a tie breaker.
                    if not is_address:
                        pairs = select_exact_pairs(pairs, search_query, chain_hint)
                    elif chain_hint:
                        pairs = [
                            pair for pair in pairs
                            if str(pair.get("chainId") or "").lower() == chain_hint
                        ]
                        pairs.sort(
                            key=lambda pair: float(
                                ((pair.get("liquidity") or {}).get("usd") or 0)
                            ),
                            reverse=True,
                        )
                    data["pairs"] = pairs
                    dexscreener_data = data
                    if pairs:
                        sources.append("DexScreener")
            except Exception:
                pass

            # CoinGecko 搜索 — 地址模式跳过模糊搜索，用 DexScreener 结果中的代币名
            cg_query = search_query
            if is_address and dexscreener_data.get("pairs"):
                # 从 DexScreener 结果提取代币名用于 CoinGecko 搜索
                base = dexscreener_data["pairs"][0].get("baseToken", {})
                cg_query = base.get("name") or base.get("symbol") or query

            try:
                resp = await client.get(
                    "https://api.coingecko.com/api/v3/search",
                    params={"query": cg_query},
                )
                if resp.status_code == 200:
                    search_data = resp.json()
                    coins = search_data.get("coins", [])
                    if coins:
                        wanted = requested_asset_terms(cg_query)
                        exact = next((
                            coin for coin in coins
                            if str(coin.get("name") or "").lower() in wanted
                            or str(coin.get("symbol") or "").lower() in wanted
                            or str(coin.get("id") or "").lower() in wanted
                        ), None)
                        coin_id = (exact or coins[0])["id"]
                        coin_resp = await client.get(
                            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                            params={
                                "localization": "false",
                                "tickers": "false",
                                "community_data": "true",
                                "developer_data": "false",
                            },
                        )
                        if coin_resp.status_code == 200:
                            coingecko_data = coin_resp.json()
                            sources.append("CoinGecko")
            except Exception:
                pass

        return {
            "query": original_prompt or query,
            "search_query": search_query,
            "chain_hint": chain_hint,
            "dexscreener": dexscreener_data,
            "coingecko": coingecko_data,
            "_sources": sources,
            "asset_match": "exact" if dexscreener_data.get("pairs") else "reference-only",
        }

    # ============ LLM 分析 ============

    async def _llm_analyze(
        self, prompt: str, raw_data: dict, intent: dict | None = None,
        analysis_core: dict | None = None,
    ) -> dict:
        """
        将原始数据 + 完整 persona prompt 发送给 DeepSeek。
        核心：不同 persona 输出截然不同的内容和建议。
        """
        data_summary = self._build_data_summary(raw_data)
        intent = intent or self._extract_request_intent(prompt)

        adjustments = PERSONA_WEIGHT_ADJUST.get(self.current_persona, {})
        dims_with_adjust = []
        for dim in DIMENSIONS:
            adj = adjustments.get(dim["key"], 0)
            dims_with_adjust.append({
                "name": dim["name"],
                "key": dim["key"],
                "weight": dim["weight"] + adj,
            })

        # 加载完整 persona prompt（不截断）
        persona_instructions = self.persona_prompt if self.persona_prompt else "Default investor perspective"

        system_prompt = f"""You are a Web3 Meme intelligence analyst. Follow the Persona definition below,
but write every user-facing string, dimension, conclusion, label, and recommendation in English.

╔══════════════════════════════════════════════════════════╗
║  PERSONA DEFINITION (follow this perspective)             ║
╚══════════════════════════════════════════════════════════╝
{persona_instructions}

══════════════════════════════════════════════════
Scoring dimensions (return every dimension in JSON, scored 0-10):
{json.dumps(dims_with_adjust, ensure_ascii=False, indent=2)}

Risk levels: green (7-10) / yellow (4-6.9) / red (0-3.9)

══════════════════════════════════════════════════
Return JSON only, without a Markdown code fence:
{{
  "token": {{"name":"Token name","symbol":"Symbol","chain":"Chain","address":"Contract address"}},
  "dimensions": [
    {{"dimension":"English dimension name","key":"key","score":8.5,"weight":5.0,
      "detail":"An evidence-based English explanation with concrete figures and a clear conclusion"}}
  ],
  "overall_score": 7.5,
  "risk_level": "green",
  "health_indicators": {{"positive":["Strength"],"negative":["Risk"],"neutral":["Watch item"]}},
  "recommendation": "A concise English recommendation reflecting the selected Persona",
  "analysis_summary": "A summary visibly following the requested writing style",
  "report_keywords": ["5-10 evidence-based analytical keywords"],
  "writing_profile": {{"tone":"friendly|analytical|academic","depth":"concise|standard|detailed|academic","length":"compact|standard|extended"}},
  "poster_narrative": {{"headline":"Short editorial headline","subheadline":"Short evidence-led subtitle"}}
}}

The user's requested writing style is binding and must be visibly different in every
dimension detail, recommendation, and summary:
- concise/friendly: plain language, 1-2 short sentences per dimension, direct conclusion.
- standard/analytical: 2-3 evidence-led sentences per dimension.
- academic/professional: 3-5 sentences per dimension using appropriate market-
  microstructure terminology, methodology, limitations, and technical implications.
Never change a source figure merely to create stylistic variety.

Perspective requirements:
- Investor: investment relevance, risk/reward, and exposure guidance
- Community Operator: growth strategy, content direction, and campaign ideas
- Project Builder: diagnosis, competitive gaps, and improvement priorities
- Researcher: sector context, data quality, and follow-up research"""

        style_instruction = intent.get("style_instruction") or "Concise, evidence-led, and approachable."
        fixed_core = analysis_core or self._fallback_analyze(prompt, raw_data, intent)
        fixed_core_payload = {
            "overall_score": fixed_core.get("overall_score"),
            "risk_level": fixed_core.get("risk_level"),
            "dimensions": [
                {
                    "key": item.get("key"),
                    "score": item.get("score"),
                    "weight": item.get("weight"),
                }
                for item in fixed_core.get("dimensions", [])
            ],
            "recommendation_stance": fixed_core.get("recommendation"),
        }
        user_prompt = f"""Analyze this asset: "{intent.get('token_query')}"
Requested chain: {intent.get('chain') or 'not specified'}
Original user request: "{prompt}"
Requested writing style: "{style_instruction}"

Fixed analysis core (binding; style must not change scores, risk, or recommendation stance):
{json.dumps(fixed_core_payload, ensure_ascii=False)}

Data:
{data_summary}

Return the requested JSON in English. Adapt tone, clarity, and level of detail to the
requested writing style, but never alter, omit, or invent factual market metrics."""

        llm = self._get_llm()
        try:
            profile = intent.get("writing_profile") or {}
            max_tokens = 8192 if profile.get("length") == "extended" else 4096
            last_error = None
            for attempt in range(2):
                retry_note = (
                    "\nThe previous response was invalid JSON. Return a complete, shorter JSON "
                    "object with all required fields and no unescaped line breaks in strings."
                    if attempt else ""
                )
                resp = await llm.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt + retry_note},
                    ],
                    temperature=0.3 if attempt == 0 else 0.1,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                content = resp.choices[0].message.content.strip()
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                elif "```" in content:
                    content = content.split("```")[1].split("```")[0].strip()
                try:
                    report = json.loads(content)
                    report["persona"] = self.current_persona
                    return report
                except json.JSONDecodeError as error:
                    last_error = error
            raise last_error or RuntimeError("Model returned invalid JSON")
            """
            # 提取 JSON（LLM 可能在 JSON 外包裹 markdown 代码块）
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            report = json.loads(content)
            report["persona"] = self.current_persona
            return report
            """
        except Exception as e:
            # Let the outer analysis flow record the failure and explicit
            # rules-engine fallback state for the UI.
            raise RuntimeError(f"{DEEPSEEK_MODEL} request failed: {e}") from e

    def _enforce_analysis_core(
        self, styled_report: dict, analysis_core: dict,
    ) -> dict:
        """Keep quantitative conclusions invariant while preserving styled prose."""
        styled_dimensions = {
            str(item.get("key") or item.get("dimension") or ""): item
            for item in styled_report.get("dimensions") or []
        }
        dimensions = []
        for core_dimension in analysis_core.get("dimensions") or []:
            key = str(
                core_dimension.get("key")
                or core_dimension.get("dimension")
                or ""
            )
            styled = styled_dimensions.get(key) or {}
            dimensions.append({
                **core_dimension,
                "detail": (
                    styled.get("detail")
                    or styled.get("notes")
                    or core_dimension.get("detail")
                    or ""
                ),
            })
        styled_report["dimensions"] = dimensions
        styled_report["overall_score"] = analysis_core.get("overall_score")
        styled_report["risk_level"] = analysis_core.get("risk_level")
        styled_report["model_recommendation"] = styled_report.get("recommendation")
        styled_report["recommendation"] = analysis_core.get("recommendation")
        styled_report["health_indicators"] = analysis_core.get("health_indicators")
        styled_report["analysis_core"] = {
            "locked": True,
            "overall_score": analysis_core.get("overall_score"),
            "risk_level": analysis_core.get("risk_level"),
            "dimension_scores": {
                str(item.get("key")): item.get("score")
                for item in analysis_core.get("dimensions") or []
            },
            "recommendation_stance": analysis_core.get("recommendation"),
        }
        return styled_report

    def _enrich_report_content(self, report: dict, raw_data: dict, intent: dict) -> dict:
        """Persist style interpretation, immutable facts, and reusable NFT keywords."""
        pairs = (raw_data.get("dexscreener") or {}).get("pairs") or []
        cg = raw_data.get("coingecko") or {}

        def number(value):
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, dict):
                return sum(number(item) for item in value.values())
            return 0.0

        liquidity = sum(number((pair.get("liquidity") or {}).get("usd")) for pair in pairs)
        volume = sum(number((pair.get("volume") or {}).get("h24")) for pair in pairs)
        transactions = sum(
            number((pair.get("txns") or {}).get("h24")) for pair in pairs
        )
        market_cap = number(
            ((cg.get("market_data") or {}).get("market_cap") or {}).get("usd")
        )
        score = float(report.get("overall_score") or 0)
        facts = [
            {"id": "score", "label": "Overall score", "value": score, "formatted": f"{score:.1f}/10"},
            {"id": "liquidity", "label": "DEX liquidity", "value": liquidity, "formatted": f"${liquidity:,.0f}"},
            {"id": "volume_24h", "label": "24h volume", "value": volume, "formatted": f"${volume:,.0f}"},
            {"id": "pair_count", "label": "Active pairs", "value": len(pairs), "formatted": str(len(pairs))},
            {"id": "transactions_24h", "label": "24h transactions", "value": transactions, "formatted": f"{transactions:,.0f}"},
        ]
        if market_cap:
            facts.append(
                {"id": "market_cap", "label": "Market cap", "value": market_cap, "formatted": f"${market_cap:,.0f}"}
            )

        evidence_by_key = {
            "liquidity": (
                f"Verified aggregate: ${liquidity:,.0f} DEX liquidity, "
                f"${volume:,.0f} 24h volume, {len(pairs)} exact matched pairs."
            ),
            "holder_count": (
                f"Verified activity proxy: {transactions:,.0f} buys + sells in 24h; "
                "this is not a unique-holder count."
            ),
            "holder_distribution": (
                f"Verified venue proxy: {len(pairs)} active pairs; wallet-level "
                "holder concentration requires explorer data."
            ),
            "social_trending": (
                f"Verified market inputs: ${volume:,.0f} 24h DEX volume"
                + (f" and ${market_cap:,.0f} market cap." if market_cap else ".")
            ),
        }
        for dimension in report.get("dimensions") or []:
            evidence = evidence_by_key.get(str(dimension.get("key") or ""))
            if evidence:
                dimension["verified_evidence"] = evidence

        derived = [
            str((report.get("token") or {}).get("symbol") or (report.get("token") or {}).get("name") or ""),
            str((report.get("token") or {}).get("chain") or ""),
            "deep liquidity" if liquidity >= 1_000_000 else "thin liquidity",
            "high turnover" if volume >= 1_000_000 else "moderate turnover" if volume >= 100_000 else "low turnover",
            f"{report.get('risk_level', 'unknown')} risk",
            f"{self.current_persona} perspective",
        ]
        keywords = []
        for keyword in [*(report.get("report_keywords") or []), *derived]:
            clean = str(keyword or "").strip()
            if clean and clean.lower() not in {item.lower() for item in keywords}:
                keywords.append(clean)

        requested_profile = intent.get("writing_profile") or {}
        model_profile = report.get("writing_profile") or {}
        report["writing_profile"] = {
            "tone": requested_profile.get("tone") or model_profile.get("tone") or "analytical",
            "depth": requested_profile.get("depth") or model_profile.get("depth") or "standard",
            "length": requested_profile.get("length") or model_profile.get("length") or "standard",
            "source_instruction": intent.get("style_instruction") or "Default analytical style",
        }
        report["report_keywords"] = keywords[:12]
        report["poster_facts"] = facts
        narrative = report.get("poster_narrative") or {}
        token_name = str((report.get("token") or {}).get("name") or "Meme")
        report["poster_narrative"] = {
            "headline": str(narrative.get("headline") or f"{token_name} Signal Brief")[:80],
            "subheadline": str(narrative.get("subheadline") or report.get("analysis_summary") or "")[:180],
        }
        dimension_text = " ".join(
            str(item.get("detail") or "") for item in report.get("dimensions", [])
        )
        recommendation_text = str(report.get("recommendation") or "")
        report["style_evidence"] = {
            "dimension_word_count": len(dimension_text.split()),
            "recommendation_word_count": len(recommendation_text.split()),
            "dimension_sentence_count": sum(
                str(item.get("detail") or "").count(".")
                for item in report.get("dimensions", [])
            ),
        }
        return report

    def _build_data_summary(self, raw_data: dict) -> str:
        """将原始 API 数据压缩为文本摘要"""
        parts = []

        def metric_number(value):
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, dict):
                return sum(metric_number(item) for item in value.values())
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        # DexScreener
        ds = raw_data.get("dexscreener", {})
        pairs = ds.get("pairs", [])
        if pairs:
            aggregate_liquidity = sum(
                metric_number((pair.get("liquidity") or {}).get("usd"))
                for pair in pairs
            )
            aggregate_volume = sum(
                metric_number((pair.get("volume") or {}).get("h24"))
                for pair in pairs
            )
            aggregate_transactions = sum(
                metric_number((pair.get("txns") or {}).get("h24"))
                for pair in pairs
            )
            parts.append(
                "### Authoritative aggregate metrics\n"
                f"- Exact matched pairs: {len(pairs)}\n"
                f"- Aggregate DEX liquidity: ${aggregate_liquidity:,.0f}\n"
                f"- Aggregate 24h volume: ${aggregate_volume:,.0f}\n"
                f"- Aggregate 24h buys + sells: {aggregate_transactions:,.0f}\n"
                "- Use these aggregate figures in report conclusions; do not substitute a single pool."
            )
            # 按流动性排序取 top 5
            sorted_pairs = sorted(pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0) or 0, reverse=True)[:5]
            parts.append(f"### DexScreener data ({len(pairs)} pairs; Top 5 shown)")
            for p in sorted_pairs:
                base = p.get("baseToken", {})
                quote = p.get("quoteToken", {})
                liquidity = p.get("liquidity", {})
                volume = p.get("volume", {})
                txns = p.get("txns", {})
                price_change = p.get("priceChange", {})
                change_24h = metric_number(price_change.get("h24"))
                liquidity_usd = metric_number(liquidity.get("usd"))
                volume_24h = metric_number(volume.get("h24"))
                transactions_24h = metric_number(txns.get("h24"))
                fdv = metric_number(p.get("fdv"))
                parts.append(
                    f"- {base.get('symbol','?')}/{quote.get('symbol','?')} "
                    f"on {p.get('chainId','?')} @ {p.get('dexId','?')}\n"
                    f"  Price: ${p.get('priceUsd','?')} "
                    f"({change_24h:+.1f}% 24h)\n"
                    f"  Liquidity: ${liquidity_usd:,.0f} "
                    f"24h volume: ${volume_24h:,.0f}\n"
                    f"  24h transactions: {transactions_24h:,.0f} "
                    f"FDV: ${fdv:,.0f}"
                )
        else:
            parts.append("### DexScreener: no data found")

        # CoinGecko
        cg = raw_data.get("coingecko", {})
        if cg:
            market = cg.get("market_data", {})
            community = cg.get("community_data", {})
            parts.append(f"\n### CoinGecko data")
            parts.append(f"- Name: {cg.get('name','?')} ({cg.get('symbol','?').upper()})")
            parts.append(f"- Rank: #{market.get('market_cap_rank','?')}")
            parts.append(f"- Market cap: ${metric_number(market.get('market_cap',{}).get('usd')):,.0f}")
            parts.append(f"- 24h volume: ${metric_number(market.get('total_volume',{}).get('usd')):,.0f}")
            parts.append(f"- Circulating supply: {metric_number(market.get('circulating_supply')):,.0f}")
            parts.append(f"- Total supply: {market.get('total_supply','?')}")
            parts.append(f"- All-time high: ${metric_number(market.get('ath',{}).get('usd')):.8f}")
            parts.append(f"- X followers: {metric_number(community.get('twitter_followers')):,.0f}")
            parts.append(f"- Reddit subscribers: {metric_number(community.get('reddit_subscribers')):,.0f}")
            parts.append(f"- Telegram: {community.get('telegram_channel_user_count','?')}")
            desc = cg.get("description", {}).get("en", "")
            if desc:
                parts.append(f"- Description: {desc[:300]}...")
            parts.append(f"- Genesis date: {cg.get('genesis_date','?')}")
        else:
            parts.append("### CoinGecko: no data found")

        return "\n".join(parts) if parts else "No data available"

    # ============ 兜底分析（无 LLM API Key 时） ============

    def _fallback_analyze(
        self, prompt: str, raw_data: dict, intent: dict | None = None,
    ) -> dict:
        """基于原始数据的详细评分逻辑"""
        ds = raw_data.get("dexscreener", {})
        pairs = ds.get("pairs", [])
        cg = raw_data.get("coingecko", {})

        search_name = raw_data.get("search_query") or prompt.strip()
        token_info = {"raw_prompt": prompt, "name": search_name, "symbol": None, "contract_addr": None, "chain": raw_data.get("chain_hint") or "unknown", "icon": ""}
        if pairs:
            p0 = pairs[0]
            base = p0.get("baseToken", {})
            token_info["name"] = base.get("name") or prompt.strip()
            token_info["symbol"] = base.get("symbol")
            token_info["chain"] = p0.get("chainId", "unknown")
            token_info["contract_addr"] = base.get("address")
            token_info["price_usd"] = p0.get("priceUsd")
            # DexScreener 可能返回图标 URL
            token_info["icon"] = p0.get("info", {}).get("imageUrl", "") or base.get("imageUrl", "") or ""
        if cg:
            token_info["name"] = cg.get("name") or token_info["name"]
            token_info["symbol"] = cg.get("symbol") or token_info["symbol"]
            token_info["icon"] = cg.get("image", {}).get("small", "") or token_info["icon"]

        def _safe_num(val):
            if val is None: return 0
            if isinstance(val, (int, float)): return val
            if isinstance(val, dict): return sum(_safe_num(v) for v in val.values())
            return 0

        total_liquidity = sum(_safe_num(p.get("liquidity", {}).get("usd")) for p in pairs)
        total_volume_24h = sum(_safe_num(p.get("volume", {}).get("h24")) for p in pairs)
        total_txns_24h = sum(_safe_num(p.get("txns", {}).get("h24")) for p in pairs)
        pair_count = len(pairs)
        twitter_followers = (cg.get("community_data", {}).get("twitter_followers", 0) or 0) if cg else 0
        market_cap = (cg.get("market_data", {}).get("market_cap", {}).get("usd", 0) or 0) if cg else 0

        # 详细打分
        liq_score = min(10, round((total_liquidity / 100_000) ** 0.4 * 4, 1)) if total_liquidity > 0 else 0
        holder_est = total_txns_24h * 2 if total_txns_24h > 0 else 0
        holder_score = min(10, round((holder_est / 100) ** 0.5 * 3, 1)) if holder_est > 0 else 0
        dist_score = 5.0 if pairs else 0
        social_score = min(10, round((twitter_followers / 1000) ** 0.4 * 3, 1)) if twitter_followers > 0 else 0
        trend_score = min(10, round(total_volume_24h / 1_000_000 * 2, 1)) if total_volume_24h > 0 else 0

        scores = {"liquidity": liq_score, "holder_count": holder_score, "holder_distribution": dist_score,
                  "social_volume": social_score, "social_trending": trend_score}

        # 详细分析文字
        details = {
            "liquidity": (
                f"Total DEX liquidity is ${total_liquidity:,.0f} across {pair_count} pairs. "
                f"24h volume is ${total_volume_24h:,.0f}. "
                f"{'Liquidity is deep with relatively low slippage risk.' if liq_score >= 7 else 'Liquidity is moderate; larger trades may face material slippage.' if liq_score >= 4 else 'Liquidity is critically thin and slippage risk is high.'}"
            ),
            "holder_count": (
                f"Approximately {total_txns_24h:,} transactions were recorded in 24h. "
                f"{'Activity and participation are strong.' if holder_score >= 7 else 'Transaction activity is moderate.' if holder_score >= 4 else 'Activity is very low, increasing liquidity-decay risk.'}"
            ),
            "holder_distribution": (
                f"{pair_count} active trading pairs were found. "
                f"{'Multiple venues reduce market concentration risk.' if pair_count >= 5 else 'Limited venue coverage raises concentration risk.' if pair_count >= 2 else 'A single venue creates very high concentration risk.'} "
                f"Full holder distribution requires a chain explorer data source."
            ),
            "social_volume": (
                f"The project has approximately {twitter_followers:,} X followers. "
                f"{'Community reach and discussion are strong.' if social_score >= 7 else 'Community reach is moderate.' if social_score >= 4 else 'Community reach and discussion are limited.'}"
            ),
            "social_trending": (
                f"24h volume is ${total_volume_24h:,.0f} against a ${market_cap:,.0f} market cap. "
                f"{'Turnover indicates strong market attention.' if trend_score >= 7 else 'Market attention is moderate.' if trend_score >= 4 else 'Momentum is weak and the asset may be losing attention.'}"
            ),
        }

        intent = intent or self._extract_request_intent(prompt)
        writing_profile = intent.get("writing_profile") or {}
        depth = writing_profile.get("depth", "standard")
        length = writing_profile.get("length", "standard")
        is_compact = depth == "concise" or length == "compact"
        is_detailed = depth in ("detailed", "academic") or length == "extended"
        liquidity_signal = (
            "deep with relatively low slippage risk" if liq_score >= 7
            else "moderate, so larger trades may face material slippage" if liq_score >= 4
            else "critically thin with high slippage and exit-capacity risk"
        )
        activity_signal = (
            "strong" if holder_score >= 7 else "moderate" if holder_score >= 4 else "very low"
        )
        venue_signal = (
            "diversified across several venues" if pair_count >= 5
            else "concentrated across a limited set of venues" if pair_count >= 2
            else "dependent on a single venue"
        )
        attention_signal = (
            "strong" if trend_score >= 7 else "moderate" if trend_score >= 4 else "weak"
        )

        if is_compact:
            details = {
                "liquidity": (
                    f"${total_liquidity:,.0f} DEX liquidity across {pair_count} pairs and "
                    f"${total_volume_24h:,.0f} 24h volume indicate liquidity is {liquidity_signal}."
                ),
                "holder_count": (
                    f"{total_txns_24h:,.0f} transactions in 24h indicate {activity_signal} activity."
                ),
                "holder_distribution": (
                    f"{pair_count} active pairs make venue exposure {venue_signal}; holder concentration is unverified."
                ),
                "social_volume": (
                    f"{twitter_followers:,.0f} X followers indicate "
                    f"{'strong' if social_score >= 7 else 'moderate' if social_score >= 4 else 'limited'} reach."
                ),
                "social_trending": (
                    f"${total_volume_24h:,.0f} 24h volume versus ${market_cap:,.0f} market cap indicates "
                    f"{attention_signal} attention."
                ),
            }
        elif is_detailed:
            details = {
                "liquidity": (
                    f"Across {pair_count} verified DEX pairs, aggregate quoted liquidity is "
                    f"${total_liquidity:,.0f} and trailing 24h volume is ${total_volume_24h:,.0f}. "
                    f"This profile is {liquidity_signal}, which directly affects executable position "
                    f"size and exit quality from the {self.current_persona} perspective. "
                    f"The conclusion aggregates reported pool liquidity and does not model order-by-order "
                    f"depth, route fragmentation, MEV, or stress-period withdrawals."
                ),
                "holder_count": (
                    f"The selected markets recorded {total_txns_24h:,.0f} buys and sells during the last "
                    f"24 hours, producing a {activity_signal} activity signal. Transaction frequency can "
                    f"support price discovery, but it cannot distinguish unique holders, bots, or repeated "
                    f"wallet activity. A holder-growth conclusion therefore requires explorer-level wallet "
                    f"cohort and retention data."
                ),
                "holder_distribution": (
                    f"Trading is available through {pair_count} active pairs and is {venue_signal}. "
                    f"Broader venue coverage can reduce single-pool dependency, while fragmented shallow "
                    f"liquidity may still worsen execution. This dimension remains methodologically limited "
                    f"because pair count is not a substitute for top-holder concentration, insider allocation, "
                    f"or unlock analysis."
                ),
                "social_volume": (
                    f"The available community source reports approximately {twitter_followers:,.0f} X "
                    f"followers, corresponding to a "
                    f"{'strong' if social_score >= 7 else 'moderate' if social_score >= 4 else 'limited'} reach signal. "
                    f"Follower count measures potential distribution rather than active discussion quality. "
                    f"Engagement rate, account quality, sentiment dispersion, and campaign-adjusted growth "
                    f"are not available in the current source set."
                ),
                "social_trending": (
                    f"Trailing 24h volume is ${total_volume_24h:,.0f} against a reported market cap of "
                    f"${market_cap:,.0f}, yielding a {attention_signal} attention signal under the rules model. "
                    f"This ratio is useful for relative turnover but does not establish directional demand "
                    f"or organic momentum. Price persistence, volume concentration, and multi-window social "
                    f"velocity should be reviewed in a full momentum study."
                ),
            }

        adjustments = PERSONA_WEIGHT_ADJUST.get(self.current_persona, {})
        dimension_results = []
        for dim in DIMENSIONS:
            adj = adjustments.get(dim["key"], 0)
            dimension_results.append({
                "dimension": dim["name"], "key": dim["key"],
                "score": scores.get(dim["key"], 0),
                "weight": dim["weight"] + adj,
                "detail": details.get(dim["key"], "Insufficient data"),
            })

        overall_score = sum(d["score"] * d["weight"] for d in dimension_results)
        total_weight = sum(d["weight"] for d in dimension_results)
        if total_weight > 0: overall_score /= total_weight

        risk = "green" if overall_score >= 7 else "yellow" if overall_score >= 4 else "red"

        recs = {
            "green": (
                f"The asset scores {overall_score:.1f}/10 with broadly healthy indicators. "
                f"DEX liquidity is ${total_liquidity:,.0f} and 24h volume is ${total_volume_24h:,.0f}. "
                f"Monitor liquidity changes and holder concentration before increasing exposure. "
                f"This fallback report is generated by the rules engine."
            ),
            "yellow": (
                f"The asset scores {overall_score:.1f}/10 and carries several material risks. "
                f"Watch liquidity, on-chain activity, and community reach for confirmation. "
                f"Use limited exposure or remain on watch until the weak signals improve. "
                f"This fallback report is generated by the rules engine."
            ),
            "red": (
                f"The asset scores {overall_score:.1f}/10 with multiple critical warnings. "
                f"Thin liquidity, stalled activity, or fading momentum may impair exit capacity. "
                f"Remain on watch and reassess only after on-chain indicators improve. "
                f"This fallback report is generated by the rules engine."
            ),
        }
        if is_compact:
            recommendation = (
                f"Score: {overall_score:.1f}/10. "
                f"{'Conditions are comparatively healthy, but verify holder concentration before acting.' if risk == 'green' else 'Keep exposure limited and wait for stronger activity and community confirmation.' if risk == 'yellow' else 'Risk is high; stay on watch until liquidity and activity recover.'}"
            )
        elif is_detailed:
            recommendation = (
                f"The asset scores {overall_score:.1f}/10 under the {self.current_persona} weighting model. "
                f"Its strongest observable support is DEX liquidity of ${total_liquidity:,.0f}, while the "
                f"principal uncertainty is the absence of verified holder-concentration and robust "
                f"social-engagement data. Treat the score as a comparative screening signal rather than "
                f"a forecast: validate pool depth, wallet cohorts, and multi-window momentum before "
                f"changing exposure. This detailed fallback preserves source figures after the language-model request failed."
            )
        else:
            recommendation = recs.get(risk, "Insufficient data")

        return {
            "token": token_info,
            "persona": self.current_persona,
            "dimensions": dimension_results,
            "overall_score": round(overall_score, 1),
            "risk_level": risk,
            "recommendation": recommendation,
            "health_indicators": {
                "positive": [f"DEX liquidity ${total_liquidity:,.0f}"] if liq_score >= 6 else [],
                "negative": ["Low 24h trading volume"] if total_volume_24h < 10000 else [],
            },
            "analysis_summary": f"Automated analysis based on {len(raw_data['_sources'])} data sources. Total liquidity: ${total_liquidity:,.0f}. {'Indicators are healthy' if risk == 'green' else 'Several indicators require attention' if risk == 'yellow' else 'High-risk conditions detected'}.",
        }
