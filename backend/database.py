"""
数据库操作层 — SQLite 实现
"""

import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

DB_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DB_DIR / "meme_ops.db"
FOLLOW_TABLE = "user_" + "follows"


def get_connection() -> sqlite3.Connection:
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    schema = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"
    conn = get_connection()
    try:
        conn.executescript(schema.read_text(encoding="utf-8"))
        post_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(posts)").fetchall()
        }
        if "quoted_post_id" not in post_columns:
            conn.execute(
                "ALTER TABLE posts ADD COLUMN quoted_post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL"
            )
        if "image_data" not in post_columns:
            conn.execute("ALTER TABLE posts ADD COLUMN image_data TEXT")
        if "parent_post_id" not in post_columns:
            conn.execute(
                "ALTER TABLE posts ADD COLUMN parent_post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE"
            )
        analysis_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(analysis_records)").fetchall()
        }
        if "owner_address" not in analysis_columns:
            conn.execute("ALTER TABLE analysis_records ADD COLUMN owner_address TEXT")
        if "report_style" not in analysis_columns:
            conn.execute("ALTER TABLE analysis_records ADD COLUMN report_style TEXT")
        watchlist_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()
        }
        if "owner_address" not in watchlist_columns:
            conn.execute("ALTER TABLE watchlist ADD COLUMN owner_address TEXT")
        nft_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(poster_nfts)").fetchall()
        }
        for column in ("poster_image", "poster_style", "poster_uid", "display_name", "category"):
            if column not in nft_columns:
                conn.execute(f"ALTER TABLE poster_nfts ADD COLUMN {column} TEXT")
        if "hidden" not in nft_columns:
            conn.execute("ALTER TABLE poster_nfts ADD COLUMN hidden INTEGER DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_analysis_owner ON analysis_records(owner_address)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_owner ON watchlist(owner_address)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_posts_parent ON posts(parent_post_id)"
        )
        conn.commit()
    finally:
        conn.close()


# ============ 分析记录 ============

