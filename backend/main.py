"""
meme_ops 后端入口 — FastAPI 服务

启动: cd backend && python3 main.py
"""

import sys
import time
import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, HTTPException, Depends, Header, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from database import (
    init_db, save_analysis, get_history, get_analysis_detail,
    upsert_user, update_user, get_user, search_users,
    create_post, get_timeline, get_user_posts, delete_post,
    get_following_timeline, get_post_detail, get_bookmarked_posts,
    toggle_like, toggle_repost, create_quote, toggle_bookmark,
    toggle_follow, get_follow_counts, is_following,
    save_nft_record, get_user_nfts, confirm_nft_record,
    update_nft_display, hide_nft_record,
    add_to_watchlist, get_watchlist, delete_watchlist_item,
    batch_delete_watchlist, update_watchlist_order, update_watchlist_note,
    save_comparison_report, get_comparison_reports, get_comparison_report,
    delete_comparison_report,
)
from agent import MemeOpsAgent, analysis_provider_status
from auth import generate_nonce, get_nonce_message, verify_signature, create_token, verify_token
from nft import build_metadata, get_mint_contract_info
from image_provider import (
    generate_background, image_provider_status, onchain_metadata_limits,
    pin_metadata_to_ipfs, prepare_onchain_metadata, OnchainMetadataTooLarge,
)
from poster_planner import build_poster_plan
from comparison import build_comparison_report
from social import (
    SocialCollector, SocialConfigurationError,
    begin_x_connection, complete_x_connection,
    begin_telegram_connection, complete_telegram_connection,
    create_telegram_link_code, disconnect_provider,
    configure_telegram_webhook,
    latest_social_context, list_connections, list_social_assets,
    process_telegram_webhook, social_provider_status,
    social_connection_diagnostics,
    demo_social_enabled,
    seed_demo_social_snapshots,
    start_social_scheduler, stop_social_scheduler,
    validate_telegram_bot_configuration,
)
from models import (
    AnalyzeRequest, ComparisonRequest, LoginRequest,
    CreatePostRequest, RepostRequest, MintNFTRequest,
    UpdateProfileRequest,
)

# ============ App ============

app = FastAPI(title="meme_ops API", version="0.3.0")

cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = MemeOpsAgent()
_top_memes_cache = {"expires": 0.0, "items": []}
_watchlist_market_cache = {}
_poster_draft_cache = {}
_analysis_jobs: dict[str, dict] = {}
_analysis_job_tasks: dict[str, asyncio.Task] = {}
_comparison_jobs: dict[str, dict] = {}
_comparison_job_tasks: dict[str, asyncio.Task] = {}
_ANALYSIS_JOB_TTL_SECONDS = 60 * 60


@app.on_event("startup")
async def startup():
    init_db()
    if demo_social_enabled():
        demo_result = seed_demo_social_snapshots(
            int(os.getenv("DEMO_SOCIAL_DATA_LIMIT", "10"))
        )
        print(
            "Synthetic demo social seed: "
            f"assets={demo_result['asset_count']} "
            f"snapshots={demo_result['snapshot_count']}"
        )
    agent.reload_memory()
    start_social_scheduler()
    if os.getenv("TELEGRAM_AUTO_SET_WEBHOOK", "false").lower() == "true":
        try:
            await configure_telegram_webhook()
        except Exception as error:
            print(f"Telegram webhook setup skipped: {error}")


@app.on_event("shutdown")
async def shutdown():
    await stop_social_scheduler()


# ============ 认证中间件 ============

