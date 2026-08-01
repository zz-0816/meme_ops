"""
Agent 分析引擎 — 接入 DexScreener + CoinGecko + DeepSeek LLM
"""

import json
import os
import asyncio
import re
import hashlib
import copy
import time
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional

import httpx
from openai import AsyncOpenAI
from config import load_project_env
from intent import extract_analysis_intent, infer_writing_profile
from asset_resolver import is_contract_address, requested_asset_terms, select_exact_pairs
from database import get_persona_rag_entries, upsert_persona_rag_entry
from social import enrich_raw_data_with_social

load_project_env()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MEMORY_PROMPT_PATH = PROJECT_ROOT / "MEMORY_PROMPT.md"
PERSONAS_DIR = PROJECT_ROOT / "personas"
ANALYSIS_DATA_CACHE_SECONDS = max(
    30, int(os.getenv("ANALYSIS_DATA_CACHE_SECONDS", "90"))
)
_ANALYSIS_DATA_CACHE: dict[tuple[str, str, str], tuple[float, dict]] = {}

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

PERSONA_REPORT_CONFIG = {
    "operator": {
        "name": "Community Operator",
        "decision_label": "Ops Verdict",
        "focus": (
            "community quality, narrative momentum, content opportunities, audience "
            "activation, campaign readiness, and whether the community merits operating effort"
        ),
        "sections": [
            "Ops Verdict", "What Is Trending", "Community Evidence & Gaps",
            "Recommended Activities", "7-Day Action Plan", "Success Metrics",
        ],
        "comparison": (
            "Compare community signal coverage, social momentum, activation potential, "
            "content opportunities, and campaign readiness. Market data is only a secondary proxy."
        ),
    },
    "investor": {
        "name": "Investor",
        "decision_label": "Investment Verdict",
        "focus": "liquidity, execution risk, holder structure, turnover, and risk/reward",
        "sections": [
            "Investment Verdict", "Market Evidence", "Risk & Reward",
            "Invalidation Signals", "Monitoring Checklist",
        ],
        "comparison": "Compare liquidity, execution quality, market activity, concentration, and risk/reward.",
    },
    "builder": {
        "name": "Project Builder",
        "decision_label": "Build Priority",
        "focus": "product health, structural gaps, user activation, competitive position, and delivery priorities",
        "sections": [
            "Build Priority", "Diagnosis", "Competitive Gaps",
            "Improvement Backlog", "30-Day Delivery Plan",
        ],
        "comparison": "Compare structural health, product gaps, user activation, defensibility, and build priorities.",
    },
    "researcher": {
        "name": "Researcher",
        "decision_label": "Research Finding",
        "focus": "methodology, evidence quality, sector context, uncertainty, and follow-up hypotheses",
        "sections": [
            "Research Finding", "Evidence & Method", "Sector Context",
            "Limitations", "Follow-up Research",
        ],
        "comparison": "Compare evidence quality, sector context, methodological confidence, anomalies, and research gaps.",
    },
}

PERSONA_DIMENSION_NAMES = {
    "operator": {
        "liquidity": "Campaign Reach Proxy",
        "holder_count": "Audience Activity Proxy",
        "holder_distribution": "Community Distribution Proxy",
        "social_volume": "Community Signal Coverage",
        "social_trending": "Narrative Momentum",
    },
    "builder": {
        "liquidity": "Market Infrastructure",
        "holder_count": "User Activation",
        "holder_distribution": "Ecosystem Resilience",
        "social_volume": "User Feedback Coverage",
        "social_trending": "Product Momentum",
    },
    "researcher": {
        "liquidity": "Market Depth Evidence",
        "holder_count": "Activity Evidence",
        "holder_distribution": "Distribution Evidence",
        "social_volume": "Community Data Coverage",
        "social_trending": "Attention Signal",
    },
}

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or ""
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_TIMEOUT_SECONDS = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "90"))
DEEPSEEK_MAX_TOKENS_STANDARD = max(
    1600, int(os.getenv("DEEPSEEK_MAX_TOKENS_STANDARD", "3000"))
)
DEEPSEEK_MAX_TOKENS_EXTENDED = max(
    DEEPSEEK_MAX_TOKENS_STANDARD,
    int(os.getenv("DEEPSEEK_MAX_TOKENS_EXTENDED", "4200")),
)


def analysis_provider_status() -> dict:
    return {
        "provider": "deepseek" if DEEPSEEK_API_KEY else "rules",
        "configured": bool(DEEPSEEK_API_KEY),
        "model": DEEPSEEK_MODEL if DEEPSEEK_API_KEY else None,
        "timeout_seconds": DEEPSEEK_TIMEOUT_SECONDS,
    }


