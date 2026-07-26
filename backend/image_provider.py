"""Provider-neutral AI background generation for Poster NFTs."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass

import httpx

from config import load_project_env


load_project_env()


@dataclass
class GeneratedImage:
    data_url: str
    provider: str
    model: str


def image_provider_status() -> dict:
    requested = os.getenv("IMAGE_PROVIDER", "auto").strip().lower()
    available = []
    if os.getenv("OPENAI_API_KEY"):
        available.append("openai")
    if os.getenv("GEMINI_API_KEY"):
        available.append("gemini")
    if os.getenv("STABILITY_API_KEY"):
        available.append("stability")
    selected = requested
    if requested == "auto":
        selected = available[0] if available else "template"
    configured = selected in available
    return {
        "requested_provider": requested,
        "provider": selected,
        "configured": configured,
        "available_providers": available,
        "model": {
            "openai": os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2"),
            "gemini": os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"),
            "stability": os.getenv("STABILITY_IMAGE_MODEL", "core"),
        }.get(selected, "deterministic-template"),
        "ipfs_configured": bool(os.getenv("PINATA_JWT")),
    }


def onchain_metadata_limits() -> dict:
    """Configured byte thresholds for direct JSON/data-URI token metadata."""
    warning_bytes = max(1024, int(os.getenv("ONCHAIN_METADATA_WARNING_BYTES", "12000")))
    maximum_bytes = max(
        warning_bytes,
        int(os.getenv("ONCHAIN_METADATA_MAX_BYTES", "24000")),
    )
    return {
        "warning_bytes": warning_bytes,
        "maximum_bytes": maximum_bytes,
    }


class OnchainMetadataTooLarge(ValueError):
    """Direct on-chain metadata exceeds the configured hard limit."""

    def __init__(self, payload_bytes: int, maximum_bytes: int):
        self.payload_bytes = payload_bytes
        self.maximum_bytes = maximum_bytes
        super().__init__(
            f"Poster metadata is {payload_bytes:,} bytes, above the configured "
            f"direct on-chain limit of {maximum_bytes:,} bytes. "
            "Reduce image size/complexity or configure IPFS before minting; "
            "sending this payload would make Gas extremely expensive."
        )


def prepare_onchain_metadata(metadata: dict) -> tuple[str, dict]:
    """Serialize token metadata and apply configurable Gas-safety thresholds."""
    serialized = json.dumps(
        metadata,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    token_uri = (
        "data:application/json;base64,"
        + base64.b64encode(serialized).decode("ascii")
    )
    payload_bytes = len(token_uri.encode("utf-8"))
    limits = onchain_metadata_limits()
    if payload_bytes > limits["maximum_bytes"]:
        raise OnchainMetadataTooLarge(payload_bytes, limits["maximum_bytes"])

    warning = None
    if payload_bytes >= limits["warning_bytes"]:
        warning = (
            f"No IPFS storage is configured. This {payload_bytes:,}-byte metadata "
            "payload will be stored directly in the mint transaction and may require "
            "substantially more Gas. Review the wallet Gas estimate before confirming."
        )
    return token_uri, {
        "mode": "onchain-json",
        "payload_bytes": payload_bytes,
        **limits,
        "warning": warning,
    }


def build_background_prompt(report: dict, style: str, poster_plan: dict | None = None) -> str:
    token = report.get("token") or {}
    name = str(token.get("name") or "meme asset")
    symbol = str(token.get("symbol") or "").upper()
    chain = str(token.get("chain") or "unknown")
    score = float(report.get("overall_score") or 0)
    risk = str(report.get("risk_level") or "unknown")
    dimensions = sorted(
        report.get("dimensions") or [],
        key=lambda item: float(item.get("score") or 0),
        reverse=True,
    )
    strongest = ", ".join(str(item.get("dimension") or "") for item in dimensions[:2])
    plan = poster_plan or {}
    visual_keywords = ", ".join(str(item) for item in (plan.get("visual_keywords") or [])[:8])
    return (
        "Create a square premium collectible NFT poster background. "
        f"Subject: {name} ({symbol}) on {chain}; overall market signal {score:.1f}/10, "
        f"risk {risk}, strongest themes {strongest or 'market momentum'}. "
        f"Art direction: {style or 'cyberpunk city at night'}. "
        f"Report-derived visual keywords: {visual_keywords or 'market momentum, liquidity, risk'}. "
        f"Composition: {plan.get('layout', 'editorial')} layout with "
        f"{plan.get('copy_density', 'balanced')} information density. "
        "Turn the art direction into scene, architecture, lighting, materials, props, "
        "and composition. For example, football should create a real stadium/match motif; "
        "Japanese should create a recognizably Japanese visual language; technology buildings "
        "should appear as physical background architecture. "
        "Leave a clean dark information zone across the lower third for verified data overlays. "
        "Do not render any words, letters, numbers, logos, watermarks, UI panels, score bars, "
        "or the user's prompt. No legible text anywhere in the generated background."
    )


async def _openai_image(prompt: str) -> GeneratedImage:
    model = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-2")
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={
                "model": model,
                "prompt": prompt,
                "size": "1024x1024",
                "output_format": "png",
            },
        )
        response.raise_for_status()
        item = response.json()["data"][0]
        encoded = item.get("b64_json")
        if not encoded and item.get("url"):
            image_response = await client.get(item["url"])
            image_response.raise_for_status()
            encoded = base64.b64encode(image_response.content).decode("ascii")
        if not encoded:
            raise RuntimeError("OpenAI returned no image data")
        return GeneratedImage(f"data:image/png;base64,{encoded}", "openai", model)


def _find_inline_image(value):
    if isinstance(value, dict):
        mime = value.get("mime_type") or value.get("mimeType")
        data = value.get("data")
        if isinstance(data, str) and isinstance(mime, str) and mime.startswith("image/"):
            return mime, data
        for child in value.values():
            found = _find_inline_image(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_inline_image(child)
            if found:
                return found
    return None


async def _gemini_image(prompt: str) -> GeneratedImage:
    model = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            headers={
                "x-goog-api-key": os.environ["GEMINI_API_KEY"],
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": prompt,
                "response_format": {
                    "type": "image", "mime_type": "image/png", "aspect_ratio": "1:1",
                },
            },
        )
        response.raise_for_status()
        found = _find_inline_image(response.json())
        if not found:
            raise RuntimeError("Gemini returned no image data")
        mime, encoded = found
        return GeneratedImage(f"data:{mime};base64,{encoded}", "gemini", model)


async def _stability_image(prompt: str) -> GeneratedImage:
    variant = os.getenv("STABILITY_IMAGE_MODEL", "core").lower()
    if variant not in {"core", "ultra"}:
        variant = "core"
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"https://api.stability.ai/v2beta/stable-image/generate/{variant}",
            headers={
                "Authorization": f"Bearer {os.environ['STABILITY_API_KEY']}",
                "Accept": "image/*",
            },
            files={"none": ("", b"")},
            data={
                "prompt": prompt,
                "aspect_ratio": "1:1",
                "output_format": "png",
            },
        )
        response.raise_for_status()
        encoded = base64.b64encode(response.content).decode("ascii")
        return GeneratedImage(
            f"data:image/png;base64,{encoded}", "stability", f"stable-image-{variant}",
        )


async def generate_background(
    report: dict, style: str, poster_plan: dict | None = None,
) -> GeneratedImage | None:
    status = image_provider_status()
    if not status["configured"]:
        return None
    prompt = build_background_prompt(report, style, poster_plan)
    if status["provider"] == "openai":
        return await _openai_image(prompt)
    if status["provider"] == "gemini":
        return await _gemini_image(prompt)
    if status["provider"] == "stability":
        return await _stability_image(prompt)
    return None


async def pin_metadata_to_ipfs(metadata: dict) -> tuple[dict, str] | None:
    """Pin the composed image and metadata through Pinata when configured."""
    jwt = os.getenv("PINATA_JWT")
    image = str(metadata.get("image") or "")
    if not jwt or not image.startswith("data:") or ";base64," not in image:
        return None
    header, encoded = image.split(",", 1)
    mime = header[5:].split(";", 1)[0]
    extension = "svg" if mime == "image/svg+xml" else mime.split("/")[-1]
    image_bytes = base64.b64decode(encoded)
    auth = {"Authorization": f"Bearer {jwt}"}
    async with httpx.AsyncClient(timeout=120) as client:
        image_response = await client.post(
            "https://uploads.pinata.cloud/v3/files",
            headers=auth,
            files={"file": (f"{metadata['poster_id']}.{extension}", image_bytes, mime)},
            data={"name": metadata["poster_id"], "network": "public"},
        )
        image_response.raise_for_status()
        image_payload = image_response.json()
        image_cid = (
            (image_payload.get("data") or {}).get("cid")
            or image_payload.get("IpfsHash")
        )
        if not image_cid:
            raise RuntimeError("Pinata returned no image CID")
        pinned_metadata = dict(metadata)
        pinned_metadata["image"] = f"ipfs://{image_cid}"
        metadata_response = await client.post(
            "https://api.pinata.cloud/pinning/pinJSONToIPFS",
            headers={**auth, "Content-Type": "application/json"},
            json={
                "pinataMetadata": {"name": f"{metadata['poster_id']}.json"},
                "pinataContent": pinned_metadata,
            },
        )
        metadata_response.raise_for_status()
        metadata_cid = metadata_response.json().get("IpfsHash")
        if not metadata_cid:
            raise RuntimeError("Pinata returned no metadata CID")
        return pinned_metadata, f"ipfs://{metadata_cid}"