async def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    """从 Bearer token 中提取当前用户地址"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization.split(" ", 1)[1]
    address = verify_token(token)
    if not address:
        raise HTTPException(status_code=401, detail="Session token is invalid or expired")
    return address


# ============ 根路由 ============

@app.get("/api/health")
async def health():
    import database

    conn = database.get_connection()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()
    return {
        "service": "meme_ops",
        "version": "0.3.0",
        "status": "running",
        "database": "ok",
    }


@app.get("/api/market/top-memes")
async def top_memes():
    """Top meme assets by market cap, cached briefly with a resilient fallback."""
    if _top_memes_cache["items"] and _top_memes_cache["expires"] > time.time():
        return {"items": _top_memes_cache["items"], "cached": True}
    chain_map = {
        "dogecoin": "dogecoin", "shiba-inu": "ethereum", "pepe": "ethereum",
        "official-trump": "solana", "bonk": "solana", "floki": "ethereum",
        "dogwifcoin": "solana", "brett": "base", "pudgy-penguins": "solana",
        "fartcoin": "solana",
    }
    try:
        import httpx
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd", "category": "meme-token",
                    "order": "market_cap_desc", "per_page": 10, "page": 1,
                    "sparkline": "false", "price_change_percentage": "24h",
                },
            )
            resp.raise_for_status()
            items = [{
                "rank": index + 1,
                "id": coin.get("id"),
                "name": coin.get("name"),
                "symbol": (coin.get("symbol") or "").upper(),
                "image": coin.get("image"),
                "chain": chain_map.get(coin.get("id"), "ethereum"),
                "price": coin.get("current_price"),
                "market_cap": coin.get("market_cap"),
                "change_24h": coin.get("price_change_percentage_24h"),
            } for index, coin in enumerate(resp.json()[:10])]
            _top_memes_cache.update({"expires": time.time() + 60, "items": items})
            return {"items": items, "cached": False}
    except Exception:
        fallback = [
            ("Dogecoin", "DOGE", "dogecoin"), ("Shiba Inu", "SHIB", "ethereum"),
            ("Pepe", "PEPE", "ethereum"), ("Official Trump", "TRUMP", "solana"),
            ("Bonk", "BONK", "solana"), ("Floki", "FLOKI", "ethereum"),
            ("dogwifhat", "WIF", "solana"), ("Brett", "BRETT", "base"),
            ("Pudgy Penguins", "PENGU", "solana"), ("Fartcoin", "FARTCOIN", "solana"),
        ]
        return {"items": [
            {"rank": i + 1, "name": name, "symbol": symbol, "chain": chain,
             "image": None, "price": None, "market_cap": None, "change_24h": None}
            for i, (name, symbol, chain) in enumerate(fallback)
        ], "cached": True}


@app.get("/api/analysis/provider")
async def api_analysis_provider_status():
    return analysis_provider_status()


# ============ Social connections and intelligence ============

@app.get("/api/social/provider")
async def api_social_provider_status(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    status = social_provider_status()
    status["public_app_url"] = (
        os.getenv("APP_PUBLIC_URL", "").strip()
        or str(request.base_url).rstrip("/")
    )
    return status


@app.get("/api/social/connections")
async def api_social_connections(user=Depends(get_current_user)):
    return list_connections(user)


@app.post("/api/social/diagnostics")
async def api_social_diagnostics(
    data: dict | None = None, user=Depends(get_current_user),
):
    return await social_connection_diagnostics(
        user, force=bool((data or {}).get("force")),
    )


@app.post("/api/social/x/connect")
async def api_connect_x(request: Request, user=Depends(get_current_user)):
    try:
        return begin_x_connection(user, str(request.base_url))
    except SocialConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/social/x/callback")
async def api_x_callback(
    request: Request, code: str = "", state: str = "", error: str = "",
):
    if error:
        return RedirectResponse(url=f"/?social=x&status=cancelled&reason={error}#/settings")
    try:
        result = await complete_x_connection(code, state, str(request.base_url))
        return RedirectResponse(
            url=f"/?social=x&status=connected{result['redirect_path']}"
        )
    except Exception as callback_error:
        message = str(callback_error)[:160].replace(" ", "%20")
        return RedirectResponse(url=f"/?social=x&status=error&reason={message}#/settings")


@app.post("/api/social/telegram/connect")
async def api_connect_telegram(request: Request, user=Depends(get_current_user)):
    try:
        await validate_telegram_bot_configuration()
        return begin_telegram_connection(user, str(request.base_url))
    except SocialConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/social/telegram/callback")
async def api_telegram_callback(request: Request):
    payload = dict(request.query_params)
    try:
        result = complete_telegram_connection(payload)
        return RedirectResponse(
            url=f"/?social=telegram&status=connected{result['redirect_path']}"
        )
    except Exception as callback_error:
        message = str(callback_error)[:160].replace(" ", "%20")
        return RedirectResponse(
            url=f"/?social=telegram&status=error&reason={message}#/settings"
        )


@app.post("/api/social/telegram/callback")
async def api_telegram_callback_inline(
    data: dict, user=Depends(get_current_user),
):
    """Verify Widget data without a cross-page query-string redirect."""
    try:
        result = complete_telegram_connection(data, expected_owner=user)
        return {
            "connected": True,
            "provider": "telegram",
            "owner_address": result["owner_address"],
        }
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/social/telegram/link-code")
async def api_telegram_link_code(data: dict, user=Depends(get_current_user)):
    try:
        return create_telegram_link_code(user, data.get("asset_key"))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/social/telegram/webhook")
async def api_telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    configured = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not configured or not secrets_compare(
        configured, x_telegram_bot_api_secret_token or "",
    ):
        raise HTTPException(status_code=401, detail="Telegram webhook secret is invalid")
    try:
        return await process_telegram_webhook(await request.json())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def secrets_compare(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


@app.delete("/api/social/connections/{provider}")
async def api_disconnect_social(provider: str, user=Depends(get_current_user)):
    if not disconnect_provider(user, provider.lower()):
        raise HTTPException(status_code=404, detail="Social connection not found")
    return {"message": f"{provider} disconnected"}


@app.get("/api/social/assets")
async def api_social_assets(limit: int = 100):
    return {"items": list_social_assets(max(100, min(limit, 500)))}


@app.get("/api/social/assets/{asset_key:path}/insights")
async def api_social_asset_insights(asset_key: str):
    context = latest_social_context(asset_key)
    if not context["metrics"] and not context["rag_documents"]:
        raise HTTPException(status_code=404, detail="No social intelligence collected yet")
    return context


@app.post("/api/social/assets/{asset_key:path}/collect")
async def api_collect_social_asset(
    asset_key: str, user=Depends(get_current_user),
):
    asset = next(
        (item for item in list_social_assets(500) if item["asset_key"] == asset_key),
        None,
    )
    if not asset:
        raise HTTPException(status_code=404, detail="Social asset is not registered")
    try:
        result = await SocialCollector().collect_asset(asset, user)
    except SocialConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {
        **result,
        "context": latest_social_context(asset_key, owner_address=user),
    }


# ============ 认证 API ============

@app.post("/api/auth/nonce")
async def auth_nonce(data: dict):
    """获取签名 nonce"""
    address = data.get("address", "")
    if not address:
        raise HTTPException(status_code=400, detail="Wallet address is required")
    generate_nonce(address)
    message = get_nonce_message(address)
    return {"nonce": message}


@app.post("/api/auth/login")
async def auth_login(req: LoginRequest):
    """签名验证登录"""
    if not verify_signature(req.address, req.signature, req.nonce):
        raise HTTPException(status_code=401, detail="Signature verification failed")
    upsert_user(req.address)
    token = create_token(req.address)
    return {"token": token, "address": req.address}


@app.get("/api/auth/me")
async def auth_me(user=Depends(get_current_user)):
    profile = get_user(user)
    counts = get_follow_counts(user)
    return {**(profile or {}), **counts}


# ============ 分析 API ============

@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, user=Depends(get_current_user)):
    """提交分析 — 拉取真实数据 + LLM 打分 + 生成海报图"""
    agent.set_persona(req.persona)
    report = await agent.analyze(req.prompt, req.report_style, owner_address=user)
    # 从 prompt 中提取链名（兜底保证 chain 正确）
    import re
    chain_aliases = {"sol": "solana", "solana": "solana", "eth": "ethereum", "ethereum": "ethereum",
                     "bsc": "bsc", "binance": "bsc", "ton": "ton", "monad": "monad"}
    for alias, chain_id in chain_aliases.items():
        if re.search(rf'\b{alias}\b', req.prompt, re.IGNORECASE):
            report.setdefault("token", {})["chain"] = chain_id
            break
    resolved_chain = req.chain or report.get("token", {}).get("chain") or "unknown"
    analysis_id = save_analysis(
        token_name=req.token_name or report["token"]["name"],
        prompt=req.prompt,
        report=report,
        overall_score=report["overall_score"],
        risk_level=report["risk_level"],
        persona=req.persona,
        contract_addr=req.contract_addr,
        chain=resolved_chain,
        owner_address=user,
        report_style=req.report_style,
    )
    # 同步生成三张海报图
    from charts import generate_all_charts
    charts = generate_all_charts(report)
    return {
        "analysis_id": analysis_id,
        "report": report,
        "charts": charts,
        "source_request": req.model_dump(),
    }


def _prune_analysis_jobs() -> None:
    cutoff = time.time() - _ANALYSIS_JOB_TTL_SECONDS
    expired = [
        job_id for job_id, job in _analysis_jobs.items()
        if job.get("updated_at", job.get("created_at", 0)) < cutoff
        and job.get("status") in {"completed", "failed", "cancelled"}
    ]
    for job_id in expired:
        _analysis_jobs.pop(job_id, None)
        _analysis_job_tasks.pop(job_id, None)


def _public_analysis_job(job: dict) -> dict:
    payload = {
        "job_id": job["job_id"],
        "kind": "analysis",
        "status": job["status"],
        "progress": job.get("progress", 0),
        "stage": job.get("stage", "Queued"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "source_request": job.get("source_request"),
    }
    if job.get("status") == "completed":
        payload["result"] = job.get("result")
    elif job.get("status") == "failed":
        payload["error"] = job.get("error") or "Analysis failed"
    return payload


def _get_owned_analysis_job(job_id: str, user: str) -> dict:
    job = _analysis_jobs.get(job_id)
    if not job or job.get("owner_address") != user.lower():
        raise HTTPException(status_code=404, detail="Analysis job not found")
    return job


async def _run_analysis_job(job_id: str, req: AnalyzeRequest, user: str) -> None:
    job = _analysis_jobs[job_id]

    def update(progress: int, stage: str) -> None:
        job.update({
            "progress": max(0, min(int(progress), 100)),
            "stage": stage,
            "updated_at": time.time(),
        })

    try:
        job["status"] = "running"
        update(8, "Resolving the exact asset and chain")
        worker_agent = MemeOpsAgent()
        worker_agent.set_persona(req.persona)
        report = await worker_agent.analyze(
            req.prompt,
            req.report_style,
            owner_address=user,
            progress_callback=update,
        )
        update(78, "Saving the wallet-private report")

        import re
        chain_aliases = {
            "sol": "solana", "solana": "solana", "eth": "ethereum",
            "ethereum": "ethereum", "bsc": "bsc", "binance": "bsc",
            "ton": "ton", "monad": "monad",
        }
        for alias, chain_id in chain_aliases.items():
            if re.search(rf"\b{alias}\b", req.prompt, re.IGNORECASE):
                report.setdefault("token", {})["chain"] = chain_id
                break
        resolved_chain = req.chain or report.get("token", {}).get("chain") or "unknown"
        analysis_id = save_analysis(
            token_name=req.token_name or report["token"]["name"],
            prompt=req.prompt,
            report=report,
            overall_score=report["overall_score"],
            risk_level=report["risk_level"],
            persona=req.persona,
            contract_addr=req.contract_addr,
            chain=resolved_chain,
            owner_address=user,
            report_style=req.report_style,
        )

        update(84, "Rendering three high-resolution charts")
        from charts import generate_all_charts
        charts = await asyncio.to_thread(generate_all_charts, report)
        update(98, "Preparing the report workspace")
        job["result"] = {
            "analysis_id": analysis_id,
            "report": report,
            "charts": charts,
            "source_request": req.model_dump(),
        }
        job["status"] = "completed"
        update(100, "Report ready")
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        update(job.get("progress", 0), "Analysis stopped")
    except Exception as error:
        job["status"] = "failed"
        job["error"] = str(error)[:500]
        update(job.get("progress", 0), "Analysis failed")
    finally:
        _analysis_job_tasks.pop(job_id, None)


@app.post("/api/analysis/jobs", status_code=202)
async def create_analysis_job(req: AnalyzeRequest, user=Depends(get_current_user)):
    """Start a cancellable analysis without blocking navigation in the client."""
    _prune_analysis_jobs()
    job_id = uuid.uuid4().hex
    now = time.time()
    _analysis_jobs[job_id] = {
        "job_id": job_id,
        "owner_address": user.lower(),
        "status": "queued",
        "progress": 0,
        "stage": "Queued",
        "source_request": req.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    _analysis_job_tasks[job_id] = asyncio.create_task(
        _run_analysis_job(job_id, req, user)
    )
    return _public_analysis_job(_analysis_jobs[job_id])


@app.get("/api/analysis/jobs/{job_id}")
async def analysis_job_status(job_id: str, user=Depends(get_current_user)):
    _prune_analysis_jobs()
    return _public_analysis_job(_get_owned_analysis_job(job_id, user))


@app.delete("/api/analysis/jobs/{job_id}")
async def cancel_analysis_job(job_id: str, user=Depends(get_current_user)):
    job = _get_owned_analysis_job(job_id, user)
    if job["status"] in {"completed", "failed", "cancelled"}:
        return _public_analysis_job(job)
    job["status"] = "cancelling"
    job["stage"] = "Stopping safely"
    job["updated_at"] = time.time()
    task = _analysis_job_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    return _public_analysis_job(job)


def _prune_comparison_jobs() -> None:
    cutoff = time.time() - _ANALYSIS_JOB_TTL_SECONDS
    expired = [
        job_id for job_id, job in _comparison_jobs.items()
        if job.get("updated_at", job.get("created_at", 0)) < cutoff
        and job.get("status") in {"completed", "failed", "cancelled"}
    ]
    for job_id in expired:
        _comparison_jobs.pop(job_id, None)
        _comparison_job_tasks.pop(job_id, None)


def _public_comparison_job(job: dict) -> dict:
    payload = {
        "job_id": job["job_id"],
        "kind": "comparison",
        "status": job["status"],
        "progress": job.get("progress", 0),
        "stage": job.get("stage", "Queued"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "source_request": job.get("source_request"),
    }
    if job.get("status") == "completed":
        payload["result"] = job.get("result")
    elif job.get("status") == "failed":
        payload["error"] = job.get("error") or "Comparison failed"
    return payload


def _get_owned_comparison_job(job_id: str, user: str) -> dict:
    job = _comparison_jobs.get(job_id)
    if not job or job.get("owner_address") != user.lower():
        raise HTTPException(status_code=404, detail="Comparison job not found")
    return job


def _resolve_comparison_inputs(
    req: ComparisonRequest, user: str,
) -> tuple[str, list[dict]]:
    if len(set(req.watchlist_ids)) != len(req.watchlist_ids):
        raise HTTPException(
            status_code=400,
            detail="Duplicate watchlist assets are not allowed",
        )
    persona = req.persona if req.persona in (
        "investor", "operator", "builder", "researcher",
    ) else "operator"
    owned = {item["id"]: item for item in get_watchlist(user)}
    selected = [owned[item_id] for item_id in req.watchlist_ids if item_id in owned]
    if len(selected) != len(req.watchlist_ids):
        raise HTTPException(
            status_code=403,
            detail="One or more selected assets do not belong to this wallet",
        )
    return persona, selected


async def _run_comparison_job(
    job_id: str,
    req: ComparisonRequest,
    user: str,
    persona: str,
    selected: list[dict],
) -> None:
    job = _comparison_jobs[job_id]

    def update(progress: int, stage: str) -> None:
        job.update({
            "progress": max(0, min(int(progress), 100)),
            "stage": stage,
            "updated_at": time.time(),
        })

    try:
        job["status"] = "running"
        update(6, "Validating wallet-private comparison inputs")
        worker_agent = MemeOpsAgent()
        report = await build_comparison_report(
            worker_agent,
            selected,
            persona,
            req.report_style,
            owner_address=user,
            progress_callback=update,
        )
        update(94, "Saving the comparison report")
        comparison_id = save_comparison_report(
            owner_address=user,
            title=report["title"],
            persona=persona,
            report=report,
            report_style=req.report_style,
        )
        job["result"] = {
            "comparison_id": comparison_id,
            "report": report,
            "created_at": report.get("generated_at"),
            "source_request": req.model_dump(),
        }
        job["status"] = "completed"
        update(100, "Comparison ready")
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        update(job.get("progress", 0), "Comparison stopped")
    except Exception as error:
        job["status"] = "failed"
        job["error"] = str(error)[:500]
        update(job.get("progress", 0), "Comparison failed")
    finally:
        _comparison_job_tasks.pop(job_id, None)


@app.post("/api/comparison/jobs", status_code=202)
async def create_comparison_job(
    req: ComparisonRequest, user=Depends(get_current_user),
):
    """Start a cancellable comparison without blocking navigation."""
    _prune_comparison_jobs()
    persona, selected = _resolve_comparison_inputs(req, user)
    job_id = uuid.uuid4().hex
    now = time.time()
    _comparison_jobs[job_id] = {
        "job_id": job_id,
        "owner_address": user.lower(),
        "status": "queued",
        "progress": 0,
        "stage": "Queued",
        "source_request": req.model_dump(),
        "created_at": now,
        "updated_at": now,
    }
    _comparison_job_tasks[job_id] = asyncio.create_task(
        _run_comparison_job(job_id, req, user, persona, selected)
    )
    return _public_comparison_job(_comparison_jobs[job_id])


@app.get("/api/comparison/jobs/{job_id}")
async def comparison_job_status(job_id: str, user=Depends(get_current_user)):
    _prune_comparison_jobs()
    return _public_comparison_job(_get_owned_comparison_job(job_id, user))


@app.delete("/api/comparison/jobs/{job_id}")
async def cancel_comparison_job(job_id: str, user=Depends(get_current_user)):
    job = _get_owned_comparison_job(job_id, user)
    if job["status"] in {"completed", "failed", "cancelled"}:
        return _public_comparison_job(job)
    job["status"] = "cancelling"
    job["stage"] = "Stopping safely"
    job["updated_at"] = time.time()
    task = _comparison_job_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    return _public_comparison_job(job)


@app.get("/api/history")
async def history(
    limit: int = 20, offset: int = 0, persona: str = None,
    user=Depends(get_current_user),
):
    records = get_history(user, limit=limit, offset=offset, persona=persona)
    return {"count": len(records), "records": records}


@app.post("/api/comparisons")
async def create_comparison(
    req: ComparisonRequest, user=Depends(get_current_user),
):
    """Create one wallet-private, same-persona horizontal comparison report."""
    persona, selected = _resolve_comparison_inputs(req, user)
    report = await build_comparison_report(
        agent, selected, persona, req.report_style, owner_address=user,
    )
    comparison_id = save_comparison_report(
        owner_address=user,
        title=report["title"],
        persona=persona,
        report=report,
        report_style=req.report_style,
    )
    return {"comparison_id": comparison_id, "report": report}


@app.get("/api/comparisons")
async def list_comparisons(
    limit: int = 30, user=Depends(get_current_user),
):
    records = get_comparison_reports(user, limit=max(1, min(limit, 100)))
    return {"count": len(records), "records": records}


@app.get("/api/comparisons/{comparison_id}")
async def comparison_detail(
    comparison_id: int, user=Depends(get_current_user),
):
    record = get_comparison_report(comparison_id, user)
    if not record:
        raise HTTPException(status_code=404, detail="Comparison report not found")
    return record


@app.delete("/api/comparisons/{comparison_id}")
async def delete_comparison(
    comparison_id: int, user=Depends(get_current_user),
):
    if not delete_comparison_report(comparison_id, user):
        raise HTTPException(status_code=404, detail="Comparison report not found")
    return {"message": "Comparison report deleted"}


@app.get("/api/analysis/{analysis_id}")
async def analysis_detail(analysis_id: int, user=Depends(get_current_user)):
    detail = get_analysis_detail(analysis_id, user)
    if not detail:
        raise HTTPException(status_code=404, detail="Analysis report not found")
    if isinstance(detail.get("report_summary"), str):
        detail["_raw_report"] = detail["report_summary"]
    return detail


@app.get("/api/debug/db-check")
async def debug_db_check(user=Depends(get_current_user)):
    """诊断：查看数据库中的记录"""
    import database
    conn = database.get_connection()
    try:
        analyses = conn.execute(
            """SELECT id, token_name, chain, persona FROM analysis_records
               WHERE owner_address = ? ORDER BY id DESC LIMIT 20""",
            (user,),
        ).fetchall()
        watchlist = conn.execute(
            "SELECT id, token_name, chain FROM watchlist WHERE owner_address = ?",
            (user,),
        ).fetchall()
        return {
            "analysis_count": len(analyses),
            "analyses": [dict(r) for r in analyses],
            "watchlist_count": len(watchlist),
            "watchlist": [dict(r) for r in watchlist],
        }
    finally:
        conn.close()


@app.delete("/api/analysis/{analysis_id}")
async def delete_analysis(analysis_id: int, user=Depends(get_current_user)):
    import database
    conn = database.get_connection()
    try:
        conn.execute(
            "DELETE FROM analysis_records WHERE id = ? AND owner_address = ?",
            (analysis_id, user),
        )
        conn.commit()
        return {"message": "Deleted"}
    finally:
        conn.close()


@app.post("/api/analysis/clear-all")
async def clear_all_analysis(user=Depends(get_current_user)):
    import database
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM analysis_records WHERE owner_address = ?", (user,))
        conn.commit()
        return {"message": "Analysis history cleared"}
    finally:
        conn.close()


@app.post("/api/analysis/batch-delete")
async def batch_delete_analysis(data: dict, user=Depends(get_current_user)):
    ids = data.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="At least one ID is required")
    import database
    conn = database.get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        cursor = conn.execute(
            f"DELETE FROM analysis_records WHERE owner_address = ? AND id IN ({placeholders})",
            [user, *ids],
        )
        conn.commit()
        return {"deleted": cursor.rowcount}
    finally:
        conn.close()


@app.get("/api/charts/{analysis_id}")
async def generate_charts(analysis_id: int, user=Depends(get_current_user)):
    """为分析记录生成三张海报图（base64 PNG）"""
    detail = get_analysis_detail(analysis_id, user)
    if not detail:
        raise HTTPException(status_code=404, detail="Analysis report not found")
    import json
    report = json.loads(detail["report_summary"]) if isinstance(detail["report_summary"], str) else detail["report_summary"]
    from charts import generate_all_charts
    charts = generate_all_charts(report)
    return {"analysis_id": analysis_id, "charts": charts}


@app.post("/api/memory/reload")
async def reload_memory():
    content = agent.reload_memory()
    return {"message": "Reloaded", "length": len(content)}


# ============ Persona API ============

@app.get("/api/personas")
async def list_personas():
    return {
        "personas": [
            {"id": "operator", "name": "Community Operator"},
            {"id": "investor", "name": "Investor"},
            {"id": "builder", "name": "Project Builder"},
            {"id": "researcher", "name": "Researcher"},
        ],
        "current": agent.current_persona,
    }


@app.post("/api/persona/switch")
async def switch_persona(data: dict):
    persona = data.get("persona", "operator")
    agent.set_persona(persona)
    return {"current": persona}


# ============ 帖子 API ============

@app.post("/api/posts")
async def api_create_post(req: CreatePostRequest, user=Depends(get_current_user)):
    _validate_post_media(req.image_data)
    if not req.content.strip() and not req.image_data:
        raise HTTPException(status_code=400, detail="Post text or a PNG image is required")
    post_id = create_post(
        user, req.content.strip(), req.attached_analysis_id, req.image_data,
    )
    return {"id": post_id, "message": "Published"}


@app.get("/api/posts")
async def api_timeline(
    limit: int = 20, offset: int = 0, mode: str = "recommended",
    user=Depends(get_current_user),
):
    posts = (
        get_following_timeline(user, limit, offset)
        if mode == "following"
        else get_timeline(user, limit, offset)
    )
    return {"count": len(posts), "posts": posts}


def _validate_post_media(image_data: Optional[str]) -> None:
    if not image_data:
        return
    if not image_data.startswith("data:image/png;base64,"):
        raise HTTPException(status_code=400, detail="Only PNG images are supported")
    import base64
    try:
        raw = base64.b64decode(image_data.split(",", 1)[1], validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="The PNG image is invalid")
    if len(raw) > 1024 * 1024:
        raise HTTPException(status_code=400, detail="The PNG must be 1 MB or smaller")


@app.get("/api/posts/{post_id}")
async def api_post_detail(post_id: int, user=Depends(get_current_user)):
    post = get_post_detail(post_id, user)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@app.post("/api/posts/{post_id}/replies")
async def api_reply(post_id: int, req: CreatePostRequest, user=Depends(get_current_user)):
    if not get_post_detail(post_id, user):
        raise HTTPException(status_code=404, detail="Post not found")
    _validate_post_media(req.image_data)
    if not req.content.strip() and not req.image_data:
        raise HTTPException(status_code=400, detail="Reply text or a PNG image is required")
    reply_id = create_post(
        user, req.content.strip(), image_data=req.image_data, parent_post_id=post_id,
    )
    return {"id": reply_id, "message": "Reply published"}


@app.delete("/api/posts/{post_id}")
async def api_delete_post(post_id: int, user=Depends(get_current_user)):
    ok = delete_post(post_id, user)
    if not ok:
        raise HTTPException(status_code=404, detail="Post not found or not owned by this wallet")
    return {"message": "Deleted"}


@app.post("/api/posts/{post_id}/like")
async def api_like(post_id: int, user=Depends(get_current_user)):
    liked = toggle_like(post_id, user)
    return {"liked": liked}


@app.post("/api/posts/{post_id}/bookmark")
async def api_bookmark(post_id: int, user=Depends(get_current_user)):
    bookmarked = toggle_bookmark(post_id, user)
    return {"bookmarked": bookmarked}


@app.get("/api/bookmarks")
async def api_bookmarks(user=Depends(get_current_user)):
    posts = get_bookmarked_posts(user)
    return {"count": len(posts), "posts": posts}


@app.post("/api/posts/{post_id}/repost")
async def api_repost(post_id: int, req: RepostRequest, user=Depends(get_current_user)):
    try:
        if req.quote_text is not None:
            repost_id = create_quote(post_id, user, req.quote_text)
            return {"id": repost_id, "quoted": True, "message": "Quoted"}
        reposted = toggle_repost(post_id, user)
        return {"reposted": reposted, "message": "Reposted" if reposted else "Repost removed"}
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ============ 用户 API ============

@app.get("/api/users/search")
async def api_search_users(q: str = "", user=Depends(get_current_user)):
    if not q.strip():
        return {"users": []}
    return {"users": search_users(q)}


@app.get("/api/users/{address}")
async def api_user_profile(address: str, authorization: Optional[str] = Header(None)):
    profile = get_user(address)
    if not profile:
        upsert_user(address)
        profile = get_user(address)
    counts = get_follow_counts(address)
    viewer = ""
    if authorization and authorization.startswith("Bearer "):
        viewer = verify_token(authorization.split(" ", 1)[1]) or ""
    following = bool(viewer and viewer.lower() != address.lower() and is_following(viewer, address))
    return {**(profile or {}), **counts, "isFollowing": following}


@app.get("/api/users/{address}/posts")
async def api_user_posts(
    address: str, limit: int = 20, offset: int = 0,
    authorization: Optional[str] = Header(None),
):
    viewer = ""
    if authorization and authorization.startswith("Bearer "):
        viewer = verify_token(authorization.split(" ", 1)[1]) or ""
    posts = get_user_posts(address, limit, offset, viewer)
    return {"count": len(posts), "posts": posts}


@app.patch("/api/users/profile")
async def api_update_profile(req: UpdateProfileRequest, user=Depends(get_current_user)):
    update_user(user, req.nickname, req.avatar, req.bio)
    return {"message": "Updated"}


@app.post("/api/users/{address}/follow")
async def api_follow(address: str, user=Depends(get_current_user)):
    if user.lower() == address.lower():
        raise HTTPException(status_code=400, detail="You cannot follow yourself")
    followed = toggle_follow(user, address)
    return {"following": followed}


@app.get("/api/users/{address}/followers")
async def api_followers(address: str, user=Depends(get_current_user)):
    if user.lower() != address.lower():
        raise HTTPException(status_code=403, detail="Only the account owner can open this list")
    import database
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT u.address, u.nickname, u.avatar
               FROM user_follows f JOIN users u ON f.follower = u.address
               WHERE f.following = ? ORDER BY f.rowid DESC LIMIT 50""",
            (address,),
        ).fetchall()
        return {"count": len(rows), "followers": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.get("/api/users/{address}/following")
async def api_following(address: str, user=Depends(get_current_user)):
    if user.lower() != address.lower():
        raise HTTPException(status_code=403, detail="Only the account owner can open this list")
    import database
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """SELECT u.address, u.nickname, u.avatar
               FROM user_follows f JOIN users u ON f.following = u.address
               WHERE f.follower = ? ORDER BY f.rowid DESC LIMIT 50""",
            (address,),
        ).fetchall()
        return {"count": len(rows), "following": [dict(r) for r in rows]}
    finally:
        conn.close()