class MemeOpsAgent:
    """Web3 Meme 投研 Agent — 真实数据 + LLM 分析"""

    def __init__(self):
        self.memory_prompt = ""
        self.persona_prompt = ""
        self.current_persona = "operator"
        self.llm_client = None
        self.reload_memory()

    def reload_memory(self) -> str:
        if MEMORY_PROMPT_PATH.exists():
            self.memory_prompt = MEMORY_PROMPT_PATH.read_text(encoding="utf-8")
        return self.memory_prompt

    def set_persona(self, persona: str):
        if persona not in ("investor", "operator", "builder", "researcher"):
            persona = "operator"
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
                timeout=DEEPSEEK_TIMEOUT_SECONDS,
                max_retries=0,
            )
        return self.llm_client

    # ============ 分析主流程 ============

    async def analyze(
        self, prompt: str, report_style: str | None = None,
        owner_address: str | None = None,
        progress_callback: Callable[[int, str], None] | None = None,
    ) -> dict:
        analysis_started = time.monotonic()
        stage_started = analysis_started
        performance_ms: dict[str, int] = {}

        def finish_stage(name: str) -> None:
            nonlocal stage_started
            now = time.monotonic()
            performance_ms[name] = round((now - stage_started) * 1000)
            stage_started = now

        def progress(value: int, stage: str) -> None:
            if progress_callback:
                progress_callback(value, stage)

        if not self.memory_prompt:
            self.reload_memory()
        # 每次分析强制重读 persona 文件
        self.set_persona(self.current_persona)

        request_intent = self._extract_request_intent(prompt)
        if report_style and report_style.strip():
            request_intent["style_instruction"] = report_style.strip()
            request_intent["writing_profile"] = infer_writing_profile(report_style)

        rag_context = self._load_and_capture_rag(
            owner_address, prompt, report_style, request_intent,
        )
        request_intent["rag_context"] = [
            {
                "type": item.get("entry_type"),
                "title": item.get("title"),
                "content": item.get("content"),
            }
            for item in rag_context
        ]

        # 1. 拉取真实数据
        cache_key = (
            "market",
            str(request_intent.get("token_query") or "").strip().lower(),
            str(request_intent.get("chain") or "unknown").strip().lower(),
        )
        now = time.monotonic()
        for expired_key, (expires_at, _) in list(_ANALYSIS_DATA_CACHE.items()):
            if expires_at <= now:
                _ANALYSIS_DATA_CACHE.pop(expired_key, None)
        cached = _ANALYSIS_DATA_CACHE.get(cache_key)
        cache_hit = bool(cached and cached[0] > now)
        try:
            if cache_hit:
                progress(24, "Reusing a fresh verified market snapshot")
                raw_data = copy.deepcopy(cached[1])
            else:
                progress(14, "Fetching market and identity data")
                raw_data = await self._fetch_raw_data(
                    request_intent["token_query"],
                    request_intent.get("chain"),
                    prompt,
                )
                # Cache only public market facts. Wallet-bound social state can
                # change at any time (for example immediately after an OAuth or
                # Telegram group binding) and must never be served from this
                # market-data cache.
                raw_data.pop("social", None)
                _ANALYSIS_DATA_CACHE[cache_key] = (
                    time.monotonic() + ANALYSIS_DATA_CACHE_SECONDS,
                    copy.deepcopy(raw_data),
                )
        except Exception as e:
            raw_data = {"query": prompt, "dexscreener": {}, "coingecko": {}, "_sources": [], "_error": str(e)}
        finish_stage("market_data")

        # Always resolve private social evidence after the market cache. This
        # makes a newly connected X account or Telegram community visible on
        # the very next analysis without waiting for the cache TTL.
        progress(34, "Joining current wallet-private social intelligence")
        try:
            raw_data.pop("social", None)
            raw_data["_sources"] = [
                source for source in (raw_data.get("_sources") or [])
                if source != "Social intelligence"
            ]
            raw_data = await enrich_raw_data_with_social(raw_data, owner_address)
        except Exception as error:
            raw_data["social"] = {
                "connected": False,
                "binding_connected": False,
                "status": "collection-error",
                "metrics": [],
                "rag_documents": [],
                "collection_error": str(error),
            }
        finish_stage("social_data")

        # 2. 用 LLM 打分 + 生成报告
        progress(46, "Building the persona-specific evidence model")
        analysis_core = self._fallback_analyze(prompt, raw_data, request_intent)
        try:
            if DEEPSEEK_API_KEY:
                progress(55, "Writing the report with DeepSeek")
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
        finish_stage("report_generation")

        progress(72, "Validating facts and report structure")
        report = self._enrich_report_content(report, raw_data, request_intent)
        report["rag_context"] = {
            "wallet_private": bool(owner_address),
            "persona": self.current_persona,
            "entries_used": len(rag_context),
            "modules": [
                item.get("title") for item in rag_context
                if item.get("entry_type") == "module"
            ],
        }
        self._remember_report_output(owner_address, report)

        report["data_sources"] = raw_data["_sources"]
        report["analyzed_at"] = datetime.now().isoformat()
        report["request_intent"] = request_intent
        report["asset_match"] = raw_data.get("asset_match", "unknown")
        report["data_cache"] = {
            "hit": cache_hit,
            "ttl_seconds": ANALYSIS_DATA_CACHE_SECONDS,
        }
        finish_stage("validation")
        performance_ms["total"] = round((time.monotonic() - analysis_started) * 1000)
        report["performance_ms"] = performance_ms

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

    def _load_and_capture_rag(
        self, owner_address: str | None, prompt: str,
        report_style: str | None, intent: dict,
    ) -> list[dict]:
        """Capture explicitly requested modules and retrieve wallet-private persona memory."""
        if not owner_address:
            return []
        combined = " ".join(value for value in (prompt, report_style or "") if value)
        module_patterns = (
            r"(?:add|include|create|new)\s+(?:an?\s+)?([a-z][a-z0-9 -]{2,48})\s+(?:section|module)",
            r"(?:新增|添加|加入|包含)(.{2,24}?)(?:模块|章节|部分)",
        )
        for pattern in module_patterns:
            for match in re.finditer(pattern, combined, re.IGNORECASE):
                title = re.sub(r"\s+", " ", match.group(1)).strip(" :，。")
                if not title:
                    continue
                entry_key = hashlib.sha1(title.lower().encode("utf-8")).hexdigest()[:16]
                upsert_persona_rag_entry(
                    owner_address, self.current_persona, "module", entry_key,
                    title[:64],
                    (
                        f"Include a distinct '{title[:64]}' report module when relevant. "
                        "Keep it evidence-grounded and label unavailable inputs explicitly."
                    ),
                    [title, self.current_persona],
                )
        if report_style and report_style.strip():
            style = report_style.strip()[:500]
            entry_key = hashlib.sha1(style.lower().encode("utf-8")).hexdigest()[:16]
            upsert_persona_rag_entry(
                owner_address, self.current_persona, "effect", entry_key,
                "Preferred report effect", style,
                list((intent.get("writing_profile") or {}).values()),
            )
        return get_persona_rag_entries(
            owner_address, self.current_persona, combined, limit=8,
        )

    def _remember_report_output(
        self, owner_address: str | None, report: dict,
    ) -> None:
        if not owner_address:
            return
        keywords = [str(value)[:80] for value in report.get("report_keywords") or [] if value]
        if not keywords:
            return
        entry_key = hashlib.sha1("|".join(sorted(keywords)).encode("utf-8")).hexdigest()[:16]
        upsert_persona_rag_entry(
            owner_address, self.current_persona, "keywords", entry_key,
            "Reusable report keywords", ", ".join(keywords[:12]), keywords[:12],
        )

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
        persona_instructions = (
            self.persona_prompt
            if self.persona_prompt
            else "Default Community Operator perspective"
        )
        persona_config = PERSONA_REPORT_CONFIG[self.current_persona]
        rag_context = intent.get("rag_context") or []
        rag_instructions = "\n".join(
            f"- {item.get('title')}: {item.get('content')}"
            for item in rag_context
        ) or "- No wallet-specific modules have been learned yet."

        system_prompt = f"""You are the report agent inside an Ops-first Web3 Meme product.
Follow exactly one Persona definition below; never blend it with another persona.
Write every user-facing string, dimension, conclusion, label, and recommendation in English.

╔══════════════════════════════════════════════════════════╗
║  PERSONA DEFINITION (follow this perspective)             ║
╚══════════════════════════════════════════════════════════╝
{persona_instructions}

Exclusive report contract for {persona_config['name']}:
- Primary focus: {persona_config['focus']}
- Lead with the conclusion labelled "{persona_config['decision_label']}", then inference,
  actions, and only then supporting evidence.
- Required modules: {json.dumps(persona_config['sections'], ensure_ascii=False)}
- Community Operator reports prioritize operations and may omit price/liquidity prose
  unless explicitly useful as a weak activity proxy. They always include a 7-day plan.
- Never report a missing social value as zero. Distinguish an unbound identity
  (not_connected), a bound identity without a metric snapshot (connected_no_data),
  a required Telegram group step (action_required), and an unsupported source
  (not_configured). Use neutral wording and do not infer follower or subscriber counts.
- Distinguish verified facts, directional proxies, inferences, and unavailable data.

Wallet-private persona RAG context (preferences only, never factual evidence):
{rag_instructions}

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
  "executive_conclusion": "Conclusion-first verdict for this exact Persona",
  "key_inferences": [
    {{"inference":"Useful further conclusion","evidence":"Source-backed evidence","confidence":"high|medium|low"}}
  ],
  "report_sections": [
    {{"title":"Persona-specific module","content":"Useful prose","status":"verified|proxy|not_connected|connected_no_data|action_required|not_configured"}}
  ],
  "action_plan": [
    {{"day":"Day 1","theme":"Concrete theme","actions":["Specific action"],"kpi":"Measurable KPI","dependency":"Needed channel or data"}}
  ],
  "data_gaps": [
    {{"source":"X community metrics","status":"not_connected|connected_no_data|action_required|not_configured","impact":"What cannot be concluded or which setup step remains"}}
  ],
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
        }
        user_prompt = f"""Analyze this asset: "{intent.get('token_query')}"
