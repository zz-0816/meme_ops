"""Strict token and chain matching helpers for DexScreener results."""

from __future__ import annotations

import re
from typing import Iterable


TOKEN_ALIASES = {
    "dogecoin": {"dogecoin", "doge"},
    "doge": {"dogecoin", "doge"},
    "shiba inu": {"shiba inu", "shib"},
    "shib": {"shiba inu", "shib"},
    "pepe": {"pepe"},
}


def normalize_asset_text(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def is_contract_address(value: str) -> bool:
    text = str(value or "").strip()
    if re.fullmatch(r"0x[a-fA-F0-9]{40}", text):
        return True
    # Solana public keys use base58 and are normally 32-44 characters.
    return bool(re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", text))


def requested_asset_terms(query: str) -> set[str]:
    normalized = normalize_asset_text(query)
    terms = {normalized, normalized.replace(" ", "")}
    terms.update(TOKEN_ALIASES.get(normalized, set()))
    return {term for term in terms if term}


def pair_identity_score(pair: dict, query: str) -> int:
    """Score identity relevance without allowing an unrelated liquid pair to win."""
    base = pair.get("baseToken") or {}
    name = normalize_asset_text(base.get("name"))
    symbol = normalize_asset_text(base.get("symbol"))
    wanted = requested_asset_terms(query)
    if name in wanted or symbol in wanted:
        return 100
    compact_name = name.replace(" ", "")
    if compact_name in wanted:
        return 95
    if any(
        len(term) >= 4 and (name.startswith(term) or compact_name.startswith(term))
        for term in wanted
    ):
        return 45
    return 0


def select_exact_pairs(
    pairs: Iterable[dict], query: str, chain: str | None = None,
) -> list[dict]:
    """Return pairs matching the requested asset and, when supplied, chain."""
    chain_id = normalize_asset_text(chain)
    ranked = []
    for pair in pairs or []:
        if chain_id and normalize_asset_text(pair.get("chainId")) != chain_id:
            continue
        score = pair_identity_score(pair, query)
        if score < 45:
            continue
        liquidity = float(((pair.get("liquidity") or {}).get("usd") or 0))
        ranked.append((score, liquidity, pair))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in ranked]