def save_analysis(
    token_name: str,
    prompt: str,
    report: dict,
    overall_score: float,
    risk_level: str,
    persona: str = "investor",
    token_symbol: str = None,
    contract_addr: str = None,
    chain: str = "unknown",
    analysis_type: str = "single",
    compare_group_id: str = None,
    owner_address: str = None,
    report_style: str = None,
) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO analysis_records
               (token_name, token_symbol, contract_addr, chain, prompt, persona,
                analysis_type, compare_group_id, report_summary, overall_score,
                risk_level, data_sources, owner_address, report_style)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_name, token_symbol, contract_addr, chain, prompt,
                persona, analysis_type, compare_group_id,
                json.dumps(report, ensure_ascii=False),
                overall_score, risk_level,
                json.dumps(report.get("data_sources", []), ensure_ascii=False),
                owner_address,
                report_style,
            ),
        )
        analysis_id = cursor.lastrowid

        for dim in report.get("dimensions", []):
            conn.execute(
                """INSERT INTO dimension_scores
                   (analysis_id, dimension, score, weight, raw_data, notes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (analysis_id, dim["dimension"], dim["score"],
                 dim["weight"], json.dumps(dim.get("raw_data", {}), ensure_ascii=False),
                 dim.get("detail") or dim.get("notes", "")),
            )
        conn.commit()
        return analysis_id
    finally:
        conn.close()


def get_history(owner_address: str, limit: int = 20, offset: int = 0, persona: str = None) -> list:
    conn = get_connection()
    try:
        query = """SELECT id, token_name, token_symbol, chain, persona,
                          overall_score, risk_level, report_summary, created_at
                   FROM analysis_records"""
        params = []
        query += " WHERE owner_address = ?"
        params.append(owner_address)
        if persona:
            query += " AND persona = ?"
            params.append(persona)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        records = [dict(r) for r in conn.execute(query, params).fetchall()]
        # 兼容早期错误保存为 unknown 的记录：以报告中的真实链为准。
        for record in records:
            try:
                report = json.loads(record.get("report_summary") or "{}")
                report_chain = (report.get("token") or {}).get("chain")
                if report_chain:
                    record["chain"] = report_chain
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return records
    finally:
        conn.close()


def get_analysis_detail(analysis_id: int, owner_address: str) -> Optional[dict]:
    conn = get_connection()
    try:
        record = conn.execute(
            "SELECT * FROM analysis_records WHERE id = ? AND owner_address = ?",
            (analysis_id, owner_address),
        ).fetchone()
        if not record:
            return None
        result = dict(record)
        result["dimension_scores"] = [
            dict(s) for s in conn.execute(
                "SELECT * FROM dimension_scores WHERE analysis_id = ? ORDER BY weight DESC",
                (analysis_id,)
            ).fetchall()
        ]
        return result
    finally:
        conn.close()


def save_comparison_report(
    owner_address: str,
    title: str,
    persona: str,
    report: dict,
    report_style: str = None,
) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO comparison_reports
               (title, persona, report_style, asset_count, winner_name,
                report_json, generation_mode, generation_model, owner_address)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                persona,
                report_style,
                len(report.get("assets") or []),
                (report.get("winner") or {}).get("name"),
                json.dumps(report, ensure_ascii=False),
                report.get("generation_mode"),
                report.get("generation_model"),
                owner_address,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_comparison_reports(owner_address: str, limit: int = 30) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, title, persona, asset_count, winner_name,
                      generation_mode, generation_model, created_at
               FROM comparison_reports
               WHERE owner_address = ?
               ORDER BY created_at DESC, id DESC
               LIMIT ?""",
            (owner_address, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_comparison_report(
    comparison_id: int, owner_address: str,
) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM comparison_reports
               WHERE id = ? AND owner_address = ?""",
            (comparison_id, owner_address),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["report"] = json.loads(result.pop("report_json") or "{}")
        return result
    finally:
        conn.close()


def delete_comparison_report(
    comparison_id: int, owner_address: str,
) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM comparison_reports WHERE id = ? AND owner_address = ?",
            (comparison_id, owner_address),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ============ 用户 ============

def upsert_user(address: str, nickname: str = None) -> dict:
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT * FROM users WHERE address = ?", (address,)
        ).fetchone()
        if existing:
            return dict(existing)
        conn.execute(
            "INSERT INTO users (address, nickname) VALUES (?, ?)",
            (address, nickname or f"User_{address[:6]}"),
        )
        conn.commit()
        return dict(conn.execute(
            "SELECT * FROM users WHERE address = ?", (address,)
        ).fetchone())
    finally:
        conn.close()


def update_user(address: str, nickname: str = None, avatar: str = None, bio: str = None):
    conn = get_connection()
    try:
        fields = []
        params = []
        if nickname is not None:
            fields.append("nickname = ?")
            params.append(nickname)
        if avatar is not None:
            fields.append("avatar = ?")
            params.append(avatar)
        if bio is not None:
            fields.append("bio = ?")
            params.append(bio)
        if fields:
            params.append(address)
            conn.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE address = ?",
                params,
            )
            conn.commit()
    finally:
        conn.close()


def get_user(address: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE address = ?", (address,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def search_users(query: str, limit: int = 6) -> list:
    conn = get_connection()
    try:
        normalized = query.strip().lstrip("@")
        rows = conn.execute(
            """SELECT address, nickname, avatar
               FROM users
               WHERE nickname LIKE ? OR address LIKE ?
               ORDER BY CASE WHEN lower(nickname) = lower(?) THEN 0 ELSE 1 END, created_at DESC
               LIMIT ?""",
            (f"%{normalized}%", f"%{normalized}%", normalized, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ============ 帖子 ============

def create_post(
    author: str, content: str, attached_analysis_id: int = None,
    image_data: str = None, parent_post_id: int = None,
) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO posts
               (author, content, attached_analysis_id, image_data, parent_post_id)
               VALUES (?, ?, ?, ?, ?)""",
            (author, content, attached_analysis_id, image_data, parent_post_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_timeline(user_address: str, limit: int = 20, offset: int = 0) -> list:
    conn = get_connection()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO post_views (post_id, user_address) VALUES (?, ?)",
            [
                (row["id"], user_address)
                for row in conn.execute(
                    """SELECT id FROM posts
                       WHERE parent_post_id IS NULL
                       ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
            ],
        )
        conn.commit()
        rows = conn.execute(
            f"""SELECT p.*, u.nickname as author_nickname, u.avatar as author_avatar,
                      q.content as quoted_content, q.author as quoted_author,
                      qu.nickname as quoted_author_nickname,
                      (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) as like_count,
                      (SELECT COUNT(*) FROM post_reposts WHERE original_post_id = p.id) as repost_count,
                      (SELECT COUNT(*) FROM posts WHERE parent_post_id = p.id) as reply_count,
                      (SELECT COUNT(*) FROM post_views WHERE post_id = p.id) as view_count,
                      EXISTS(SELECT 1 FROM post_likes
                             WHERE post_id = p.id AND user_address = ?) as liked,
                      EXISTS(SELECT 1 FROM post_reposts
                             WHERE original_post_id = p.id AND reposter = ? AND quote_text IS NULL) as reposted,
                      EXISTS(SELECT 1 FROM post_bookmarks
                             WHERE post_id = p.id AND user_address = ?) as bookmarked
               FROM posts p
               JOIN users u ON p.author = u.address
               LEFT JOIN posts q ON p.quoted_post_id = q.id
               LEFT JOIN users qu ON q.author = qu.address
               WHERE p.parent_post_id IS NULL
               ORDER BY (
                   (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) * 3 +
                   (SELECT COUNT(*) FROM post_reposts WHERE original_post_id = p.id) * 4 +
                   (SELECT COUNT(*) FROM posts WHERE parent_post_id = p.id) * 2
               ) DESC, p.created_at DESC
               LIMIT ? OFFSET ?""",
            (user_address, user_address, user_address, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_following_timeline(user_address: str, limit: int = 20, offset: int = 0) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""SELECT p.*, u.nickname as author_nickname, u.avatar as author_avatar,
                      q.content as quoted_content, q.author as quoted_author,
                      qu.nickname as quoted_author_nickname,
                      (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) as like_count,
                      (SELECT COUNT(*) FROM post_reposts WHERE original_post_id = p.id) as repost_count,
                      (SELECT COUNT(*) FROM posts WHERE parent_post_id = p.id) as reply_count,
                      (SELECT COUNT(*) FROM post_views WHERE post_id = p.id) as view_count,
                      EXISTS(SELECT 1 FROM post_likes
                             WHERE post_id = p.id AND user_address = ?) as liked,
                      EXISTS(SELECT 1 FROM post_reposts
                             WHERE original_post_id = p.id AND reposter = ? AND quote_text IS NULL) as reposted,
                      EXISTS(SELECT 1 FROM post_bookmarks
                             WHERE post_id = p.id AND user_address = ?) as bookmarked
               FROM posts p
               JOIN users u ON p.author = u.address
               LEFT JOIN posts q ON p.quoted_post_id = q.id
               LEFT JOIN users qu ON q.author = qu.address
               WHERE p.parent_post_id IS NULL
                 AND (p.author = ? OR p.author IN (
                     SELECT following FROM {FOLLOW_TABLE} WHERE follower = ?
                 ))
               ORDER BY p.created_at DESC
               LIMIT ? OFFSET ?""",
            (
                user_address, user_address, user_address,
                user_address, user_address, limit, offset,
            ),
        ).fetchall()
        ids = [row["id"] for row in rows]
        conn.executemany(
            "INSERT OR IGNORE INTO post_views (post_id, user_address) VALUES (?, ?)",
            [(post_id, user_address) for post_id in ids],
        )
        conn.commit()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_user_posts(address: str, limit: int = 20, offset: int = 0, viewer: str = "") -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT p.*, u.nickname as author_nickname, u.avatar as author_avatar,
                      q.content as quoted_content, q.author as quoted_author,
                      qu.nickname as quoted_author_nickname,
                      (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) as like_count,
                      (SELECT COUNT(*) FROM post_reposts WHERE original_post_id = p.id) as repost_count,
                      (SELECT COUNT(*) FROM posts WHERE parent_post_id = p.id) as reply_count,
                      (SELECT COUNT(*) FROM post_views WHERE post_id = p.id) as view_count,
                      EXISTS(SELECT 1 FROM post_likes
                             WHERE post_id = p.id AND user_address = ?) as liked,
                      EXISTS(SELECT 1 FROM post_reposts
                             WHERE original_post_id = p.id AND reposter = ? AND quote_text IS NULL) as reposted,
                      EXISTS(SELECT 1 FROM post_bookmarks
                             WHERE post_id = p.id AND user_address = ?) as bookmarked
               FROM posts p
               JOIN users u ON p.author = u.address
               LEFT JOIN posts q ON p.quoted_post_id = q.id
               LEFT JOIN users qu ON q.author = qu.address
               WHERE p.author = ? AND p.parent_post_id IS NULL
               ORDER BY p.created_at DESC
               LIMIT ? OFFSET ?""",
            (viewer, viewer, viewer, address, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_post(post_id: int, author: str) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM posts WHERE id = ? AND author = ?", (post_id, author)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ============ 社交操作 ============

def toggle_like(post_id: int, user_address: str) -> bool:
    """返回 True 表示已点赞，False 表示已取消"""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM post_likes WHERE post_id = ? AND user_address = ?",
            (post_id, user_address),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM post_likes WHERE post_id = ? AND user_address = ?",
                (post_id, user_address),
            )
            conn.commit()
            return False
        else:
            conn.execute(
                "INSERT INTO post_likes (post_id, user_address) VALUES (?, ?)",
                (post_id, user_address),
            )
            conn.commit()
            return True
    finally:
        conn.close()


def toggle_repost(original_post_id: int, reposter: str) -> bool:
    """普通转发开关；不能转发自己的帖子。"""
    conn = get_connection()
    try:
        post = conn.execute(
            "SELECT author FROM posts WHERE id = ?", (original_post_id,)
        ).fetchone()
        if not post:
            raise ValueError("Post not found")
        if post["author"].lower() == reposter.lower():
            raise PermissionError("You cannot repost your own post")
        existing = conn.execute(
            """SELECT 1 FROM post_reposts
               WHERE original_post_id = ? AND reposter = ? AND quote_text IS NULL""",
            (original_post_id, reposter),
        ).fetchone()
        if existing:
            conn.execute(
                """DELETE FROM post_reposts
                   WHERE original_post_id = ? AND reposter = ? AND quote_text IS NULL""",
                (original_post_id, reposter),
            )
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO post_reposts (original_post_id, reposter, quote_text) VALUES (?, ?, NULL)",
            (original_post_id, reposter),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def create_quote(original_post_id: int, reposter: str, quote_text: str) -> int:
    conn = get_connection()
    try:
        if not quote_text or not quote_text.strip():
            raise ValueError("Quote text cannot be empty")
        post = conn.execute("SELECT id FROM posts WHERE id = ?", (original_post_id,)).fetchone()
        if not post:
            raise ValueError("Post not found")
        cursor = conn.execute(
            "INSERT INTO posts (author, content, quoted_post_id) VALUES (?, ?, ?)",
            (reposter, quote_text.strip(), original_post_id),
        )
        conn.execute(
            "INSERT INTO post_reposts (original_post_id, reposter, quote_text) VALUES (?, ?, ?)",
            (original_post_id, reposter, quote_text.strip()),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_post_detail(post_id: int, viewer: str) -> Optional[dict]:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO post_views (post_id, user_address) VALUES (?, ?)",
            (post_id, viewer),
        )
        conn.commit()
        row = conn.execute(
            """SELECT p.*, u.nickname as author_nickname, u.avatar as author_avatar,
                      q.content as quoted_content, q.author as quoted_author,
                      qu.nickname as quoted_author_nickname,
                      (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) as like_count,
                      (SELECT COUNT(*) FROM post_reposts WHERE original_post_id = p.id) as repost_count,
                      (SELECT COUNT(*) FROM posts WHERE parent_post_id = p.id) as reply_count,
                      (SELECT COUNT(*) FROM post_views WHERE post_id = p.id) as view_count,
                      EXISTS(SELECT 1 FROM post_likes
                             WHERE post_id = p.id AND user_address = ?) as liked,
                      EXISTS(SELECT 1 FROM post_reposts
                             WHERE original_post_id = p.id AND reposter = ? AND quote_text IS NULL) as reposted,
                      EXISTS(SELECT 1 FROM post_bookmarks
                             WHERE post_id = p.id AND user_address = ?) as bookmarked
               FROM posts p
               JOIN users u ON p.author = u.address
               LEFT JOIN posts q ON p.quoted_post_id = q.id
               LEFT JOIN users qu ON q.author = qu.address
               WHERE p.id = ?""",
            (viewer, viewer, viewer, post_id),
        ).fetchone()
        if not row:
            return None
        post = dict(row)
        replies = conn.execute(
            """SELECT p.*, u.nickname as author_nickname, u.avatar as author_avatar,
                      (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) as like_count,
                      (SELECT COUNT(*) FROM post_reposts WHERE original_post_id = p.id) as repost_count,
                      (SELECT COUNT(*) FROM posts WHERE parent_post_id = p.id) as reply_count,
                      (SELECT COUNT(*) FROM post_views WHERE post_id = p.id) as view_count,
                      EXISTS(SELECT 1 FROM post_likes
                             WHERE post_id = p.id AND user_address = ?) as liked,
                      EXISTS(SELECT 1 FROM post_reposts
                             WHERE original_post_id = p.id AND reposter = ? AND quote_text IS NULL) as reposted,
                      EXISTS(SELECT 1 FROM post_bookmarks
                             WHERE post_id = p.id AND user_address = ?) as bookmarked
               FROM posts p JOIN users u ON p.author = u.address
               WHERE p.parent_post_id = ?
               ORDER BY p.created_at ASC""",
            (viewer, viewer, viewer, post_id),
        ).fetchall()
        post["replies"] = [dict(reply) for reply in replies]
        return post
    finally:
        conn.close()


def toggle_bookmark(post_id: int, user_address: str) -> bool:
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM post_bookmarks WHERE post_id = ? AND user_address = ?",
            (post_id, user_address),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM post_bookmarks WHERE post_id = ? AND user_address = ?",
                (post_id, user_address),
            )
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO post_bookmarks (post_id, user_address) VALUES (?, ?)",
            (post_id, user_address),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_bookmarked_posts(user_address: str, limit: int = 50) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT p.*, u.nickname as author_nickname, u.avatar as author_avatar,
                      q.content as quoted_content, q.author as quoted_author,
                      qu.nickname as quoted_author_nickname,
                      (SELECT COUNT(*) FROM post_likes WHERE post_id = p.id) as like_count,
                      (SELECT COUNT(*) FROM post_reposts WHERE original_post_id = p.id) as repost_count,
                      (SELECT COUNT(*) FROM posts WHERE parent_post_id = p.id) as reply_count,
                      (SELECT COUNT(*) FROM post_views WHERE post_id = p.id) as view_count,
                      EXISTS(SELECT 1 FROM post_likes
                             WHERE post_id = p.id AND user_address = ?) as liked,
                      EXISTS(SELECT 1 FROM post_reposts
                             WHERE original_post_id = p.id AND reposter = ? AND quote_text IS NULL) as reposted,
                      1 as bookmarked
               FROM post_bookmarks b
               JOIN posts p ON p.id = b.post_id
               JOIN users u ON p.author = u.address
               LEFT JOIN posts q ON p.quoted_post_id = q.id
               LEFT JOIN users qu ON q.author = qu.address
               WHERE b.user_address = ?
               ORDER BY b.created_at DESC LIMIT ?""",
            (user_address, user_address, user_address, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def toggle_follow(follower: str, following: str) -> bool:
    """返回 True 表示已关注，False 表示已取消"""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT 1 FROM user_follows WHERE follower = ? AND following = ?",
            (follower, following),
        ).fetchone()
        if existing:
            conn.execute(
                "DELETE FROM user_follows WHERE follower = ? AND following = ?",
                (follower, following),
            )
            conn.commit()
            return False
        else:
            conn.execute(
                "INSERT INTO user_follows (follower, following) VALUES (?, ?)",
                (follower, following),
            )
            conn.commit()
            return True
    finally:
        conn.close()


def get_follow_counts(address: str) -> dict:
    conn = get_connection()
    try:
        following = conn.execute(
            "SELECT COUNT(*) FROM user_follows WHERE follower = ?", (address,)
        ).fetchone()[0]
        followers = conn.execute(
            "SELECT COUNT(*) FROM user_follows WHERE following = ?", (address,)
        ).fetchone()[0]
        return {"following": following, "followers": followers}
    finally:
        conn.close()


def is_following(follower: str, following: str) -> bool:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT 1 FROM user_follows WHERE follower = ? AND following = ?",
            (follower, following),
        ).fetchone() is not None
    finally:
        conn.close()


# ============ NFT ============

def save_nft_record(
    token_id: str, contract_address: str, chain: str,
    minter: str, analysis_id: int, token_uri: str, tx_hash: str,
    poster_image: str = None, poster_style: str = None, poster_uid: str = None,
) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO poster_nfts
               (token_id, contract_address, chain, minter, analysis_id, token_uri,
                poster_image, poster_style, poster_uid, tx_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                token_id, contract_address, chain, minter, analysis_id, token_uri,
                poster_image, poster_style, poster_uid, tx_hash,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_user_nfts(address: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT n.*, a.token_name, a.token_symbol, a.chain AS token_chain
               FROM poster_nfts n
               LEFT JOIN analysis_records a ON a.id = n.analysis_id
               WHERE n.minter = ? AND COALESCE(n.hidden, 0) = 0
               ORDER BY n.created_at DESC""",
            (address,),
        ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            if not item.get("poster_image") and item.get("token_uri"):
                try:
                    metadata = json.loads(item["token_uri"])
                    item["poster_image"] = metadata.get("image")
                    item["poster_uid"] = metadata.get("poster_id")
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            items.append(item)
        return items
    finally:
        conn.close()


def update_nft_display(
    record_id: int, minter: str, display_name: str | None, category: str | None,
) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """UPDATE poster_nfts
               SET display_name = COALESCE(?, display_name),
                   category = COALESCE(?, category)
               WHERE id = ? AND minter = ? AND COALESCE(hidden, 0) = 0""",
            (display_name, category, record_id, minter),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def hide_nft_record(record_id: int, minter: str) -> bool:
    """Hide a local gallery record. This does not destroy the on-chain token."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE poster_nfts SET hidden = 1 WHERE id = ? AND minter = ?",
            (record_id, minter),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def confirm_nft_record(record_id: int, minter: str, token_id: str) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """UPDATE poster_nfts SET token_id = ?
               WHERE id = ? AND minter = ? AND token_id = 'pending'""",
            (token_id, record_id, minter),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ============ 自选列表 ============

def add_to_watchlist(
    owner_address: str, token_name: str, chain: str = "unknown",
    token_symbol: str = None, contract_addr: str = None,
) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO watchlist
               (token_name, token_symbol, contract_addr, chain, owner_address)
               VALUES (?, ?, ?, ?, ?)""",
            (token_name, token_symbol, contract_addr, chain, owner_address),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_watchlist(owner_address: str) -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM watchlist WHERE owner_address = ? ORDER BY sort_order, id",
            (owner_address,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_watchlist_item(item_id: int, owner_address: str) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM watchlist WHERE id = ? AND owner_address = ?",
            (item_id, owner_address),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def batch_delete_watchlist(ids: list[int], owner_address: str) -> int:
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        cursor = conn.execute(
            f"DELETE FROM watchlist WHERE owner_address = ? AND id IN ({placeholders})",
            [owner_address, *ids],
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def update_watchlist_order(ordered_ids: list[int], owner_address: str):
    conn = get_connection()
    try:
        for i, item_id in enumerate(ordered_ids):
            conn.execute(
                """UPDATE watchlist SET sort_order = ?
                   WHERE id = ? AND owner_address = ?""",
                (i, item_id, owner_address),
            )
        conn.commit()
    finally:
        conn.close()


def update_watchlist_note(item_id: int, notes: str, owner_address: str):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE watchlist SET notes = ? WHERE id = ? AND owner_address = ?",
            (notes, item_id, owner_address),
        )
        conn.commit()
    finally:
        conn.close()