# ============ NFT API ============

# ============ 自选列表 API ============

@app.get("/api/watchlist")
async def api_get_watchlist(user=Depends(get_current_user)):
    items = get_watchlist(user)
    return {"count": len(items), "items": items}


def _invalidate_watchlist_market(user: str):
    _watchlist_market_cache.pop(user.lower(), None)


@app.get("/api/watchlist/market")
async def api_watchlist_market(user=Depends(get_current_user)):
    cached = _watchlist_market_cache.get(user.lower())
    if cached and cached["expires"] > time.time():
        return {"items": cached["items"], "cached": True}
    items = get_watchlist(user)

    async def snapshot(item):
        try:
            query = item.get("contract_addr") or item.get("token_symbol") or item["token_name"]
            raw = await agent._fetch_raw_data(
                query, item.get("chain"), f"{item['token_name']} {item.get('chain') or ''}",
            )
            pair = next(iter((raw.get("dexscreener") or {}).get("pairs") or []), {})
            info = pair.get("info") or {}
            return {
                **item,
                "image": info.get("imageUrl") or (
                    ((raw.get("coingecko") or {}).get("image") or {}).get("small")
                ),
                "price": pair.get("priceUsd"),
                "change_24h": (pair.get("priceChange") or {}).get("h24"),
                "market_cap": pair.get("marketCap") or pair.get("fdv"),
                "liquidity": (pair.get("liquidity") or {}).get("usd"),
                "volume_24h": (pair.get("volume") or {}).get("h24"),
                "market_status": "live" if pair else "unavailable",
            }
        except Exception:
            return {**item, "market_status": "unavailable"}

    snapshots = await asyncio.gather(*(snapshot(item) for item in items))
    _watchlist_market_cache[user.lower()] = {
        "expires": time.time() + 60,
        "items": snapshots,
    }
    return {"items": snapshots, "cached": False}


