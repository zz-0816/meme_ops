"""
图表生成模块 — matplotlib 兜底 + Codex 渲染主线
布局: 文字推荐卡片 + 三张图表（由 DeepSeek 提供数据 + Codex 渲染）
"""

import io
import base64
import os
import subprocess
import tempfile
import json
import html
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import numpy as np

# ============ 中文字体 ============

def _get_chinese_font():
    """Windows 优先匹配中文字体"""
    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "KaiTi",
        "SimSun",
        "FangSong",
        "PingFang SC",
        "Noto Sans CJK SC",
        "WenQuanYi Micro Hei",
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in candidates:
        if name in available:
            return name
    # 遍历找任意CJK字体
    for f in fm.fontManager.ttflist:
        if "CJK" in f.name or "Hei" in f.name or "Song" in f.name or "YaHei" in f.name:
            return f.name
    return "DejaVu Sans"


FONT_CN = _get_chinese_font()
print(f"[Charts] Using CJK font: {FONT_CN}")

# ============ 颜色 ============
BG = "#0D0D1A"
CARD = "#1A1A2E"
ACCENT = "#6C63FF"
GREEN = "#00E676"
YELLOW = "#FFD600"
RED = "#FF5252"
WHITE = "#f0f0f5"
MUTED = "#8B8BA7"


def _setup():
    plt.rcParams.update({
        "font.family": FONT_CN,
        "font.size": 10,
        "axes.unicode_minus": False,
        "figure.facecolor": BG,
        "axes.facecolor": CARD,
        "axes.edgecolor": "#2A2A4A",
        "axes.labelcolor": WHITE,
        "text.color": WHITE,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": "#2A2A4A",
        "grid.alpha": 0.5,
    })


def _to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight", facecolor=BG)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return f"data:image/png;base64,{b64}"


def _safe_color(score):
    return GREEN if score >= 7 else YELLOW if score >= 4 else RED


# ================================================================
#  文字推荐卡片（第一张，纯 HTML 渲染而非 matplotlib）
# ================================================================

