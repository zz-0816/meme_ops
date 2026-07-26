"""
NFT 铸造服务模块

当前阶段：预留接口，实际链上交互由用户在前端通过 MetaMask 完成。
后端负责：
1. 构建 NFT 元数据 JSON
2. 记录铸造结果到数据库
3. 提供铸造状态查询

合约地址在部署后通过环境变量或配置文件注入。
"""

import json
import os
import base64
import hashlib
import html
from datetime import datetime
from typing import Optional

from config import load_project_env


load_project_env()

# NFT 合约配置（部署后更新）
NFT_CONTRACT_ADDRESS = os.getenv(
    "NFT_CONTRACT_ADDRESS",
    "0xa3362da22738D290C982fB9176A1FE6cD6F2ed04",
)
NFT_CHAIN = os.getenv("NFT_CHAIN", "monad-testnet")
NFT_CHAIN_ID = int(os.getenv("NFT_CHAIN_ID", "10143"))
NFT_EXPLORER_URL = os.getenv("NFT_EXPLORER_URL", "https://testnet.monadexplorer.com")


def _poster_palette(style: str) -> dict:
    value = (style or "Cyberpunk").lower()
    presets = {
        "minimal": ("#f7f8fc", "#111827", "#4f46e5", "#e5e7eb"),
        "vintage": ("#241b15", "#f4d6a0", "#d97745", "#493628"),
        "ocean": ("#061a2d", "#d7f9ff", "#11b5e4", "#0d3b66"),
        "nature": ("#071d14", "#e8fff4", "#26d07c", "#123d2b"),
        "luxury": ("#110f18", "#f9e6ad", "#d4af37", "#322c42"),
        "cyberpunk": ("#070716", "#f5f4ff", "#7c5cff", "#161633"),
    }
    for keyword, colors in presets.items():
        if keyword in value:
            return dict(zip(("bg", "text", "accent", "panel"), colors))
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    hue = int(digest[:2], 16) * 360 // 255
    return {
        "bg": f"hsl({hue} 45% 8%)",
        "text": f"hsl({hue} 30% 94%)",
        "accent": f"hsl({(hue + 55) % 360} 88% 62%)",
        "panel": f"hsl({hue} 36% 16%)",
    }


def _template_scene(style: str, accent: str) -> str:
    """Meaningful no-key preview scene; never repeats the user's prompt as text."""
    value = (style or "").lower()
    scenes = []
    if any(word in value for word in ("football", "soccer", "足球", "球场")):
        scenes.append(f"""
<ellipse cx="480" cy="360" rx="410" ry="250" fill="#0b612f" opacity=".82"/>
<path d="M70 360h820M480 110v500" stroke="#d9ffe8" stroke-width="7" opacity=".68"/>
<circle cx="480" cy="360" r="72" fill="none" stroke="#d9ffe8" stroke-width="7" opacity=".68"/>
<path d="M340 120L210 40M620 120L750 40" stroke="{accent}" stroke-width="18" opacity=".65"/>""")
    if any(word in value for word in ("japanese", "japan", "日式", "日本", "浮世绘", "anime")):
        scenes.append(f"""
<circle cx="720" cy="210" r="170" fill="#ffdfd8" opacity=".9"/>
<path d="M160 500h640M230 500V235h500v265M185 235h590L690 160H270Z"
 stroke="{accent}" stroke-width="22" fill="none" stroke-linejoin="round"/>
<path d="M0 560Q180 470 330 555T660 555T960 530" fill="none" stroke="#d8ecff" stroke-width="28" opacity=".46"/>""")
    if any(word in value for word in ("building", "city", "technology", "tech", "大楼", "科技", "cyber")):
        scenes.append(f"""
<path d="M80 610V220h190v390M300 610V100h250v510M580 610V260h300v350"
 fill="#08152b" stroke="{accent}" stroke-width="8"/>
<g stroke="{accent}" stroke-width="6" opacity=".75">
<path d="M125 280h95M125 350h95M125 420h95M350 180h150M350 260h150M350 340h150M635 330h190M635 410h190"/>
</g><path d="M0 565L960 170" stroke="{accent}" stroke-width="4" opacity=".3"/>""")
    if scenes:
        return "".join(scenes)
    return f"""
<path d="M80 470Q260 120 480 310T880 160" fill="none" stroke="{accent}" stroke-width="34" opacity=".58"/>
<circle cx="720" cy="220" r="180" fill="{accent}" opacity=".17"/>
<path d="M80 560L310 310L500 470L850 140" fill="none" stroke="#fff" stroke-width="8" opacity=".36"/>"""


