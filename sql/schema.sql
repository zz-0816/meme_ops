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
