-- meme_ops 数据库建表语句
-- 本地部署 SQLite，生产迁移 PostgreSQL/MySQL

-- ============ 分析记录 ============

CREATE TABLE IF NOT EXISTS analysis_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    token_name      TEXT NOT NULL,
    token_symbol    TEXT,
    contract_addr   TEXT,
    chain           TEXT DEFAULT 'unknown',
    prompt          TEXT NOT NULL,
    persona         TEXT DEFAULT 'investor',
    report_style    TEXT,
    analysis_type   TEXT DEFAULT 'single',
    compare_group_id TEXT,
    report_summary  TEXT,  -- JSON
    overall_score   REAL,
    risk_level      TEXT,
    data_sources    TEXT,  -- JSON
    owner_address   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comparison_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT NOT NULL,
    persona         TEXT NOT NULL,
    report_style    TEXT,
    asset_count     INTEGER NOT NULL,
    winner_name     TEXT,
    report_json     TEXT NOT NULL,
    generation_mode TEXT,
    generation_model TEXT,
    owner_address   TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_comparison_owner_created
ON comparison_reports(owner_address, created_at DESC);

CREATE TABLE IF NOT EXISTS dimension_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    dimension   TEXT NOT NULL,
    score       REAL NOT NULL,
    weight      REAL NOT NULL,
    raw_data    TEXT,
    notes       TEXT,
    FOREIGN KEY (analysis_id) REFERENCES analysis_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  INTEGER NOT NULL,
    metric_name  TEXT NOT NULL,
    metric_value REAL,
    metric_unit  TEXT,
    snapshot_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (analysis_id) REFERENCES analysis_records(id) ON DELETE CASCADE
);

-- Wallet-private memory for persona-specific report modules, style effects,
-- and reusable keywords.  This is deliberately separate from shared prompts:
-- one wallet can teach its Operator agent without changing another wallet.
CREATE TABLE IF NOT EXISTS persona_rag_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_address   TEXT NOT NULL,
    persona         TEXT NOT NULL,
    entry_type      TEXT NOT NULL,
    entry_key       TEXT NOT NULL,
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    keywords_json   TEXT DEFAULT '[]',
    use_count       INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_address, persona, entry_type, entry_key)
);

CREATE INDEX IF NOT EXISTS idx_persona_rag_lookup
ON persona_rag_entries(owner_address, persona, entry_type, updated_at DESC);

-- ============ Social connections and shared intelligence ============

CREATE TABLE IF NOT EXISTS social_connections (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_address            TEXT NOT NULL,
    provider                 TEXT NOT NULL,
    provider_user_id         TEXT NOT NULL,
    username                 TEXT,
    access_token_encrypted   TEXT,
    refresh_token_encrypted  TEXT,
    scopes                   TEXT DEFAULT '',
    expires_at               TIMESTAMP,
    status                   TEXT DEFAULT 'connected',
    metadata_json            TEXT DEFAULT '{}',
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_address, provider)
);

CREATE INDEX IF NOT EXISTS idx_social_connections_owner
ON social_connections(owner_address, provider);