@app.post("/api/watchlist")
async def api_add_watchlist(data: dict, user=Depends(get_current_user)):
    token_name = data.get("token_name", "")
    if not token_name:
        raise HTTPException(status_code=400, detail="Token name is required")
    chain = data.get("chain", "unknown")
    # 检查是否已存在同名同链
    items = get_watchlist(user)
    for item in items:
        if item["token_name"].lower() == token_name.lower() and (item.get("chain") or "").lower() == chain.lower():
            return {"id": item["id"], "message": "Already exists", "duplicate": True}
    item_id = add_to_watchlist(
        owner_address=user,
        token_name=token_name, chain=chain,
        token_symbol=data.get("token_symbol"),
        contract_addr=data.get("contract_addr"),
    )
    _invalidate_watchlist_market(user)
    return {"id": item_id, "message": "Added", "duplicate": False}


@app.delete("/api/watchlist/{item_id}")
async def api_delete_watchlist(item_id: int, user=Depends(get_current_user)):
    ok = delete_watchlist_item(item_id, user)
    if not ok:
        raise HTTPException(status_code=404, detail="Watchlist item not found")
    _invalidate_watchlist_market(user)
    return {"message": "Deleted"}


@app.post("/api/watchlist/batch-delete")
async def api_batch_delete_watchlist(data: dict, user=Depends(get_current_user)):
    ids = data.get("ids", [])
    if not ids:
        raise HTTPException(status_code=400, detail="At least one ID is required")
    count = batch_delete_watchlist(ids, user)
    _invalidate_watchlist_market(user)
    return {"deleted": count}


