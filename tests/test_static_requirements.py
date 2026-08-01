import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
MAIN = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
DB = (ROOT / "backend" / "database.py").read_text(encoding="utf-8")
AUTH = (ROOT / "backend" / "auth.py").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
RAILWAY = (ROOT / "railway.json").read_text(encoding="utf-8")


class StaticProductRequirementsTests(unittest.TestCase):
    def test_single_service_production_deployment_configuration(self):
        self.assertIn("window.location.origin", APP)
        self.assertIn('app.mount("/", StaticFiles', MAIN)
        self.assertIn('@app.get("/api/health")', MAIN)
        self.assertIn('os.getenv("PORT", "8788")', MAIN)
        self.assertIn('"CORS_ORIGINS"', MAIN)

    def test_production_session_and_sqlite_volume_are_configurable(self):
        self.assertIn('os.getenv("JWT_SECRET")', AUTH)
        self.assertIn('os.getenv("DATABASE_PATH"', DB)
        self.assertIn("DATABASE_PATH=/app/data/meme_ops.db", DOCKERFILE)
        self.assertIn('"healthcheckPath": "/api/health"', RAILWAY)
        for variable in ("APP_ENV", "PORT", "JWT_SECRET", "DATABASE_PATH", "CORS_ORIGINS"):
            self.assertIn(f"{variable}=", ENV_EXAMPLE)

    def test_analysis_accepts_name_and_chain_hint(self):
        self.assertIn("example: pepe sol", APP)
        self.assertIn("chain_aliases", MAIN)

    def test_poster_is_one_editable_block_with_mint(self):
        for marker in ("analysis-poster", "removePosterBlock", "posterStyle_", "mintPoster", "Cyberpunk"):
            self.assertIn(marker, APP)

    def test_history_detail_accepts_id_and_creates_result_container(self):
        self.assertIn("data.analysis_id ?? data.id", APP)
        self.assertIn("ensureAnalysisResults", APP)

    def test_watchlist_supports_drag_notes_and_batch_delete(self):
        for marker in ("watchlistDragStart", "watchlistDrop", "startInlineEdit", "batchDeleteWatchlist"):
            self.assertIn(marker, APP)
        self.assertIn("/api/watchlist/reorder", APP)

    def test_history_has_absolute_date_and_no_reanalyze_button(self):
        self.assertIn("`${year}/${month}/${day}`", APP)
        history_section = APP[APP.index("async function loadWatchlistHistory"):APP.index("async function batchDeleteWatchlist")]
        self.assertNotIn("重新分析", history_section)

    def test_history_matching_is_strict_by_token_and_chain(self):
        self.assertIn("recordMatchesTokenChain", APP)
        matcher = APP[APP.index("function recordMatchesTokenChain"):APP.index("async function loadWatchlistHistory")]
        self.assertIn("recordName !== expectedName", matcher)
        self.assertNotIn("unknown') return true", matcher)

    def test_personas_are_all_available(self):
        for persona in ("investor", "operator", "builder", "researcher"):
            self.assertTrue((ROOT / "personas" / f"{persona}.md").exists())
            self.assertIn(f'value="{persona}"', APP)

    def test_community_is_global_discovery_feed(self):
        timeline = DB[DB.index("def get_timeline"):DB.index("def get_user_posts")]
        self.assertNotIn("user_follows", timeline)
        self.assertIn("Explore", APP)

    def test_follow_lists_require_owner_auth(self):
        self.assertGreaterEqual(MAIN.count("Only the account owner can open this list"), 2)
        self.assertRegex(MAIN, re.compile(r"api_followers\(address: str, user=Depends\(get_current_user\)\)"))
        self.assertRegex(MAIN, re.compile(r"api_following\(address: str, user=Depends\(get_current_user\)\)"))

    def test_profile_name_edit_and_wallet_read_only(self):
        self.assertIn("renderProfileSettings", APP)
        self.assertIn("#/settings", APP)
        self.assertIn("/api/users/profile", APP)
        self.assertNotIn("修改钱包", APP)

    def test_routes_survive_refresh_and_external_profiles_are_separate(self):
        for route in ("#/overview", "#/analysis", "#/community", "#/profile", "#/settings", "#/user/"):
            self.assertIn(route, APP)
        self.assertIn("routeFromHash", APP)
        self.assertIn("switchTab('profile', false, null)", APP)

    def test_twitter_style_social_actions_and_post_delete(self):
        for marker in ("toggleLike", "repostPost", "submitQuote", "deleteOwnPost", "sharePost"):
            self.assertIn(marker, APP)
        self.assertIn("toggle_repost", DB)
        self.assertIn("You cannot repost your own post", DB)
        self.assertIn("quoted_post_id", DB)

    def test_profile_has_no_private_label(self):
        profile_section = APP[APP.index("async function renderProfile()"):APP.index("// ============ 辅助函数")]
        self.assertNotIn("私密", profile_section)

    def test_empty_history_has_no_start_analysis_button(self):
        history_section = APP[APP.index("async function loadWatchlistHistory"):APP.index("async function batchDeleteWatchlist")]
        self.assertNotIn("开始分析</button>", history_section)

    def test_static_responsive_styles_exist(self):
        self.assertIn("@media (max-width: 720px)", STYLE)

    def test_report_auto_resizes_and_contract_links_to_dexscreener(self):
        self.assertIn("resizeAnalysisFrame", APP)
        self.assertIn("https://dexscreener.com/search?q=", APP)

    def test_mint_metadata_uses_post_and_auth(self):
        mint = APP[APP.index("async function mintPoster"):APP.index("function encodeMintData")]
        self.assertIn("method: 'POST'", mint)
        self.assertIn("headers: apiHeaders()", mint)

    def test_analysis_controls_stay_in_shell(self):
        self.assertIn("analysis-controls", APP)
        self.assertIn("ensureAnalysisResults()", APP)
        self.assertIn("analysis-controls.results-mode", STYLE)
        self.assertIn("position:static", STYLE)

    def test_top_ten_market_board_and_click_to_analyze(self):
        self.assertIn("/api/market/top-memes", APP)
        self.assertIn("Top 10 Meme Assets", APP)
        self.assertIn("analyzeTopMeme", APP)
        self.assertIn('/api/market/top-memes', MAIN)

    def test_ops_first_overview_wallet_gate_and_top_ten_compare_shortcut(self):
        self.assertIn("currentPersona: localStorage.getItem('meme_ops_persona') || 'operator'", APP)
        self.assertIn("function renderOverview", APP)
        self.assertIn("Connect wallet to analyze", APP)
        self.assertIn("overviewSocialConnections", APP)
        self.assertIn("socialProviderLogo('x')", APP)
        self.assertIn("socialProviderLogo('telegram')", APP)
        self.assertIn("Server setup required:", APP)
        self.assertIn(".overview-social", STYLE)
        self.assertIn(".social-brand-telegram", STYLE)
        self.assertIn("addTopMemeToCompare", APP)
        self.assertIn('class="market-add"', APP)
        self.assertIn("Back to Top 10", APP)

    def test_report_is_conclusion_first_and_charts_render_before_text(self):
        charts = (ROOT / "backend" / "charts.py").read_text(encoding="utf-8")
        self.assertIn("build_report_html_v2", charts)
        self.assertIn("executive_conclusion", charts)
        self.assertIn("Action Plan", charts)
        self.assertIn("Supporting Evidence", charts)
        self.assertIn("dpi=180", charts)
        ordered = APP[APP.index("const orderedCard"):APP.index("container.insertAdjacentHTML('afterbegin', orderedCard)")]
        self.assertLess(ordered.index("chartImgs"), ordered.index("recCard"))

    def test_persona_rag_is_wallet_private(self):
        schema = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
        database = (ROOT / "backend" / "database.py").read_text(encoding="utf-8")
        self.assertIn("persona_rag_entries", schema)
        self.assertIn("owner_address", schema[schema.index("persona_rag_entries"):])
        self.assertIn("get_persona_rag_entries", database)
        self.assertIn("upsert_persona_rag_entry", database)

    def test_watchlist_has_no_manual_add_control(self):
        self.assertNotIn("addToWatchlistPrompt", APP)
        self.assertNotIn("+ Add", APP)

    def test_png_and_default_avatar_options(self):
        self.assertIn('accept=".png,image/png"', APP)
        self.assertIn("handleAvatarUpload", APP)
        self.assertIn("selectDefaultAvatar", APP)
        self.assertIn("emoji:", APP)

    def test_visible_ui_is_english(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        self.assertIn(">Home<", html)
        self.assertNotIn(">我的<", html)
        self.assertIn("Return the requested JSON in English", (ROOT / "backend" / "agent.py").read_text(encoding="utf-8"))

    def test_x_style_community_features(self):
        for marker in (
            "For you", "Following", "handleComposerPaste", "handlePostImageFiles",
            "toggleBookmark", "renderPostDetail", "submitReply", "view_count",
        ):
            self.assertIn(marker, APP + DB)
        for marker in ("post_bookmarks", "post_views", "parent_post_id", "image_data"):
            self.assertIn(marker, DB)

    def test_nft_gallery_and_receipt_confirmation(self):
        for marker in (
            "waitForTransactionReceipt", "resolveMintedTokenId", "poster_image",
            "nft-image-button", "Transaction ↗", "data-profile-section=\"nfts\"",
        ):
            self.assertIn(marker, APP)

    def test_natural_language_analysis_intent(self):
        agent = (ROOT / "backend" / "agent.py").read_text(encoding="utf-8")
        self.assertIn("_extract_request_intent", agent)
        self.assertIn("style_instruction", agent)
        self.assertIn("never alter, omit, or invent factual market metrics", agent)

    def test_model_fallback_is_visible_and_image_providers_are_explicit(self):
        provider = (ROOT / "backend" / "image_provider.py").read_text(encoding="utf-8")
        self.assertIn("generation_mode", APP + (ROOT / "backend" / "agent.py").read_text(encoding="utf-8"))
        for marker in ("OPENAI_IMAGE_MODEL", "GEMINI_IMAGE_MODEL", "STABILITY_IMAGE_MODEL"):
            self.assertIn(marker, provider)
        self.assertIn("PINATA_JWT", provider)

    def test_community_uses_compact_plus_composer_and_views_are_static(self):
        self.assertIn("compose-fab", APP)
        self.assertIn("openPostComposer", APP)
        self.assertNotIn("PNG · paste or drag · use @name to mention", APP)
        self.assertIn("pointer-events:none", STYLE)

    def test_dedicated_watchlist_market_page(self):
        for marker in ("#/watchlist", "renderWatchlistPage", "/api/watchlist/market", "PRIVATE WATCHLIST"):
            self.assertIn(marker, APP + MAIN)

    def test_watchlist_page_has_no_analysis_form_and_keeps_sidebar(self):
        section = APP[APP.index("async function renderWatchlistPage"):APP.index("function openWatchlistMarketHistory")]
        switch_section = APP[APP.index("function switchTab"):APP.index("function switchSidebarTab")]
        self.assertNotIn("renderAnalysisShell", section)
        self.assertNotIn("analysisInput", section)
        self.assertIn("tab === 'analysis' || tab === 'watchlist'", APP)
        self.assertIn("renderSidebar();", switch_section)
        self.assertIn("ensureWorkspaceResults", APP)

    def test_watchlist_market_cache_is_invalidated_after_mutations(self):
        self.assertIn("def _invalidate_watchlist_market", MAIN)
        start = MAIN.index('@app.post("/api/watchlist")')
        mutation_section = MAIN[start:MAIN.index("# ============ NFT API ============", start)]
        self.assertGreaterEqual(mutation_section.count("_invalidate_watchlist_market(user)"), 5)

    def test_report_style_is_a_separate_persisted_input(self):
        models = (ROOT / "backend" / "models.py").read_text(encoding="utf-8")
        agent = (ROOT / "backend" / "agent.py").read_text(encoding="utf-8")
        self.assertIn('id="reportStyleInput"', APP)
        self.assertIn("report_style: Optional[str]", models)
        self.assertIn("agent.analyze(req.prompt, req.report_style, owner_address=user)", MAIN)
        self.assertIn("infer_writing_profile(report_style)", agent)
        self.assertIn("report_style", DB)

    def test_image_provider_configuration_is_visible_in_poster_editor(self):
        self.assertIn("loadPosterProviderStatus", APP)
        self.assertIn("AI image renderer is not configured", APP)
        self.assertIn("poster-provider-status", STYLE)

    def test_comparison_is_separate_from_edit_delete_mode(self):
        self.assertIn("toggleCompareMode", APP)
        self.assertIn("openComparisonPersonaDialog", APP)
        self.assertIn("Create comparison", APP)
        self.assertNotIn("batchCompareWatchlist", APP)
        action_start = APP.index("const actionBar = editMode")
        action_end = APP.index("el.innerHTML = toolbar", action_start)
        edit_branch = APP[action_start:action_end].split(": compareMode", 1)[0]
        self.assertNotIn("Compare</button>", edit_branch)

    def test_comparison_reports_have_private_api_and_dedicated_sidebar_history(self):
        for marker in (
            '@app.post("/api/comparisons")',
            '@app.get("/api/comparisons")',
            '@app.get("/api/comparisons/{comparison_id}")',
            '@app.delete("/api/comparisons/{comparison_id}")',
        ):
            self.assertIn(marker, MAIN)
        for marker in (
            "COMPARISON REPORTS", "renderComparisonHistory",
            "renderComparisonReport", "comparison-matrix",
        ):
            self.assertIn(marker, APP + STYLE)

    def test_no_ipfs_onchain_metadata_has_warning_and_hard_limit(self):
        provider = (ROOT / "backend" / "image_provider.py").read_text(encoding="utf-8")
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        for marker in (
            "ONCHAIN_METADATA_WARNING_BYTES",
            "ONCHAIN_METADATA_MAX_BYTES",
        ):
            self.assertIn(marker, provider + example)
        self.assertIn("status_code=413", MAIN)
        self.assertIn("eth_estimateGas", APP)
        self.assertIn("Direct on-chain image metadata is much more expensive", APP)

    def test_nft_gallery_supports_rename_category_and_hide(self):
        for marker in ("editNFTDisplay", "filterNFTCategory", "hideNFTFromProfile"):
            self.assertIn(marker, APP)
        for marker in ("update_nft_display", "hide_nft_record"):
            self.assertIn(marker, DB)

    def test_watchlist_market_rows_open_full_history(self):
        watchlist_page = APP[APP.index("async function renderWatchlistPage"):APP.index("function renderAnalysisShell")]
        self.assertIn("openWatchlistMarketHistory", watchlist_page)
        self.assertIn("History →", watchlist_page)
        self.assertNotIn("analyzeWatchlistMarketItem", watchlist_page)

    def test_report_style_keywords_and_poster_plan_are_persisted(self):
        agent = (ROOT / "backend" / "agent.py").read_text(encoding="utf-8")
        planner = (ROOT / "backend" / "poster_planner.py").read_text(encoding="utf-8")
        for marker in ("report_keywords", "writing_profile", "poster_facts", "poster_narrative"):
            self.assertIn(marker, agent)
        for marker in ("selected_fact_ids", "copy_density", "context_lines", "visual_keywords"):
            self.assertIn(marker, planner)

    def test_report_charts_enlarge_individually_and_persona_switches_in_place(self):
        self.assertIn("clickable-report-chart", APP)
        self.assertIn("openImageViewer(this.src,this.alt)", APP)
        self.assertIn("report-perspective-switcher", APP + STYLE)
        self.assertIn("switchReportPerspective", APP)

    def test_analysis_jobs_are_non_blocking_cancellable_and_restorable(self):
        for marker in (
            '@app.post("/api/analysis/jobs"',
            '@app.get("/api/analysis/jobs/{job_id}")',
            '@app.delete("/api/analysis/jobs/{job_id}")',
            "asyncio.to_thread(generate_all_charts, report)",
        ):
            self.assertIn(marker, MAIN)
        for marker in (
            "startAnalysisJob", "cancelAnalysisJob", "restoreAnalysisJobs",
            "analysis-job-dock", "meme_ops_analysis_draft",
        ):
            self.assertIn(marker, APP + STYLE)
        submit = APP[APP.index("async function submitAnalysis()"):APP.index("function persistAnalysisJobs")]
        self.assertNotIn("showLoading(true)", submit)

    def test_comparison_jobs_are_non_blocking_cancellable_and_restorable(self):
        for marker in (
            '@app.post("/api/comparison/jobs"',
            '@app.get("/api/comparison/jobs/{job_id}")',
            '@app.delete("/api/comparison/jobs/{job_id}")',
            "_run_comparison_job",
        ):
            self.assertIn(marker, MAIN)
        for marker in (
            "startComparisonJob", "kind === 'comparison'",
            "Comparison ready", "Comparison stopped",
        ):
            self.assertIn(marker, APP + MAIN)
        create = APP[
            APP.index("async function createComparisonReport()"):
            APP.index("async function loadComparisonDetail")
        ]
        self.assertNotIn("showLoading(true)", create)
        self.assertNotIn("/api/comparisons`", create)

    def test_top_connection_icons_and_social_callback_feedback(self):
        social = (ROOT / "backend" / "social.py").read_text(encoding="utf-8")
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        for marker in ("walletStatusIcon", "xStatusIcon", "telegramStatusIcon"):
            self.assertIn(marker, html)
        for marker in (
            "injectedWalletInfo", "updateTopConnectionStatus",
            "handleSocialReturn", "openTelegramSetupGuide",
        ):
            self.assertIn(marker, APP)
        self.assertIn("request_base_url", social)
        self.assertIn("or request_base_url", social)

    def test_social_setup_requires_x_secret_and_keeps_telegram_tokens_server_side(self):
        social = (ROOT / "backend" / "social.py").read_text(encoding="utf-8")
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("X_OAUTH_PUBLIC_CLIENT", social + example)
        self.assertIn("X token exchange was rejected (401)", social)
        for marker in (
            "Project administrator action", "refreshTelegramSetup",
            "Regular users never paste a Bot Token", "Check current setup",
        ):
            self.assertIn(marker, APP)
        self.assertNotIn('id="telegramBotToken"', APP)

    def test_telegram_beginner_guide_explains_all_connection_stages(self):
        style = (ROOT / "frontend" / "style.css").read_text(encoding="utf-8")
        for marker in (
            "#/telegram-guide", "renderTelegramGuidePage", "loadTelegramGuideStatus",
            "You have successfully logged in to use Telegram Widgets",
            "Bind your Telegram identity", "Connect an administered group for metrics",
            "signature invalid or expired", "Check current setup",
            "Cannot be bound in this release", "Telegram Client API (MTProto)",
            "Saved Messages", "Why is /connect required?",
        ):
            self.assertIn(marker, APP)
        self.assertIn(".telegram-guide-page", style)
        self.assertIn(".telegram-access-model", style)

    def test_social_data_flow_has_inline_telegram_and_provider_diagnostics(self):
        main = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
        social = (ROOT / "backend" / "social.py").read_text(encoding="utf-8")
        schema = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
        for marker in (
            "data-onauth", "memeOpsTelegramAuth(user)",
            "runSocialDiagnostics", "Run data connection test",
        ):
            self.assertIn(marker, APP)
        self.assertIn('@app.post("/api/social/telegram/callback")', main)
        self.assertIn('@app.post("/api/social/diagnostics")', main)
        self.assertIn("credits_depleted", social)
        self.assertIn("wallet-oauth-search", social)
        self.assertIn("telegram_activity_events", schema + social)


if __name__ == "__main__":
    unittest.main()