def build_poster_image(
    analysis_report: dict,
    analysis_id: int,
    poster_style: str,
    background_image: str | None = None,
    content_plan: dict | None = None,
) -> tuple[str, str]:
    """Compose immutable facts over an AI background or a semantic template."""
    token = analysis_report.get("token", {})
    name = str(token.get("name") or "Unknown")
    symbol = str(token.get("symbol") or "").upper()
    chain = str(token.get("chain") or "unknown")
    score = float(analysis_report.get("overall_score") or 0)
    risk = str(analysis_report.get("risk_level") or "unknown")
    dims = list(analysis_report.get("dimensions") or [])[:5]
    style = (poster_style or "Cyberpunk").strip()[:120]
    uid_seed = json.dumps(analysis_report, sort_keys=True, ensure_ascii=False) + style
    poster_uid = f"MOP-{analysis_id:06d}-{hashlib.sha256(uid_seed.encode('utf-8')).hexdigest()[:8].upper()}"
    colors = _poster_palette(style)
    plan = content_plan or {}

    bars = []
    for index, dim in enumerate(dims):
        label = html.escape(str(dim.get("dimension") or "Metric")[:28])
        dim_score = max(0.0, min(10.0, float(dim.get("score") or 0)))
        y = 690 + index * 43
        bars.append(
            f'<text x="70" y="{y}" class="label">{label}</text>'
            f'<rect x="390" y="{y - 14}" width="390" height="14" rx="7" fill="#ffffff" opacity=".18"/>'
            f'<rect x="390" y="{y - 14}" width="{dim_score * 39:.0f}" height="14" rx="7" fill="{colors["accent"]}"/>'
            f'<text x="812" y="{y}" class="metric">{dim_score:.1f}</text>'
        )

    facts = {
        str(fact.get("id")): fact for fact in (analysis_report.get("poster_facts") or [])
    }
    selected_ids = [
        str(item) for item in (plan.get("selected_fact_ids") or [])
        if str(item) in facts
    ]
    fact_markup = ""
    if selected_ids:
        fact_items = [facts[item] for item in selected_ids[:6]]
        if plan.get("layout") == "sides":
            rendered = []
            for index, fact in enumerate(fact_items):
                column = index % 2
                row = index // 2
                x = 70 + column * 450
                y = 690 + row * 62
                rendered.append(
                    f'<text x="{x}" y="{y}" class="fact-label">{html.escape(str(fact.get("label") or ""))}</text>'
                    f'<text x="{x}" y="{y + 28}" class="fact-value">{html.escape(str(fact.get("formatted") or ""))}</text>'
                )
            fact_markup = "".join(rendered)
        else:
            fact_markup = "".join(
                f'<text x="70" y="{690 + index * 38}" class="fact-label">{html.escape(str(fact.get("label") or ""))}</text>'
                f'<text x="815" y="{690 + index * 38}" class="fact-value" text-anchor="end">{html.escape(str(fact.get("formatted") or ""))}</text>'
                for index, fact in enumerate(fact_items)
            )

    context_lines = []
    for source_line in (plan.get("context_lines") or [])[:2]:
        words = str(source_line).split()
        current = ""
        for word in words:
            if len(current) + len(word) + 1 > 92:
                context_lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()
        if current:
            context_lines.append(current)
    context_markup = "".join(
        f'<text x="70" y="{858 + index * 24}" class="context">{html.escape(line)}</text>'
        for index, line in enumerate(context_lines[:3])
    )
    narrative = analysis_report.get("poster_narrative") or {}
    headline = str(plan.get("headline") or narrative.get("headline") or name)[:64]
    subheadline = str(
        plan.get("subheadline") or narrative.get("subheadline") or
        f"{chain.upper()} verified trend report"
    )[:120]

    visual = (
        f'<image href="{html.escape(background_image, quote=True)}" width="960" height="960" '
        'preserveAspectRatio="xMidYMid slice"/>'
        if background_image
        else _template_scene(style, colors["accent"])
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="960" viewBox="0 0 960 960">
<rect width="960" height="960" fill="{colors['bg']}"/>
{visual}
<linearGradient id="shade" x1="0" y1="0" x2="0" y2="1"><stop offset="35%" stop-color="#02030b" stop-opacity=".06"/><stop offset="68%" stop-color="#02030b" stop-opacity=".88"/><stop offset="100%" stop-color="#02030b"/></linearGradient>
<rect width="960" height="960" fill="url(#shade)"/>
<style>
.title{{font:700 58px Arial,sans-serif;fill:#fff}}
.sub{{font:400 21px Arial,sans-serif;fill:#fff;opacity:.78}}
.label{{font:600 17px Arial,sans-serif;fill:#fff}}
.metric{{font:700 18px Arial,sans-serif;fill:{colors['accent']}}}
.fact-label{{font:600 16px Arial,sans-serif;fill:#fff;opacity:.7}}
.fact-value{{font:700 23px Arial,sans-serif;fill:{colors['accent']}}}
.context{{font:400 14px Arial,sans-serif;fill:#fff;opacity:.68}}
.id{{font:600 17px monospace;fill:{colors['accent']};letter-spacing:1px}}
</style>
<text x="70" y="82" class="id">{poster_uid}</text>
<text x="70" y="515" class="title">{html.escape(name[:24])}{f' ({html.escape(symbol[:10])})' if symbol else ''}</text>
<text x="70" y="558" class="sub">{html.escape(headline)}</text>
<text x="70" y="598" class="sub" opacity=".62">{html.escape(subheadline)}</text>
<rect x="70" y="635" width="820" height="1" fill="#fff" opacity=".25"/>
<text x="70" y="670" class="sub">SCORE <tspan font-size="30" font-weight="700" fill="{colors['accent']}">{score:.1f}</tspan>/10</text>
<text x="690" y="668" class="sub">RISK {html.escape(risk.upper())}</text>
{fact_markup or ''.join(bars)}
{context_markup}
<line x1="70" y1="914" x2="890" y2="914" stroke="#fff" stroke-width="1" opacity=".2"/>
<text x="70" y="944" class="id">MEME OPS · PERSONAL POSTER NFT · IMMUTABLE METRICS</text>
</svg>"""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}", poster_uid


def build_metadata(
    analysis_report: dict,
    analysis_id: int,
    poster_style: str = "Cyberpunk",
    background_image: str | None = None,
    image_provider: str = "template",
    image_model: str = "deterministic-template",
    content_plan: dict | None = None,
) -> dict:
    """
    从分析报告中提取关键信息，构建 NFT 元数据 JSON。
    这是铸造时传入合约的 tokenURI 内容。
    """
    token = analysis_report.get("token", {})
    image, poster_uid = build_poster_image(
        analysis_report, analysis_id, poster_style, background_image, content_plan,
    )
    return {
        "name": f"Meme Ops Poster: {token.get('name', 'Unknown')} · {poster_uid}",
        "description": f"Overall score {analysis_report.get('overall_score', 'N/A')}/10 | "
                       f"Risk level {analysis_report.get('risk_level', 'N/A')} | "
                       f"Persona {analysis_report.get('persona', 'N/A')} | "
                       f"Visual style {poster_style or 'Cyberpunk'}",
        "attributes": [
            {"trait_type": "Poster ID", "value": poster_uid},
            {"trait_type": "Token Name", "value": token.get("name", "Unknown")},
            {"trait_type": "Chain", "value": token.get("chain", "unknown")},
            {"trait_type": "Overall Score", "value": analysis_report.get("overall_score", 0)},
            {"trait_type": "Risk Level", "value": analysis_report.get("risk_level", "N/A")},
            {"trait_type": "Persona", "value": analysis_report.get("persona", "N/A")},
            {"trait_type": "Visual Style", "value": poster_style or "Cyberpunk"},
            {"trait_type": "Image Provider", "value": image_provider},
            {"trait_type": "Analyzed At", "value": analysis_report.get("analyzed_at", "")},
            {"trait_type": "Analysis ID", "value": str(analysis_id)},
        ],
        "image": image,
        "poster_id": poster_uid,
        "image_provider": image_provider,
        "image_model": image_model,
        "poster_plan": content_plan or {},
        "created_at": datetime.now().isoformat(),
    }


def get_mint_contract_info() -> dict:
    """返回前端铸造所需的合约信息"""
    return {
        "contract_address": NFT_CONTRACT_ADDRESS,
        "chain": NFT_CHAIN,
        "chain_id": NFT_CHAIN_ID,
        "explorer_url": NFT_EXPLORER_URL,
        "abi": [
            {
                "type": "function",
                "name": "mint",
                "inputs": [{"name": "tokenURI_", "type": "string"}],
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "nonpayable",
            },
        ],
    }