@app.post("/api/watchlist/reorder")
async def api_reorder_watchlist(data: dict, user=Depends(get_current_user)):
    ids = data.get("ids", [])
    update_watchlist_order(ids, user)
    _invalidate_watchlist_market(user)
    return {"message": "Order updated"}


@app.patch("/api/watchlist/{item_id}")
async def api_update_watchlist(item_id: int, data: dict, user=Depends(get_current_user)):
    notes = data.get("notes")
    if notes is not None:
        update_watchlist_note(item_id, notes, user)
    _invalidate_watchlist_market(user)
    return {"message": "Updated"}


# ============ NFT API ============

@app.get("/api/nft/contract")
async def nft_contract_info():
    return get_mint_contract_info()


@app.get("/api/nft/image-provider")
async def nft_image_provider_status():
    return {
        **image_provider_status(),
        "onchain_metadata": onchain_metadata_limits(),
    }


@app.post("/api/nft/metadata/{analysis_id}")
async def nft_build_metadata(
    analysis_id: int, poster_style: str = "Cyberpunk",
    pin: bool = False,
    user=Depends(get_current_user),
):
    """为指定分析记录生成 NFT 元数据（前端 mint 前调用）"""
    detail = get_analysis_detail(analysis_id, user)
    if not detail:
        raise HTTPException(status_code=404, detail="Analysis report not found")
    report_summary = detail.get("report_summary", "{}")
    import json
    report = json.loads(report_summary) if isinstance(report_summary, str) else report_summary
    poster_style = str(poster_style or "Cyberpunk").strip()[:120]
    cache_key = (user.lower(), analysis_id, poster_style)
    draft = _poster_draft_cache.get(cache_key)
    status = image_provider_status()
    if not draft:
        poster_plan = await build_poster_plan(report, poster_style)
        generated = await generate_background(report, poster_style, poster_plan)
        metadata = build_metadata(
            report,
            analysis_id,
            poster_style,
            generated.data_url if generated else None,
            generated.provider if generated else "template",
            generated.model if generated else "deterministic-template",
            poster_plan,
        )
        draft = {
            "metadata": metadata,
            "provider_status": status,
            "poster_plan": poster_plan,
        }
        _poster_draft_cache[cache_key] = draft
    result = dict(draft)
    if pin:
        if status["ipfs_configured"]:
            pinned = await pin_metadata_to_ipfs(result["metadata"])
            if pinned:
                pinned_metadata, token_uri = pinned
                result = {
                    **result,
                    "metadata": pinned_metadata,
                    "token_uri": token_uri,
                    "preview_image": draft["metadata"]["image"],
                    "storage": {
                        "mode": "ipfs",
                        "warning": None,
                    },
                }
        else:
            try:
                token_uri, storage = prepare_onchain_metadata(result["metadata"])
            except OnchainMetadataTooLarge as error:
                raise HTTPException(
                    status_code=413,
                    detail=str(error),
                ) from error
            result = {
                **result,
                "token_uri": token_uri,
                "preview_image": draft["metadata"]["image"],
                "storage": storage,
            }
    return result