CREATE TABLE IF NOT EXISTS social_oauth_states (
    state                    TEXT PRIMARY KEY,
    owner_address            TEXT NOT NULL,
    provider                 TEXT NOT NULL,
    verifier_encrypted       TEXT,
    redirect_path            TEXT DEFAULT '#/settings',
    expires_at               TIMESTAMP NOT NULL,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_link_codes (
    code                     TEXT PRIMARY KEY,
    owner_address            TEXT NOT NULL,
    provider                 TEXT NOT NULL,
    asset_key                TEXT,
    expires_at               TIMESTAMP NOT NULL,
    used_at                  TIMESTAMP,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS social_communities (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_address            TEXT NOT NULL,
    provider                 TEXT NOT NULL,
    external_community_id    TEXT NOT NULL,
    community_name           TEXT,
    asset_key                TEXT,
    permission_level         TEXT DEFAULT 'member',
    status                   TEXT DEFAULT 'connected',
    metadata_json            TEXT DEFAULT '{}',
    last_sync_at             TIMESTAMP,
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(owner_address, provider, external_community_id)
);

CREATE INDEX IF NOT EXISTS idx_social_communities_owner
ON social_communities(owner_address, provider);

-- Privacy-preserving Telegram activity aggregates. Raw message text and user
-- identifiers are never stored; only a per-community sender hash is retained.
CREATE TABLE IF NOT EXISTS telegram_activity_events (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    community_id             INTEGER NOT NULL,
    external_event_id        TEXT NOT NULL,
    sender_hash              TEXT,
    event_type               TEXT DEFAULT 'message',
    created_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(community_id, external_event_id),
    FOREIGN KEY (community_id) REFERENCES social_communities(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_telegram_activity_community_time
ON telegram_activity_events(community_id, created_at DESC);

-- asset_key uses chain:contract when possible and coingecko:<id> otherwise.
CREATE TABLE IF NOT EXISTS social_assets (
    asset_key                TEXT PRIMARY KEY,
    coin_id                  TEXT,
    name                     TEXT NOT NULL,
    symbol                   TEXT,
    chain                    TEXT DEFAULT 'unknown',
    contract_address         TEXT,
    image_url                TEXT,
    market_cap               REAL,
    volume_24h               REAL,
    change_24h               REAL,
    market_score             REAL DEFAULT 0,
    priority_tier            INTEGER DEFAULT 3,
    official_x               TEXT,
    telegram_chat            TEXT,
    metadata_json            TEXT DEFAULT '{}',
    last_market_sync_at      TIMESTAMP,
    updated_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_social_assets_priority
ON social_assets(priority_tier, market_score DESC);

CREATE TABLE IF NOT EXISTS social_metric_snapshots (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_key                TEXT NOT NULL,
    provider                 TEXT NOT NULL,
    source_mode              TEXT NOT NULL,
    owner_address            TEXT,
    community_id             INTEGER,
    followers                INTEGER,
    members                  INTEGER,
    mentions_24h             INTEGER,
    posts_24h                INTEGER,
    active_authors_24h       INTEGER,
    engagements_24h          INTEGER,
    engagement_rate          REAL,
    sentiment                REAL,
    confidence               REAL DEFAULT 0,
    raw_summary_json         TEXT DEFAULT '{}',
    collected_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (asset_key) REFERENCES social_assets(asset_key) ON DELETE CASCADE,
    FOREIGN KEY (community_id) REFERENCES social_communities(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_social_metrics_asset_time
ON social_metric_snapshots(asset_key, provider, collected_at DESC);

CREATE TABLE IF NOT EXISTS social_rag_documents (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_key                TEXT NOT NULL,
    platform                 TEXT NOT NULL,
    document_type            TEXT NOT NULL,
    period_key               TEXT NOT NULL,
    title                    TEXT NOT NULL,
    content                  TEXT NOT NULL,
    keywords_json            TEXT DEFAULT '[]',
    confidence               REAL DEFAULT 0,
    source_mode              TEXT NOT NULL,
    owner_scope              TEXT NOT NULL DEFAULT 'shared',
    owner_address            TEXT,
    collected_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at               TIMESTAMP,
    UNIQUE(asset_key, platform, document_type, period_key, owner_scope),
    FOREIGN KEY (asset_key) REFERENCES social_assets(asset_key) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_social_rag_asset_time
ON social_rag_documents(asset_key, collected_at DESC);

CREATE TABLE IF NOT EXISTS social_sync_runs (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    mode                     TEXT NOT NULL,
    provider                 TEXT,
    asset_count              INTEGER DEFAULT 0,
    success_count            INTEGER DEFAULT 0,
    error_count              INTEGER DEFAULT 0,
    status                   TEXT DEFAULT 'running',
    details_json             TEXT DEFAULT '{}',
    started_at               TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at              TIMESTAMP
);

-- ============ 自选列表 ============

CREATE TABLE IF NOT EXISTS watchlist (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token_name    TEXT NOT NULL,
    token_symbol  TEXT,
    contract_addr TEXT,
    chain         TEXT DEFAULT 'unknown',
    sort_order    INTEGER DEFAULT 0,
    notes         TEXT,
    owner_address TEXT,
    added_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============ 运营向 ============

CREATE TABLE IF NOT EXISTS community_member_snapshot (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    layer       TEXT NOT NULL,
    member_count INTEGER,
    percentage  REAL,
    change_7d   REAL,
    FOREIGN KEY (analysis_id) REFERENCES analysis_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS community_content_analysis (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    frequency   TEXT,
    engagement  REAL,
    FOREIGN KEY (analysis_id) REFERENCES analysis_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS competitor_benchmark (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id      INTEGER NOT NULL,
    competitor_name  TEXT NOT NULL,
    dimension        TEXT NOT NULL,
    competitor_score REAL,
    our_score        REAL,
    FOREIGN KEY (analysis_id) REFERENCES analysis_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS growth_playbook (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  INTEGER NOT NULL,
    week_start   TEXT,
    week_end     TEXT,
    goal_summary TEXT,
    status       TEXT DEFAULT 'active',
    FOREIGN KEY (analysis_id) REFERENCES analysis_records(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS playbook_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    playbook_id INTEGER NOT NULL,
    day         INTEGER,
    theme       TEXT,
    preparation TEXT,
    promotion   TEXT,
    execution   TEXT,
    kpi         TEXT,
    completed   INTEGER DEFAULT 0,
    FOREIGN KEY (playbook_id) REFERENCES growth_playbook(id) ON DELETE CASCADE
);

-- ============ DApp 平台层 ============

CREATE TABLE IF NOT EXISTS users (
    address    TEXT PRIMARY KEY,
    nickname   TEXT,
    avatar     TEXT,
    ens        TEXT,
    bio        TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    author                TEXT NOT NULL,
    content               TEXT NOT NULL,
    image_data            TEXT,
    attached_analysis_id  INTEGER,
    quoted_post_id        INTEGER,
    parent_post_id        INTEGER,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (author) REFERENCES users(address) ON DELETE CASCADE,
    FOREIGN KEY (attached_analysis_id) REFERENCES analysis_records(id) ON DELETE SET NULL,
    FOREIGN KEY (quoted_post_id) REFERENCES posts(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_likes (
    post_id      INTEGER NOT NULL,
    user_address TEXT NOT NULL,
    PRIMARY KEY (post_id, user_address),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (user_address) REFERENCES users(address) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_reposts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    original_post_id  INTEGER NOT NULL,
    reposter          TEXT NOT NULL,
    quote_text        TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (original_post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (reposter) REFERENCES users(address) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_bookmarks (
    post_id      INTEGER NOT NULL,
    user_address TEXT NOT NULL,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (post_id, user_address),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (user_address) REFERENCES users(address) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS post_views (
    post_id      INTEGER NOT NULL,
    user_address TEXT NOT NULL,
    viewed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (post_id, user_address),
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (user_address) REFERENCES users(address) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS user_follows (
    follower  TEXT NOT NULL,
    following TEXT NOT NULL,
    PRIMARY KEY (follower, following),
    FOREIGN KEY (follower) REFERENCES users(address) ON DELETE CASCADE,
    FOREIGN KEY (following) REFERENCES users(address) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS poster_nfts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id          TEXT NOT NULL,
    contract_address  TEXT NOT NULL,
    chain             TEXT DEFAULT 'monad-testnet',
    minter            TEXT NOT NULL,
    analysis_id       INTEGER,
    token_uri         TEXT,
    poster_image      TEXT,
    poster_style      TEXT,
    poster_uid        TEXT,
    display_name      TEXT,
    category          TEXT,
    hidden            INTEGER DEFAULT 0,
    tx_hash           TEXT,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (minter) REFERENCES users(address) ON DELETE CASCADE,
    FOREIGN KEY (analysis_id) REFERENCES analysis_records(id) ON DELETE SET NULL
);

-- ============ 索引 ============

CREATE INDEX IF NOT EXISTS idx_analysis_token   ON analysis_records(token_name);
CREATE INDEX IF NOT EXISTS idx_analysis_created ON analysis_records(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analysis_persona ON analysis_records(persona);
CREATE INDEX IF NOT EXISTS idx_scores_analysis  ON dimension_scores(analysis_id);
CREATE INDEX IF NOT EXISTS idx_posts_author     ON posts(author);
CREATE INDEX IF NOT EXISTS idx_posts_created    ON posts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_nfts_minter      ON poster_nfts(minter);