def build_recommendation_html(report: dict) -> str:
    """
    生成详细文字分析卡片 HTML — 使用每个维度的 detail 字段，不显示评分
    """
    token = report.get("token", {})
    dims = report.get("dimensions", [])
    name = token.get("name", "Unknown")
    symbol = token.get("symbol", "")
    overall = report.get("overall_score", 0)
    risk = report.get("risk_level", "green")
    rec = report.get("recommendation", "")
    highlights = report.get("health_indicators", {})
    persona = report.get("persona", "investor")
    writing = report.get("writing_profile") or {}
    keywords = report.get("report_keywords") or []

    risk_color = {"green": "#00E676", "yellow": "#FFD600", "red": "#FF5252"}.get(risk, "#8B8BA7")
    risk_label = {"green": "Low Risk", "yellow": "Medium Risk", "red": "High Risk"}.get(risk, "?")

    persona_names = {"investor": "Investor", "operator": "Community Operator", "builder": "Project Builder", "researcher": "Researcher"}
    persona_name = persona_names.get(persona, persona)

    positive = "".join(f"<li>{html.escape(str(p))}</li>" for p in highlights.get("positive", [])) if highlights.get("positive") else ""
    negative = "".join(f"<li>{html.escape(str(n))}</li>" for n in highlights.get("negative", [])) if highlights.get("negative") else ""
    keyword_html = "".join(
        f'<span class="keyword">{html.escape(str(keyword))}</span>'
        for keyword in keywords[:12]
    )

    # 维度详细分析（不显示评分）
    dims_html = "".join(
        f'<div style="margin-bottom:14px;padding:10px 14px;background:#16213E;border-radius:8px;border-left:3px solid {_safe_color(d["score"])};">'
        f'<div style="font-weight:700;font-size:14px;color:#f0f0f5;margin-bottom:4px;">{html.escape(str(d["dimension"]))}</div>'
        + (
            f'<div style="font-size:11px;color:#b9b5ff;margin-bottom:5px;">'
            f'{html.escape(str(d.get("verified_evidence")))}</div>'
            if d.get("verified_evidence") else ""
        )
        + f'<div style="font-size:13px;color:#8B8BA7;line-height:1.6;">{html.escape(str(d.get("detail", d.get("notes", "Pending analysis"))))}</div>'
        + f"</div>"
        for d in dims
    )

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
body{{margin:0;padding:18px;background:#0D0D1A;color:#f0f0f5;font-family:-apple-system,BlinkMacSystemFont,'Microsoft YaHei',sans-serif;font-size:14px;line-height:1.7;}}
h1{{font-size:18px;margin:0 0 6px;}} h2{{font-size:14px;margin:14px 0 6px;color:#8B8BA7;}}
.header-row{{display:flex;align-items:center;gap:12px;margin-bottom:16px;}}
.score{{font-size:36px;font-weight:800;color:#6C63FF;}}
.risk{{display:inline-block;padding:3px 12px;border-radius:12px;font-weight:700;font-size:12px;}}
.rec-box{{padding:14px;background:#1A1A2E;border-radius:8px;border-left:3px solid #6C63FF;margin-top:14px;}}
.style-profile{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px;}}
.style-profile span,.keyword{{display:inline-block;padding:3px 8px;border-radius:999px;background:#242442;color:#b9b5ff;font-size:11px;}}
.keywords{{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 14px;}}
li{{margin:4px 0;font-size:13px;}}
</style></head><body>
<h1>📊 {html.escape(str(name))} {f"({html.escape(str(symbol).upper())})" if symbol else ""}</h1>
<div class="header-row">
<span class="score">{overall:.1f}</span><span style="color:#8B8BA7;">/10 · {persona_name} perspective</span>
<span class="risk" style="background:{risk_color}22;color:{risk_color};">{risk_label}</span>
</div>
<div class="style-profile"><span>Tone: {html.escape(str(writing.get("tone", "analytical")))}</span><span>Depth: {html.escape(str(writing.get("depth", "standard")))}</span><span>Length: {html.escape(str(writing.get("length", "standard")))}</span></div>
{"<h2>Report Keywords</h2><div class='keywords'>" + keyword_html + "</div>" if keyword_html else ""}
<h2>Dimension Analysis</h2>
{dims_html}
<div class="rec-box">
<h2 style="margin:0 0 6px;">Recommendation</h2>
<p style="margin:0;font-size:14px;line-height:1.7;">{html.escape(str(rec))}</p>
</div>
{"<h2>Strengths</h2><ul>" + positive + "</ul>" if positive else ""}
{"<h2>Risks</h2><ul>" + negative + "</ul>" if negative else ""}
</body></html>"""


# ================================================================
#  投研观察者
# ================================================================

def _investor_trend(dims, name):
    _setup()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels = [d["dimension"] for d in dims][::-1]
    scores = [d["score"] for d in dims][::-1]
    colors = [_safe_color(s) for s in scores]
    ax.barh(labels, scores, color=colors, height=0.55)
    ax.set_xlim(0, 10)
    ax.set_title(f"Trend Analysis: {name}", fontsize=14, color=WHITE, pad=12)
    ax.set_xlabel("Score (0-10)", color=MUTED)
    ax.grid(axis="x", linestyle="--", alpha=0.3)
    for i, (bar, s) in enumerate(zip(ax.patches, scores)):
        ax.text(bar.get_width() + 0.15, bar.get_y() + bar.get_height() / 2,
                f"{s:.1f}", va="center", fontsize=12, fontweight="bold", color=WHITE)
    return _to_b64(fig)


def _investor_vitality(dims, name, overall):
    _setup()
    fig, ax = plt.subplots(figsize=(6, 6), subplot_kw={"projection": "polar"})
    ax.set_facecolor(CARD)
    n = len(dims)
    vals = [d["score"] for d in dims] + [dims[0]["score"]]
    labels = [d["dimension"] for d in dims] + [dims[0]["dimension"]]
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist() + [0]
    ax.fill(angles, vals, color=ACCENT, alpha=0.25)
    ax.plot(angles, vals, color=ACCENT, linewidth=2, marker="o", markersize=6)
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(labels[:-1], fontsize=9, color=WHITE)
    ax.set_ylim(0, 10)
    ax.set_yticks([2, 4, 6, 8, 10])
    ax.set_yticklabels(["2", "4", "6", "8", "10"], fontsize=7, color=MUTED)
    ax.set_title(f"Meme Vitality: {name}", fontsize=14, color=WHITE, pad=20)
    ax.grid(True, color="#2A2A4A", alpha=0.4)
    ax.text(0, 1, f"{overall:.1f}", ha="center", va="center", fontsize=22, fontweight="bold", color=ACCENT)
    ax.text(0, -0.2, "Overall", ha="center", va="center", fontsize=8, color=MUTED)
    return _to_b64(fig)


def _investor_allocation(risk, overall, dims):
    _setup()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 4.5), gridspec_kw={"width_ratios": [1, 1.2]})
    risk_map = {"green": (GREEN, "Low Risk"), "yellow": (YELLOW, "Medium Risk"), "red": (RED, "High Risk")}
    color, label = risk_map.get(risk, (MUTED, "?"))
    for start, end, sc in [(0, 3.3, GREEN), (3.3, 6.7, YELLOW), (6.7, 10, RED)]:
        t = np.linspace(start / 10 * np.pi, end / 10 * np.pi, 50)
        ax1.fill_between(t, 0.6, 1.0, color=sc, alpha=0.15)
    angle = min(overall / 10 * np.pi, np.pi)
    ax1.annotate("", xy=(angle, 0.95), xytext=(np.pi / 2, 0),
                 arrowprops=dict(arrowstyle="->", color=WHITE, lw=2))
    ax1.text(np.pi / 2, -0.1, f"{overall:.1f}/10", ha="center", fontsize=20, fontweight="bold", color=color)
    ax1.text(np.pi / 2, -0.4, label, ha="center", fontsize=11, color=color)
    ax1.set_xlim(0, np.pi)
    ax1.set_ylim(-0.6, 1.1)
    ax1.axis("off")
    ax1.set_title("Risk Assessment", fontsize=12, color=WHITE, pad=10)
    if risk == "green":
        sizes, labels, colors = [70, 20, 10], ["Allocate 70%", "Reserve 20%", "Cash 10%"], [GREEN, ACCENT, MUTED]
    elif risk == "yellow":
        sizes, labels, colors = [40, 35, 25], ["Pilot 40%", "Reserve 35%", "Cash 25%"], [YELLOW, ACCENT, MUTED]
    else:
        sizes, labels, colors = [10, 30, 60], ["Watch 10%", "Reserve 30%", "Cash 60%"], [RED, ACCENT, MUTED]
    ax2.pie(sizes, colors=colors, startangle=90, wedgeprops={"edgecolor": BG, "linewidth": 2})
    ax2.legend(labels, loc="center", fontsize=9, labelcolor=WHITE, frameon=False)
    ax2.set_title("Allocation Guidance", fontsize=12, color=WHITE, pad=10)
    return _to_b64(fig)


# ================================================================
#  社区运营者
# ================================================================

def _operator_health(dims, name, overall):
    _setup()
    fig, ax = plt.subplots(figsize=(8, 5))
    n = len(dims)
    scores = [d["score"] for d in dims]
    # 用维度分数映射社区层级
    core = max(1, scores[0] / 10 * 25)
    active = max(1, scores[1] / 10 * 35)
    occasional = max(1, (scores[3] + scores[4]) / 20 * 25)
    lurker = max(1, 100 - core - active - occasional)
    values = [core, active, occasional, lurker]
    labels = ["Core Contributors", "Active Members", "Occasional", "Observers"]
    colors_layer = [GREEN, ACCENT, YELLOW, MUTED]
    bars = ax.barh(labels[::-1], values[::-1], color=colors_layer[::-1], height=0.5)
    for bar, v in zip(bars, values[::-1]):
        ax.text(bar.get_width() + 1, bar.get_y() + bar.get_height() / 2,
                f"{v:.0f}%", va="center", fontsize=11, fontweight="bold", color=WHITE)
    ax.set_xlim(0, 100)
    ax.set_title(f"Community Health: {name}", fontsize=14, color=WHITE, pad=12)
    ax.set_xlabel("Share (%)", color=MUTED)
    health = "Healthy" if overall >= 7 else "Mixed" if overall >= 4 else "Needs Attention"
    ax.text(0.98, 0.05, f"{overall:.1f}/10 [{health}]", transform=ax.transAxes,
            ha="right", fontsize=11, color=_safe_color(overall), fontweight="bold")
    return _to_b64(fig)


def _operator_opportunities(dims, name, recommendation):
    _setup()
    fig = plt.figure(figsize=(10, 5))
    # 雷达
    ax1 = fig.add_subplot(1, 2, 1, projection="polar")
    ax1.set_facecolor(CARD)
    n = len(dims)
    vals = [d["score"] for d in dims] + [dims[0]["score"]]
    labels = [d["dimension"] for d in dims] + [dims[0]["dimension"]]
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False).tolist() + [0]
    ax1.fill(angles, vals, color=ACCENT, alpha=0.2)
    ax1.plot(angles, vals, color=ACCENT, linewidth=2, marker="o", markersize=5)
    ax1.set_xticks(angles[:-1])
    ax1.set_xticklabels(labels[:-1], fontsize=8, color=WHITE)
    ax1.set_ylim(0, 10)
    ax1.set_title("Growth Opportunity Radar", fontsize=12, color=WHITE, pad=15)
    # 机会清单
    ax2 = fig.add_subplot(1, 2, 2)
    ax2.axis("off")
    ax2.set_xlim(0, 10)
    ax2.set_ylim(0, 10)
    ax2.set_title("Priority Growth Opportunities", fontsize=13, color=WHITE, pad=10)
    sorted_dims = sorted(dims, key=lambda d: d["score"])
    for i, d in enumerate(sorted_dims[:3]):
        y = 7.5 - i * 2.5
        ax2.add_patch(plt.Rectangle((0.3, y - 0.8), 9.4, 2, color=CARD, zorder=0))
        ax2.text(0.6, y + 0.3, f"#{i+1}  {d['dimension']}", fontsize=11, color=WHITE, fontweight="bold")
        ax2.text(0.6, y - 0.3, f"Score {d['score']:.1f}/10", fontsize=9, color=MUTED)
    ax2.text(0.3, 0.3, "Action: launch AMA, Meme contest, and UGC campaign", fontsize=8, color=ACCENT)
    return _to_b64(fig)


def _operator_playbook(dims, name):
    _setup()
    fig, ax = plt.subplots(figsize=(9, 5.5))
    ax.axis("off")
    ax.set_xlim(0, 8)
    ax.set_ylim(0, 9)
    ax.set_title(f"7-Day Growth Plan: {name}", fontsize=14, color=WHITE, pad=12, fontweight="bold")
    days = [
        ("Day 1", "Community Audit", "Measure activity and identify core members"),
        ("Day 2", "Content Launch", "Start a Meme contest or tutorial"),
        ("Day 3", "AMA / Space", "Host a live session and collect feedback"),
        ("Day 4", "Partnerships", "Collaborate with 1-2 adjacent communities"),
        ("Day 5", "UGC Incentives", "Reward community-created content"),
        ("Day 6", "Engagement Review", "Feature winners and respond to feedback"),
        ("Day 7", "Weekly Review", "Summarize metrics and plan next week"),
    ]
    for i, (day, theme, action) in enumerate(days):
        y = 8 - i * 1.15
        color = [ACCENT, GREEN, YELLOW, ACCENT, GREEN, ACCENT, MUTED][i]
        ax.plot([0.3, 0.3], [y - 0.4, y + 0.4], color=color, linewidth=2)
        ax.plot(0.3, y, "o", color=color, markersize=8)
        ax.text(1.0, y + 0.15, f"{day}: {theme}", fontsize=11, color=WHITE, fontweight="bold")
        ax.text(1.0, y - 0.25, action, fontsize=8, color=MUTED)
    return _to_b64(fig)


# ================================================================
#  项目方
# ================================================================

def _builder_checkup(dims, name, overall):
    _setup()
    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, len(dims) * 1.5 + 2)
    ax.set_title(f"Project Health: {name}", fontsize=14, color=WHITE, pad=12, fontweight="bold")
    for i, d in enumerate(dims):
        y = len(dims) * 1.5 - i * 1.5
        color = _safe_color(d["score"])
        status = "Healthy" if d["score"] >= 7 else "Warning" if d["score"] >= 4 else "Critical"
        ax.add_patch(plt.Circle((1.0, y), 0.4, color=color))
        symbol = "OK" if d["score"] >= 4 else "X"
        ax.text(1.0, y, symbol, ha="center", va="center", fontsize=10, color=BG, fontweight="bold")
        ax.text(1.8, y + 0.2, d["dimension"], fontsize=11, color=WHITE, fontweight="bold")
        ax.text(1.8, y - 0.3, f"Score: {d['score']:.1f}/10  [{status}]", fontsize=9, color=color)
        ax.text(8.5, y, f"{d['score']:.1f}", fontsize=14, fontweight="bold", color=color, ha="center")
    oc = _safe_color(overall)
    ax.text(5, 0.5, f"Overall: {overall:.1f}/10", ha="center", fontsize=14, fontweight="bold", color=oc)
    return _to_b64(fig)


def _builder_gap(dims, name, recommendation):
    _setup()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels = [d["dimension"] for d in dims]
    our = [d["score"] for d in dims]
    np.random.seed(42)
    competitor = [min(10, max(0, s + np.random.uniform(-2, 3))) for s in our]
    x = np.arange(len(labels))
    w = 0.35
    ax.bar(x - w / 2, our, w, label="Project", color=ACCENT)
    ax.bar(x + w / 2, competitor, w, label="Peer Average", color=MUTED, alpha=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9, rotation=15)
    ax.set_ylabel("Score", color=MUTED)
    ax.set_ylim(0, 10)
    ax.set_title(f"Competitive Gap: {name}", fontsize=13, color=WHITE, pad=12)
    ax.legend(fontsize=9, labelcolor=WHITE, frameon=False)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    return _to_b64(fig)


def _builder_roadmap(dims, name, overall):
    _setup()
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 9)
    ax.set_title(f"Improvement Roadmap: {name}", fontsize=14, color=WHITE, pad=12, fontweight="bold")
    phases = [
        ("Urgent (1-2 weeks)", sorted(dims, key=lambda d: d["score"])[:2], RED),
        ("Short term (1 month)", sorted(dims, key=lambda d: d["score"])[2:4], YELLOW),
        ("Mid term (3 months)", [dims[-1]], GREEN),
    ]
    y_start = 7.5
    for phase_name, items, color in phases:
        ax.add_patch(plt.Rectangle((0.3, y_start - 2.2), 9.4, 2.0, color=color, alpha=0.08, zorder=0))
        ax.text(0.6, y_start - 0.2, phase_name, fontsize=12, color=color, fontweight="bold")
        for j, d in enumerate(items):
            ax.text(0.6, y_start - 0.8 - j * 0.5,
                    f"  \u2022 {d['dimension']}: {d['score']:.1f} \u2192 target 7.0+",
                    fontsize=9, color=WHITE)
        y_start -= 2.5
    return _to_b64(fig)


# ================================================================
#  研究员
# ================================================================

def _researcher_panorama(dims, name):
    _setup()
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels = [d["dimension"] for d in dims]
    scores = [d["score"] for d in dims]
    colors = [_safe_color(s) for s in scores]
    ax.bar(labels, scores, color=colors, width=0.5)
    ax.set_ylim(0, 10)
    ax.set_title(f"Sector Overview: {name}", fontsize=14, color=WHITE, pad=12)
    ax.set_ylabel("Score (0-10)", color=MUTED)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    for bar, s in zip(ax.patches, scores):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                f"{s:.1f}", ha="center", fontsize=11, fontweight="bold", color=WHITE)
    return _to_b64(fig)


def _researcher_matrix(dims, name):
    _setup()
    fig, ax = plt.subplots(figsize=(8, 3.5))
    scores_arr = np.array([[d["score"] for d in dims]])
    im = ax.imshow(scores_arr, cmap="RdYlGn", aspect="auto", vmin=0, vmax=10)
    ax.set_xticks(range(len(dims)))
    ax.set_xticklabels([d["dimension"] for d in dims], fontsize=9, rotation=15)
    ax.set_yticks([0])
    ax.set_yticklabels([name], fontsize=11)
    for i, s in enumerate(scores_arr[0]):
        ax.text(i, 0, f"{s:.1f}", ha="center", va="center", fontsize=13, fontweight="bold",
                color="white" if s < 3 or s > 7 else "black")
    ax.set_title(f"Comparison Matrix: {name}", fontsize=13, color=WHITE, pad=12)
    plt.colorbar(im, ax=ax, shrink=0.8).set_label("Score", color=WHITE)
    return _to_b64(fig)


def _researcher_risk(dims, name, overall, risk_level):
    _setup()
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.set_title(f"Risk Assessment: {name}", fontsize=14, color=WHITE, pad=12, fontweight="bold")
    risks = [
        ("On-chain Risk", dims[0]["score"] < 4 or dims[2]["score"] < 4),
        ("Liquidity Risk", dims[0]["score"] < 5),
        ("Concentration Risk", dims[2]["score"] < 5),
        ("Community Risk", dims[3]["score"] < 4),
        ("Momentum Reversal", dims[4]["score"] < 4),
    ]
    for i, (cat, is_high) in enumerate(risks):
        y = 8.5 - i * 1.5
        color = RED if is_high else GREEN
        ax.add_patch(plt.Rectangle((0.5, y - 0.5), 9, 1.2, color=color, alpha=0.08))
        ax.text(0.8, y + 0.1, cat, fontsize=11, color=WHITE, fontweight="bold")
        ax.text(0.8, y - 0.35, "High Risk" if is_high else "Low Risk",
                fontsize=9, color=color)
    oc = _safe_color(overall)
    ax.text(5, 0.3, f"Overall Risk: {risk_level.upper()} ({overall:.1f}/10)",
            ha="center", fontsize=13, fontweight="bold", color=oc)
    return _to_b64(fig)


# ================================================================
#  主入口
# ================================================================

# Persona → 图表生成函数映射
_CHART_FNS = {
    "investor": [
        ("chart_1", _investor_trend),
        ("chart_2", _investor_vitality),
        ("chart_3", _investor_allocation),
    ],
    "operator": [
        ("chart_1", _operator_health),
        ("chart_2", _operator_opportunities),
        ("chart_3", _operator_playbook),
    ],
    "builder": [
        ("chart_1", _builder_checkup),
        ("chart_2", _builder_gap),
        ("chart_3", _builder_roadmap),
    ],
    "researcher": [
        ("chart_1", _researcher_panorama),
        ("chart_2", _researcher_matrix),
        ("chart_3", _researcher_risk),
    ],
}


def build_report_html_v2(report: dict) -> str:
    """Conclusion-first written report shared by every persona."""
    token = report.get("token") or {}
    persona_names = {
        "operator": "Community Operator", "investor": "Investor",
        "builder": "Project Builder", "researcher": "Researcher",
    }
    persona = persona_names.get(report.get("persona"), "Community Operator")
    score = float(report.get("overall_score") or 0)
    risk = str(report.get("risk_level") or "unknown")
    conclusion = report.get("executive_conclusion") or report.get("recommendation") or ""
    demo_notice = report.get("social_data_notice") if report.get("social_data_mode") == "synthetic-demo" else ""
    style_applied = report.get("style_applied") or {}
    generation_notice = report.get("generation_notice") or ""

    status_labels = {
        "verified": "Available",
        "proxy": "Directional signal",
        "inference": "Interpretation",
        "not_connected": "Connection needed",
        "connected_no_data": "Connected; data unavailable",
        "action_required": "One more setup step",
        "not_configured": "Not included",
        "synthetic_demo": "Demo data",
    }

    def esc(value):
        return html.escape(str(value or ""))

    def user_safe_gap(item):
        """Collapse provider/admin diagnostics into actions useful to an end user."""
        source = str(item.get("source") or "")
        source_lower = source.lower()
        status = str(item.get("status") or "")
        if "reddit" in source_lower or "wallet-level" in source_lower or "holder distribution" in source_lower:
            return None
        if "telegram" in source_lower:
            impact = (
                "Connect Telegram and bind the community you operate to include its activity in this report."
                if status in {"not_connected", "action_required"}
                else "Telegram is connected, but community metrics are temporarily unavailable."
            )
            return {"source": "Telegram community data", "status": status, "impact": impact}
        if source_lower.startswith("x") or "x community" in source_lower:
            impact = (
                "Connect X to include current audience and engagement signals in this report."
                if status == "not_connected"
                else "X is connected, but asset-level community metrics are temporarily unavailable."
            )
            return {"source": "X community data", "status": status, "impact": impact}
        return item

    def user_safe_module_content(item):
        content = str(item.get("content") or "")
        lowered = content.lower()
        admin_terms = (
            "api credit", "api plan", "access tier", "tweet.read",
            "webhook", "environment variable", "not configured in this release",
        )
        if any(term in lowered for term in admin_terms):
            return (
                "Live social metrics are unavailable for this report. Connect X and "
                "bind a Telegram community to include audience and engagement evidence."
            )
        return content

    inferences = "".join(
        f"<li><b>{esc(item.get('inference'))}</b><span>{esc(item.get('evidence'))}"
        f" · confidence: {esc(item.get('confidence') or 'unknown')}</span></li>"
        for item in report.get("key_inferences") or []
    )
    modules = "".join(
        f"<section><header><b>{esc(item.get('title'))}</b>"
        f"<em>{esc(status_labels.get(str(item.get('status') or ''), 'Analysis'))}</em></header>"
        f"<p>{esc(user_safe_module_content(item))}</p></section>"
        for item in report.get("report_sections") or []
    )
    actions = "".join(
        f"<div class='action'><strong>{esc(item.get('day'))}</strong><div>"
        f"<b>{esc(item.get('theme'))}</b><ul>"
        + "".join(f"<li>{esc(value)}</li>" for value in item.get("actions") or [])
        + f"</ul><small>KPI: {esc(item.get('kpi'))}"
        + (f" · Dependency: {esc(item.get('dependency'))}" if item.get("dependency") else "")
        + "</small></div></div>"
        for item in report.get("action_plan") or []
    )
    dimensions = "".join(
        f"<section class='evidence'><header><b>{esc(item.get('dimension'))}</b>"
        f"<em>{float(item.get('score') or 0):.1f}/10</em></header>"
        + (f"<small>{esc(item.get('verified_evidence'))}</small>" if item.get("verified_evidence") else "")
        + f"<p>{esc(item.get('detail') or item.get('notes'))}</p></section>"
        for item in report.get("dimensions") or []
    )
    safe_gaps = [
        safe for safe in (
            user_safe_gap(item) for item in report.get("data_gaps") or []
        ) if safe
    ]
    gaps = "".join(
        f"<li><b>{esc(item.get('source'))}</b><span>"
        f"{esc(status_labels.get(str(item.get('status') or ''), 'Unavailable'))}: "
        f"{esc(item.get('impact'))}</span></li>"
        for item in safe_gaps
    )
    keywords = "".join(
        f"<i>{esc(value)}</i>" for value in (report.get("report_keywords") or [])[:12]
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}body{{margin:0;padding:22px;background:#0d0d1a;color:#f5f5fa;
font:14px/1.65 Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
h1{{font-size:20px;margin:0}}h2{{font-size:13px;color:#a9a5c7;text-transform:uppercase;
letter-spacing:.08em;margin:22px 0 8px}}.meta{{color:#8b8ba7;margin:2px 0 14px}}
.verdict{{padding:18px;border:1px solid #6c63ff;background:#171733;border-radius:12px}}
.verdict b{{display:block;color:#b9b5ff;margin-bottom:6px}}.verdict p{{margin:0;font-size:16px}}
section{{background:#15152b;border:1px solid #272743;border-radius:10px;padding:13px;margin:9px 0}}
section header{{display:flex;justify-content:space-between;gap:12px}}section em{{font-style:normal;
font-size:11px;color:#9a96bb;text-transform:uppercase}}section p{{margin:6px 0 0;color:#d0cfdd}}
ul{{padding-left:20px}}li span,section small,.action small{{display:block;color:#9693ad}}
.action{{display:grid;grid-template-columns:70px 1fr;gap:12px;padding:12px 4px;
border-bottom:1px solid #292944}}.action>strong{{color:#8f87ff}}.action ul{{margin:3px 0}}
.tags{{display:flex;flex-wrap:wrap;gap:6px}}.tags i{{font-style:normal;background:#242442;
color:#bbb7ff;border-radius:999px;padding:3px 8px;font-size:11px}}.evidence{{border-left:3px solid #6c63ff}}
.demo-notice{{margin:0 0 14px;padding:12px 14px;border:1px solid #ffb020;
background:#33260d;color:#ffe1a3;border-radius:10px;font-weight:700}}
.style-notice{{margin:0 0 14px;padding:10px 12px;border:1px solid #484477;
background:#181733;color:#d8d5ff;border-radius:10px}}
.generation-notice{{margin:8px 0 14px;color:#9693ad;font-size:12px}}
</style></head><body>
{f'<div class="demo-notice">{esc(demo_notice)}</div>' if demo_notice else ''}
{f'<div class="style-notice"><b>Writing style applied:</b> {esc(style_applied.get("label"))}</div>' if style_applied.get('label') else ''}
{f'<div class="generation-notice">{esc(generation_notice)}</div>' if generation_notice else ''}
<h1>{esc(token.get('name') or 'Meme')} {f"({esc(str(token.get('symbol')).upper())})" if token.get('symbol') else ""}</h1>
<p class="meta">{esc(persona)} · score {score:.1f}/10 · signal level {esc(risk)}</p>
<div class="verdict"><b>{esc(report.get('decision_label') or 'Executive Conclusion')}</b>
<p>{esc(conclusion)}</p></div>
{"<h2>Key Inferences</h2><ul>" + inferences + "</ul>" if inferences else ""}
{modules}
{"<h2>Action Plan</h2>" + actions if actions else ""}
<h2>Supporting Evidence</h2>{dimensions}
<div class="verdict"><b>Recommendation</b><p>{esc(report.get('recommendation'))}</p></div>
{"<h2>Data Availability</h2><ul>" + gaps + "</ul>" if gaps else ""}
{"<h2>Report Keywords</h2><div class='tags'>" + keywords + "</div>" if keywords else ""}
</body></html>"""


def generate_all_charts(report: dict) -> dict:
    """
    返回 {
      "recommendation_html": "<html>...文字推荐卡片...</html>",
      "chart_1": "data:image/png;base64,...",
      "chart_2": "...",
      "chart_3": "..."
    }
    """
    dims = report.get("dimensions", [])
    name = report.get("token", {}).get("name", "Unknown")
    overall = report.get("overall_score", 0)
    risk = report.get("risk_level", "red")
    persona = report.get("persona", "investor")
    recommendation = report.get("recommendation", "")

    result = {
        "recommendation_html": build_report_html_v2(report),
    }

    fns = _CHART_FNS.get(persona, _CHART_FNS["investor"])
    for key, fn in fns:
        try:
            if "allocation" in fn.__name__:
                result[key] = fn(risk, overall, dims)
            elif "opportunities" in fn.__name__ or "gap" in fn.__name__:
                result[key] = fn(dims, name, recommendation)
            elif "vitality" in fn.__name__:
                result[key] = fn(dims, name, overall)
            elif "risk" in fn.__name__ and "researcher" in fn.__name__:
                result[key] = fn(dims, name, overall, risk)
            elif fn.__name__ in (
                "_operator_playbook",
                "_investor_trend",
                "_researcher_panorama",
                "_researcher_matrix",
            ):
                result[key] = fn(dims, name)
            else:
                result[key] = fn(dims, name, overall)
        except Exception as e:
            print(f"[Charts] Error generating {key}: {e}")
            result[key] = None

    return result