@app.post("/api/nft/mint")
async def nft_record_mint(data: dict, user=Depends(get_current_user)):
    """
    记录铸造结果（用户在前端通过 MetaMask 完成链上 mint 后调用此接口记录）
    """
    token_id = data.get("token_id")
    tx_hash = data.get("tx_hash")
    analysis_id = data.get("analysis_id")
    if not all([token_id, tx_hash, analysis_id]):
        raise HTTPException(status_code=400, detail="Required fields are missing")
    token_uri = data.get("token_uri", "")
    contract_addr = data.get("contract_address", "0x0000000000000000000000000000000000000000")
    chain = data.get("chain", "monad-testnet")
    poster_image = data.get("poster_image")
    poster_style = data.get("poster_style")
    poster_uid = data.get("poster_uid")
    record_id = save_nft_record(
        token_id, contract_addr, chain, user, int(analysis_id), token_uri, tx_hash,
        poster_image, poster_style, poster_uid,
    )
    return {"id": record_id, "message": "NFT mint record saved"}


@app.get("/api/users/{address}/nfts")
async def api_user_nfts(address: str):
    nfts = get_user_nfts(address)
    import json
    for nft in nfts:
        if nft.get("poster_image") or not nft.get("analysis_id"):
            continue
        detail = get_analysis_detail(int(nft["analysis_id"]), address)
        if not detail:
            continue
        report_summary = detail.get("report_summary", "{}")
        report = json.loads(report_summary) if isinstance(report_summary, str) else report_summary
        metadata = build_metadata(
            report, int(nft["analysis_id"]), nft.get("poster_style") or "Cyberpunk",
        )
        nft["poster_image"] = metadata["image"]
        nft["poster_uid"] = metadata["poster_id"]
    return {"count": len(nfts), "nfts": nfts}