Requested chain: {intent.get('chain') or 'not specified'}
Original user request: "{prompt}"
Requested writing style: "{style_instruction}"

Fixed quantitative core (binding; style must not change scores or risk):
{json.dumps(fixed_core_payload, ensure_ascii=False)}

Data:
{data_summary}

Return the requested JSON in English. Make the chosen Persona visibly distinct in
conclusion, modules, inferences, and actions. Adapt tone, clarity, and level of detail
to the requested writing style, but never alter, omit, or invent factual market metrics."""

        llm = self._get_llm()
        try:
            profile = intent.get("writing_profile") or {}
            max_tokens = (
                DEEPSEEK_MAX_TOKENS_EXTENDED
                if profile.get("length") == "extended"
                else DEEPSEEK_MAX_TOKENS_STANDARD
            )
            last_error = None
            previous_content = None
            for attempt in range(2):
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ]
                if previous_content:
                    messages.extend([
                        {"role": "assistant", "content": previous_content[:12000]},
                        {
                            "role": "user",
                            "content": (
                                "Repair the previous response into complete valid JSON. "
                                "Keep all source facts and locked scores unchanged. Use shorter "
                                "strings if needed. Return JSON only."
                            ),
                        },
                    ])
                resp = await llm.chat.completions.create(
                    model=DEEPSEEK_MODEL,
                    messages=messages,
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
                    previous_content = content
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
        def safe_styled_detail(styled: dict, core: dict) -> str:
            candidate = str(styled.get("detail") or styled.get("notes") or "")
            fallback = str(core.get("detail") or "")
            if not candidate:
                return fallback
            # Reject model prose that introduces a numeric claim absent from the
            # source-derived core. This catches invented thresholds/position sizes
            # while still allowing non-numeric persona and tone changes.
            number_pattern = r"(?<![A-Za-z])[$#]?\d[\d,.]*(?:\.\d+)?%?[KMB]?"
            candidate_numbers = set(re.findall(number_pattern, candidate, re.IGNORECASE))
            fallback_numbers = set(re.findall(number_pattern, fallback, re.IGNORECASE))
            if candidate_numbers - fallback_numbers:
                return fallback
            return candidate

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
                "detail": safe_styled_detail(styled, core_dimension),
            })
        styled_report["dimensions"] = dimensions
        styled_report["overall_score"] = analysis_core.get("overall_score")
        styled_report["risk_level"] = analysis_core.get("risk_level")
        # Scores and source-derived facts are immutable. Persona prose is not:
        # preserving it is what makes Operator, Investor, Builder, and Researcher
        # reports observably different.
        styled_report["persona_recommendation"] = styled_report.get("recommendation")
        styled_report["recommendation"] = analysis_core.get("recommendation")
        styled_report["health_indicators"] = (
            styled_report.get("health_indicators")
            or analysis_core.get("health_indicators")
        )
        for field in (
            "executive_conclusion", "key_inferences", "report_sections",
            "action_plan", "data_gaps",
        ):
            if not styled_report.get(field):
                styled_report[field] = analysis_core.get(field)
        # Connection states and provider gaps come from the wallet-bound source
        # model, not from the language model. Keep them immutable so a connected
        # identity cannot be rewritten as disconnected (or vice versa).
        styled_report["data_gaps"] = analysis_core.get("data_gaps") or []
        styled_report["decision_label"] = (
            styled_report.get("decision_label")
            or analysis_core.get("decision_label")
        )
        social_gap = any(
            str(item.get("status") or "").lower()
            in {"not_connected", "connected_no_data", "action_required", "not_configured"}
            and any(
                marker in str(item.get("source") or "").lower()
                for marker in ("x", "reddit", "social", "community", "telegram", "discord")
            )
            for item in analysis_core.get("data_gaps") or []
        )
        if self.current_persona == "operator" and social_gap:
            # Without community telemetry the model may be tempted to call the
            # community "quiet", "weak", or "strong" from market proxies. Keep
            # the source-derived neutral verdict/sections while retaining its
            # stylistic action plan.
            styled_report["model_executive_conclusion"] = styled_report.get(
                "executive_conclusion"
            )
            for field in (
                "executive_conclusion", "key_inferences",
                "report_sections", "data_gaps",
            ):
                styled_report[field] = analysis_core.get(field)
        styled_report["analysis_core"] = {
            "locked": True,
            "overall_score": analysis_core.get("overall_score"),
            "risk_level": analysis_core.get("risk_level"),
            "dimension_scores": {
                str(item.get("key")): item.get("score")
                for item in analysis_core.get("dimensions") or []
            },
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

        def connected_metric(value, formatter="{:,.0f}"):
            """Zero/None from social providers means unavailable, not a verified zero."""
            if value in (None, "", 0, 0.0, "0"):
                return "not connected"
            try:
                return formatter.format(float(value))
            except (TypeError, ValueError):
                return str(value)

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
            parts.append(f"- X followers: {connected_metric(community.get('twitter_followers'))}")
            parts.append(f"- Reddit subscribers: {connected_metric(community.get('reddit_subscribers'))}")
            parts.append(f"- Telegram members: {connected_metric(community.get('telegram_channel_user_count'))}")
            parts.append(
                "- Missing social metrics are unavailable inputs. Do not interpret them "
                "as zero audience, zero discussion, or a weak community."
            )
            desc = cg.get("description", {}).get("en", "")
            if desc:
                parts.append(f"- Description: {desc[:300]}...")
            parts.append(f"- Genesis date: {cg.get('genesis_date','?')}")
        else:
            parts.append("### CoinGecko: no data found")

        social = raw_data.get("social") or {}
        provider_states = social.get("providers") or {}
        if social.get("connected"):
            parts.append("\n### Connected social intelligence")
            for metric in social.get("metrics") or []:
                provider = str(metric.get("provider") or "social").upper()
                available = []
                for key, label in (
                    ("followers", "followers"),
                    ("members", "members"),
                    ("mentions_24h", "mentions/24h"),
                    ("posts_24h", "posts/24h"),
                    ("engagements_24h", "engagements/24h"),
                ):
                    if metric.get(key) is not None:
                        available.append(f"{label}: {metric[key]:,}")
                parts.append(
                    f"- {provider}: {', '.join(available) or 'snapshot available'} "
                    f"(source={metric.get('source_mode')}, confidence={metric.get('confidence')})"
                )
            for document in (social.get("rag_documents") or [])[:4]:
                parts.append(
                    f"- Social RAG [{document.get('platform')}]: {document.get('content')}"
                )
        else:
            parts.append("\n### Social intelligence: no verified asset snapshot yet")
            if social.get("binding_connected"):
                parts.append(
                    "- A wallet-bound social identity is connected, but no verified metric "
                    "snapshot is available for this exact asset yet. Do not label the account "
                    "or community as disconnected."
                )
            else:
                parts.append(
                    "- No shared or wallet-bound X/Telegram snapshot is available for this exact asset. "
                    "Do not infer zero followers, weak discussion, or low community quality."
                )
        if provider_states:
            parts.append("- Provider states:")
            for provider in ("x", "telegram", "reddit"):
                state = provider_states.get(provider) or {}
                parts.append(
                    f"  - {provider.upper()}: {state.get('status', 'unknown')}"
                    + (
                        f"; connected identity @{state.get('username')}"
                        if state.get("identity_connected") and state.get("username")
                        else "; connected identity"
                        if state.get("identity_connected")
                        else ""
                    )
                    + (
                        f"; {state.get('community_count')} bound communities"
                        if provider == "telegram" and state.get("community_count") is not None
                        else ""
                    )
                )
        if social.get("collection_error"):
            safe_error = str(social.get("collection_error"))[:240]
            parts.append(
                "- Collection attempt failed after identity verification. Treat this as "
                "connected-but-unavailable provider data, not as a disconnected account. "
                f"Provider status: {safe_error}"
            )

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
        community_data = cg.get("community_data", {}) if cg else {}
        twitter_raw = community_data.get("twitter_followers")
        reddit_raw = community_data.get("reddit_subscribers")
        twitter_followers = twitter_raw if isinstance(twitter_raw, (int, float)) and twitter_raw > 0 else None
        reddit_subscribers = reddit_raw if isinstance(reddit_raw, (int, float)) and reddit_raw > 0 else None
        social_context = raw_data.get("social") or {}
        social_metrics = social_context.get("metrics") or []
        def aggregate_social_metric(provider: str, key: str) -> int | None:
            values = [
                int(item[key]) for item in social_metrics
                if item.get("provider") == provider and item.get(key) is not None
            ]
            return sum(values) if values else None

        x_mentions = aggregate_social_metric("x", "mentions_24h")
        x_posts = aggregate_social_metric("x", "posts_24h")
        x_engagements = aggregate_social_metric("x", "engagements_24h")
        x_active_authors = aggregate_social_metric("x", "active_authors_24h")
        telegram_members = aggregate_social_metric("telegram", "members")
        telegram_posts = aggregate_social_metric("telegram", "posts_24h")
        telegram_active_authors = aggregate_social_metric(
            "telegram", "active_authors_24h",
        )
        social_connected = bool(
            twitter_followers is not None
            or reddit_subscribers is not None
            or social_context.get("connected")
        )
        provider_states = social_context.get("providers") or {}
        social_binding_connected = bool(social_context.get("binding_connected"))
        x_state = (provider_states.get("x") or {}).get("status")
        telegram_state = (provider_states.get("telegram") or {}).get("status")
        if social_binding_connected and not social_connected:
            availability_parts = []
            if x_state == "connected_no_data":
                availability_parts.append(
                    "X identity is connected, but the current API plan or permissions "
                    "did not return an asset-level metric snapshot"
                )
            if telegram_state == "group_not_bound":
                availability_parts.append(
                    "Telegram identity is connected, but no operated group/channel is bound"
                )
            elif telegram_state == "connected_no_data":
                availability_parts.append(
                    "Telegram is connected to a community, but its latest metric sync is unavailable"
                )
            social_availability = (
                "; ".join(availability_parts)
                or "A wallet-bound social identity is connected, but verified asset-level metrics are not available yet"
            )
            social_availability += ". Missing metrics remain unknown rather than zero."
        else:
            social_availability = (
                "X and Telegram identities are not connected for this wallet, and Reddit "
                "analytics are not configured. Missing metrics remain unknown rather than zero."
            )
        market_cap = (cg.get("market_data", {}).get("market_cap", {}).get("usd", 0) or 0) if cg else 0

        # 详细打分
        liq_score = min(10, round((total_liquidity / 100_000) ** 0.4 * 4, 1)) if total_liquidity > 0 else 0
        holder_est = total_txns_24h * 2 if total_txns_24h > 0 else 0
        holder_score = min(10, round((holder_est / 100) ** 0.5 * 3, 1)) if holder_est > 0 else 0
        dist_score = 5.0 if pairs else 0
        # Missing coverage must not penalize the asset as if it had a verified zero audience.
        social_score = (
            min(10, round((twitter_followers / 1000) ** 0.4 * 3, 1))
            if twitter_followers is not None
            else min(10, round((telegram_members / 1000) ** 0.4 * 3, 1))
            if telegram_members is not None
            else min(10, round((x_mentions / 100) ** 0.4 * 3, 1))
            if x_mentions is not None
            else 5.0
        )
        social_evidence_parts = []
        if twitter_followers is not None:
            social_evidence_parts.append(f"{twitter_followers:,.0f} connected X followers")
        if x_mentions is not None:
            social_evidence_parts.append(f"{x_mentions:,.0f} X mentions in the provider window")
        elif x_posts is not None:
            social_evidence_parts.append(f"{x_posts:,.0f} matched X posts in the provider window")
        if x_active_authors is not None:
            social_evidence_parts.append(f"{x_active_authors:,.0f} active X authors")
        if x_engagements is not None:
            social_evidence_parts.append(f"{x_engagements:,.0f} observed X engagements")
        if telegram_members is not None:
            social_evidence_parts.append(
                f"{telegram_members:,.0f} members across connected Telegram communities"
            )
        if telegram_posts is not None:
            social_evidence_parts.append(
                f"{telegram_posts:,.0f} Telegram messages/posts in 24h"
            )
        if telegram_active_authors is not None:
            social_evidence_parts.append(
                f"{telegram_active_authors:,.0f} active Telegram contributors in 24h"
            )
        social_reach_evidence = (
            ", ".join(social_evidence_parts)
            if social_evidence_parts else "No connected social audience metric"
        )
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
                (
                    f"The connected source reports {social_reach_evidence}. "
                    f"{'Community reach appears strong.' if social_score >= 7 else 'Community reach appears moderate.'}"
                )
                if social_connected else
                social_availability + " No audience-size or discussion-quality conclusion is made; "
                "on-chain activity is shown only as a directional proxy, not as a substitute."
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
                    (
                        f"{social_reach_evidence} indicates "
                        f"{'strong' if social_score >= 7 else 'moderate'} potential reach."
                    )
                    if social_connected else
                    social_availability + " Community reach is not scored as zero."
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
                    (
                        f"The connected community source reports {social_reach_evidence}, "
                        f"corresponding to a "
                        f"{'strong' if social_score >= 7 else 'moderate'} potential-reach signal. "
                        "Follower count does not establish discussion quality. Engagement rate, account "
                        "quality, sentiment dispersion, and campaign-adjusted growth remain unavailable."
                    )
                    if social_connected else
                    social_availability + " The report therefore makes no audience-size, "
                    "engagement-quality, or sentiment claim. "
                    "Any transaction activity used elsewhere is explicitly a directional proxy and not "
                    "a replacement for community telemetry."
                ),
                "social_trending": (
                    f"Trailing 24h volume is ${total_volume_24h:,.0f} against a reported market cap of "
                    f"${market_cap:,.0f}, yielding a {attention_signal} attention signal under the rules model. "
                    f"This ratio is useful for relative turnover but does not establish directional demand "
                    f"or organic momentum. Price persistence, volume concentration, and multi-window social "
                    f"velocity should be reviewed in a full momentum study."
                ),
            }

        if self.current_persona == "operator":
            social_detail = (
                f"The connected source reports {social_reach_evidence}. "
                "Treat reach as distribution capacity only; engagement quality, "
                "active contributors, retention, and content conversion still require direct telemetry."
                if social_connected else
                social_availability + " No audience-size, engagement, sentiment, or "
                "community-quality claim is made."
            )
            if is_compact:
                details = {
                    "liquidity": (
                        f"{pair_count} active venues indicate the asset is operationally accessible; "
                        "this is a campaign-continuity proxy, not a community-quality score."
                    ),
                    "holder_count": (
                        f"{total_txns_24h:,.0f} 24h transactions show possible audience attention, "
                        "but do not identify unique or retained community members."
                    ),
                    "holder_distribution": (
                        f"Activity spans {pair_count} pairs; community distribution across X, Reddit, "
                        "Telegram, and Discord is not yet verified."
                    ),
                    "social_volume": social_detail,
                    "social_trending": (
                        f"${total_volume_24h:,.0f} in 24h DEX volume is a directional attention proxy. "
                        "It cannot identify the trending narrative or prove organic conversation."
                    ),
                }
            else:
                methodology = (
                    "For community operations, this construct has low validity as a direct measure "
                    "of participation and must not be used to infer audience quality. "
                    if is_detailed else
                    "This does not establish community quality. "
                )
                details = {
                    "liquidity": (
                        f"The asset is accessible across {pair_count} exact-matched venues. "
                        "Operational accessibility can reduce friction around a campaign, but liquidity "
                        "and price depth are deliberately secondary in this persona. " + methodology
                    ),
                    "holder_count": (
                        f"The matched markets recorded {total_txns_24h:,.0f} buys and sells in 24h. "
                        "This is a possible attention proxy, not a count of unique participants, active "
                        "contributors, new members, or retained members. " + methodology
                    ),
                    "holder_distribution": (
                        f"Market activity is distributed across {pair_count} trading pairs. "
                        "That venue footprint cannot substitute for community distribution across X, "
                        "Reddit, Telegram, Discord, regions, or contributor cohorts. " + methodology
                    ),
                    "social_volume": social_detail,
                    "social_trending": (
                        f"Trailing DEX volume is ${total_volume_24h:,.0f}. It may indicate a moment worth "
                        "investigating, but it cannot reveal which meme, creator, post, or narrative is "
                        "trending. Connect social time series and content-level engagement before planning "
                        "a scaled campaign. " + methodology
                    ),
                }

        adjustments = PERSONA_WEIGHT_ADJUST.get(self.current_persona, {})
        dimension_results = []
        for dim in DIMENSIONS:
            adj = adjustments.get(dim["key"], 0)
            dimension_results.append({
                "dimension": PERSONA_DIMENSION_NAMES.get(
                    self.current_persona, {},
                ).get(dim["key"], dim["name"]),
                "key": dim["key"],
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

        data_gaps = []
        collection_errors = social_context.get("collection_errors") or {}
        x_collection_error = next(
            (
                value for key, value in collection_errors.items()
                if str(key).startswith("x_")
            ),
            None,
        )
        if x_state != "ready" and twitter_followers is None and x_mentions is None:
            data_gaps.append({
                "source": "X community metrics",
                "status": (
                    "action_required"
                    if x_collection_error else
                    "connected_no_data"
                    if x_state == "connected_no_data"
                    else "not_connected"
                ),
                "impact": (
                    str(x_collection_error.get("message"))
                    if x_collection_error else
                    "X identity is connected, but no asset-level metric snapshot was returned. "
                    "Check the X API access tier and tweet.read permissions; audience size, "
                    "engagement quality, and sentiment remain unverified."
                    if x_state == "connected_no_data" else
                    "Connect X to collect wallet-authorized asset-level conversation signals. "
                    "Audience size, engagement quality, and sentiment remain unverified."
                ),
            })
        if telegram_state != "ready" and telegram_members is None:
            telegram_status = (
                "action_required" if telegram_state == "group_not_bound"
                else "connected_no_data" if telegram_state == "connected_no_data"
                else "not_connected"
            )
            telegram_impact = (
                "Telegram identity is connected. Bind the operated group or channel with the "
                "generated /connect code before member metrics can be collected."
                if telegram_state == "group_not_bound" else
                "Telegram identity and community are connected, but the latest member-count "
                "sync is unavailable. Verify that the Bot remains in the community."
                if telegram_state == "connected_no_data" else
                "Connect Telegram identity and bind an operated group or channel to collect "
                "member metrics."
            )
            data_gaps.append({
                "source": "Telegram community metrics",
                "status": telegram_status,
                "impact": telegram_impact,
            })
        if reddit_subscribers is None:
            data_gaps.append({
                "source": "Reddit community metrics",
                "status": "not_configured",
                "impact": (
                    "Reddit ingestion is not configured in this release; subscriber, post, "
                    "comment, and sentiment signals are therefore unavailable."
                ),
            })
        data_gaps.append({
            "source": "Wallet-level holder distribution",
            "status": "not_connected",
            "impact": "Unique-holder growth and concentration require a chain explorer source.",
        })

        persona_config = PERSONA_REPORT_CONFIG[self.current_persona]
        if self.current_persona == "operator":
            executive_conclusion = (
                "Ops Verdict: treat this community as a validation sprint, not a scaled "
                "campaign yet. Transaction activity offers a directional attention signal, "
                "but verified community telemetry is not available yet, so first establish the audience "
                "baseline and test one repeatable participation loop."
                if not social_connected else
                "Ops Verdict: the available audience and activity signals justify a focused "
                "seven-day activation sprint. Prioritize a repeatable participation loop and "
                "measure qualified contributors rather than raw impressions."
            )
            recommendation = (
                (
                    f"Use the verified baseline ({social_reach_evidence}) to run a focused seven-day "
                    "community validation sprint. Build the content theme around the current conversation, "
                    "then compare contributor conversion and engagement against this baseline before scaling."
                )
                if social_connected else
                "Start with a seven-day community validation sprint. Complete any remaining "
                "social metric setup, map the active conversation, publish one native meme prompt, run "
                "a live interaction, and review contributor conversion before committing more "
                "operating resources."
            )
            report_sections = [
                {
                    "title": "What Is Trending",
                    "content": (
                        (
                            f"The connected social snapshot reports {social_reach_evidence}. "
                            f"Alongside {total_txns_24h:,.0f} DEX transactions and "
                            f"${total_volume_24h:,.0f} in 24h volume, this identifies a live attention "
                            "window; review the matched posts before selecting a campaign narrative."
                        )
                        if social_connected else
                        f"{total_txns_24h:,.0f} DEX transactions and ${total_volume_24h:,.0f} "
                        "in 24h volume indicate current market attention, but they do not reveal "
                        "which narratives or community posts are driving it."
                    ),
                    "status": "proxy",
                },
                {
                    "title": "Community Evidence & Gaps",
                    "content": (
                        social_availability + " Establish engagement, active-contributor, "
                        "retention, and content-baseline measurements before claiming community strength."
                        if not social_connected else
                        "Connected audience size is a distribution signal; validate engagement "
                        "quality, active contributors, retention, and content conversion next."
                    ),
                    "status": (
                        "connected_no_data"
                        if social_binding_connected and not social_connected
                        else "not_connected" if not social_connected
                        else "verified"
                    ),
                },
                {
                    "title": "Operating Opportunity",
                    "content": (
                        "Turn current attention into a simple loop: discover the strongest native "
                        "narrative, invite low-friction meme participation, spotlight contributors, "
                        "then convert the best response into a live community event."
                    ),
                    "status": "inference",
                },
            ]
            day_themes = [
                ("Day 1", "Join and listen", ["Join the primary X conversation; map 20 relevant accounts and 5 recurring topics."], "20 accounts mapped; 5 themes tagged"),
                ("Day 2", "Content direction", ["Draft three narrative pillars and publish one native conversation prompt."], "3 pillars approved; baseline engagement recorded"),
                ("Day 3", "Contributor activation", ["Invite 10 visible participants to a lightweight meme remix challenge."], "10 invites; at least 3 qualified submissions"),
                ("Day 4", "Social proof", ["Feature the strongest community contribution and credit its creator."], "1 feature; contributor response rate tracked"),
                ("Day 5", "Live interaction", ["Host an X Space, AMA, or live thread around the highest-response theme."], "1 event; attendance and questions recorded"),
                ("Day 6", "Retention loop", ["Follow up with participants and invite them into the next content cycle."], "30% participant return or reply rate"),
                ("Day 7", "Review and decide", ["Compare reach, engagement, contributors, and retention; choose scale, iterate, or pause."], "One documented resource-allocation decision"),
            ]
            action_plan = [
                {
                    "day": day, "theme": theme, "actions": actions, "kpi": kpi,
                    "dependency": (
                        f"Measure lift against the connected baseline: {social_reach_evidence}"
                        if social_connected else
                        "Complete social metric collection for verified measurement"
                    ),
                }
                for day, theme, actions, kpi in day_themes
            ]
            key_inferences = [
                {
                    "inference": "There is observable attention, but community quality remains unverified.",
                    "evidence": f"{total_txns_24h:,.0f} 24h transactions are a market-activity proxy; social telemetry is {'verified' if social_connected else 'identity connected but awaiting metrics' if social_binding_connected else 'not connected'}.",
                    "confidence": "medium" if social_connected else "low",
                },
                {
                    "inference": "A small validation sprint is more appropriate than a large campaign.",
                    "evidence": "The current source set cannot measure active contributors, retention, or content conversion.",
                    "confidence": "medium",
                },
            ]
        elif self.current_persona == "builder":
            executive_conclusion = (
                "Build Priority: close the measurement gap before optimizing growth. "
                "Instrument activation, holder cohorts, and feedback channels, then address "
                "the weakest verified product signal."
            )
            report_sections = [
                {"title": "Diagnosis", "content": "Market activity exists, but user and retention instrumentation is incomplete.", "status": "proxy"},
                {"title": "Improvement Backlog", "content": "Prioritize analytics coverage, activation funnels, and a feedback-to-release loop.", "status": "inference"},
            ]
            action_plan = [
                {"day": "Week 1", "theme": "Instrumentation", "actions": ["Connect wallet cohorts and community feedback sources."], "kpi": "Core activation funnel measurable", "dependency": "Explorer and social APIs"},
                {"day": "Week 2", "theme": "Activation", "actions": ["Ship the highest-impact onboarding improvement."], "kpi": "Activation baseline and uplift reported", "dependency": "Week 1 baseline"},
            ]
            key_inferences = [{"inference": "The main blocker is observability, not a proven lack of demand.", "evidence": "Market activity is visible while user/community cohorts are not.", "confidence": "medium"}]
        elif self.current_persona == "researcher":
            executive_conclusion = (
                "Research Finding: the evidence supports a market-activity description, "
                "but not a community-health or holder-quality conclusion. Treat missing "
                "sources as scope limitations, not zero observations."
            )
            report_sections = [
                {"title": "Evidence & Method", "content": "DEX pair aggregates support liquidity and transaction observations.", "status": "verified"},
                {"title": "Limitations", "content": "Social and wallet-cohort sources are incomplete; causal interpretation is not warranted.", "status": "not_connected"},
            ]
            action_plan = [
                {"day": "Next study", "theme": "Data completion", "actions": ["Add social time series and explorer-level holder cohorts."], "kpi": "Coverage matrix completed", "dependency": "Social and explorer APIs"},
            ]
            key_inferences = [{"inference": "Observed turnover cannot establish organic community momentum.", "evidence": "Market activity and social engagement measure different constructs.", "confidence": "high"}]
        else:
            executive_conclusion = (
                f"Investment Verdict: the asset scores {overall_score:.1f}/10. "
                "Use the result as a screening signal; verify execution depth, holder "
                "concentration, and missing social coverage before changing exposure."
            )
            report_sections = [
                {"title": "Market Evidence", "content": f"Aggregate DEX liquidity is ${total_liquidity:,.0f} across {pair_count} exact pairs.", "status": "verified"},
                {"title": "Risk & Reward", "content": recommendation, "status": "inference"},
            ]
            action_plan = [
                {"day": "Before exposure", "theme": "Validate execution", "actions": ["Check route depth, holder concentration, and invalidation thresholds."], "kpi": "Position and exit limits documented", "dependency": "Explorer-level holder data"},
            ]
            key_inferences = [{"inference": "Execution quality is the primary observable decision input.", "evidence": f"${total_liquidity:,.0f} aggregate liquidity and ${total_volume_24h:,.0f} 24h volume.", "confidence": "medium"}]

        return {
            "token": token_info,
            "persona": self.current_persona,
            "dimensions": dimension_results,
            "overall_score": round(overall_score, 1),
            "risk_level": risk,
            "recommendation": recommendation,
            "executive_conclusion": executive_conclusion,
            "key_inferences": key_inferences,
            "report_sections": report_sections,
            "action_plan": action_plan,
            "data_gaps": data_gaps,
            "decision_label": persona_config["decision_label"],
            "health_indicators": {
                "positive": [f"DEX liquidity ${total_liquidity:,.0f}"] if liq_score >= 6 else [],
                "negative": ["Low 24h trading volume"] if total_volume_24h < 10000 else [],
            },
            "analysis_summary": f"Automated analysis based on {len(raw_data['_sources'])} data sources. Total liquidity: ${total_liquidity:,.0f}. {'Indicators are healthy' if risk == 'green' else 'Several indicators require attention' if risk == 'yellow' else 'High-risk conditions detected'}.",
        }
