"""Dependency-free parsing for conversational analysis requests."""

import re


CHAIN_ALIASES = {
    "solana": "solana", "sol": "solana",
    "ethereum": "ethereum", "eth": "ethereum",
    "bsc": "bsc", "binance smart chain": "bsc", "binance": "bsc",
    "base": "base", "ton": "ton", "monad": "monad",
}


def infer_writing_profile(text: str) -> dict:
    """Translate natural-language style directions into a strict writing contract."""
    lowered = str(text or "").strip().lower()
    academic = any(word in lowered for word in (
        "academic", "research paper", "professional terminology", "methodology",
        "学术", "论文", "专业术语", "研究报告", "专业化",
    ))
    friendly = any(word in lowered for word in (
        "friendly", "approachable", "beginner", "casual", "plain language",
        "亲近", "友好", "通俗", "易懂", "容易理解", "口语化",
    ))
    concise = any(word in lowered for word in (
        "concise", "brief", "short", "clear", "key points", "plain language",
        "简洁", "精简", "简短", "清晰", "重点", "易懂", "容易理解",
    ))
    detailed = academic or any(word in lowered for word in (
        "detailed", "in-depth", "comprehensive", "specific", "actionable",
        "详细", "深入", "完整", "具体", "可执行",
    ))
    plain = friendly or any(word in lowered for word in (
        "simple", "clear", "plain", "no jargon", "清晰", "简单", "直白",
        "不要术语", "少术语", "容易理解", "易懂",
    ))
    return {
        "tone": "academic" if academic else "friendly" if friendly else "analytical",
        "depth": (
            "academic" if academic else "detailed" if detailed
            else "concise" if concise else "standard"
        ),
        "length": "extended" if detailed else "compact" if concise else "standard",
        "clarity": "technical" if academic else "plain" if plain else "professional",
    }


def extract_analysis_intent(prompt: str) -> dict:
    text = prompt.strip()
    lowered = text.lower()
    chain = None
    for alias, chain_id in sorted(CHAIN_ALIASES.items(), key=lambda item: -len(item[0])):
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?:\s*链)?(?![a-z0-9])", lowered):
            chain = chain_id
            break

    address = re.search(r"0x[a-fA-F0-9]{40}", text)
    token_query = address.group(0) if address else ""
    if not token_query:
        patterns = [
            r"(?:分析(?:的是|一下|一个)?|币种(?:是|为))\s*[\"'“”]?\s*([A-Za-z0-9._-]{2,32})",
            r"([A-Za-z0-9._-]{2,32})\s*(?:是|属于|在)\s*(?:solana|sol|ethereum|eth|bsc|binance|base|ton|monad)\s*链?",
            r"(?:analy[sz]e|research|review)\s+(?:the\s+)?([A-Za-z0-9._-]{2,32})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                token_query = match.group(1)
                break

    if not token_query:
        words = re.findall(r"[A-Za-z0-9._-]{2,32}", text)
        stopwords = {
            "i", "want", "to", "analyze", "analysis", "the", "is", "chain",
            "on", "more", "accurate", "concise", "friendly", "tone", "please",
            *CHAIN_ALIASES.keys(),
        }
        token_query = next((word for word in words if word.lower() not in stopwords), text)

    verbose = len(text.split()) > 3 or len(text) > 32
    return {
        "token_query": token_query.strip(),
        "chain": chain,
        "style_instruction": text if verbose else "",
        "writing_profile": infer_writing_profile(text),
    }