@app.patch("/api/nft/{record_id}/confirm")
async def api_confirm_nft(record_id: int, data: dict, user=Depends(get_current_user)):
    token_id = str(data.get("token_id") or "").strip()
    if not token_id.isdigit():
        raise HTTPException(status_code=400, detail="A numeric Token ID is required")
    if not confirm_nft_record(record_id, user, token_id):
        raise HTTPException(status_code=404, detail="Pending NFT record not found")
    return {"message": "Token ID confirmed", "token_id": token_id}


@app.patch("/api/nft/{record_id}")
async def api_update_nft_display(record_id: int, data: dict, user=Depends(get_current_user)):
    display_name = data.get("display_name")
    category = data.get("category")
    if display_name is not None:
        display_name = str(display_name).strip()[:80] or None
    if category is not None:
        category = str(category).strip()[:40] or None
    if not update_nft_display(record_id, user, display_name, category):
        raise HTTPException(status_code=404, detail="Poster NFT record not found")
    return {"message": "Poster display updated"}


@app.delete("/api/nft/{record_id}")
async def api_hide_nft(record_id: int, user=Depends(get_current_user)):
    if not hide_nft_record(record_id, user):
        raise HTTPException(status_code=404, detail="Poster NFT record not found")
    return {
        "message": "Poster hidden from this profile",
        "on_chain_unchanged": True,
    }


# ============ Production frontend ============

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.is_dir():
    # Keep this mount after every /api route so the same Railway service can
    # serve the browser app and API from one HTTPS origin.
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


# ============ 启动入口 ============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8788")),
        reload=os.getenv("APP_ENV", "development").lower() != "production",
    )
