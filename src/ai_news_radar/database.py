"""SQLite database layer for AI News Radar."""

import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import get_database_path
from .models import Article, Source, SourceType, Sentiment, Category, DailyReport

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    source_type TEXT NOT NULL UNIQUE,
    base_url    TEXT NOT NULL DEFAULT '',
    enabled     INTEGER DEFAULT 1,
    last_fetch  TEXT,
    fetch_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS articles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type   TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL UNIQUE,
    author        TEXT,
    published_at  TEXT,
    fetched_at    TEXT NOT NULL DEFAULT (datetime('now')),
    summary       TEXT,
    category      TEXT DEFAULT 'unknown',
    tags          TEXT DEFAULT '[]',
    importance    REAL DEFAULT 0.0,
    sentiment     TEXT DEFAULT 'neutral',
    is_analyzed   INTEGER DEFAULT 0,
    raw_content   TEXT,
    score         INTEGER,
    comment_count INTEGER,
    metadata      TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_type   TEXT NOT NULL,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    title         TEXT DEFAULT '',
    content       TEXT NOT NULL DEFAULT '',
    article_count INTEGER DEFAULT 0,
    top_articles  TEXT DEFAULT '[]',
    source_summary TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_type);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_importance ON articles(importance DESC);
CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url);
CREATE INDEX IF NOT EXISTS idx_articles_analyzed ON articles(is_analyzed);
CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(generated_at);

-- ─── User & Stock Tracking ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    feishu_id      TEXT NOT NULL UNIQUE,
    username       TEXT NOT NULL DEFAULT '',
    webhook_url    TEXT NOT NULL DEFAULT '',    -- Feishu/个人通知地址
    feishu_open_id TEXT NOT NULL DEFAULT '',    -- 飞书用户 open_id（用于私聊推送）
    notify_enabled INTEGER NOT NULL DEFAULT 0, -- 是否开启推送 0=关 1=开
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_stocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_code    TEXT NOT NULL,       -- e.g. "sh600589"
    stock_name    TEXT NOT NULL DEFAULT '',
    buy_price     REAL,                -- 买入均价（可选）
    quantity      INTEGER DEFAULT 0,   -- 持仓数量（可选）
    watched       INTEGER NOT NULL DEFAULT 1,  -- 是否推送关注 1=关注 0=不关注
    daily_notify  INTEGER NOT NULL DEFAULT 0,  -- 是否推送每日复盘报告
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    notes         TEXT DEFAULT '',
    UNIQUE(user_id, stock_code)
);

CREATE TABLE IF NOT EXISTS stock_transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stock_code       TEXT NOT NULL,
    transaction_type TEXT NOT NULL CHECK(transaction_type IN ('buy', 'sell')),
    price            REAL NOT NULL,
    quantity         INTEGER NOT NULL,
    transaction_date TEXT NOT NULL DEFAULT (datetime('now')),
    notes            TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_user_stocks_user ON user_stocks(user_id);
CREATE INDEX IF NOT EXISTS idx_user_stocks_code ON user_stocks(stock_code);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON stock_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_code ON stock_transactions(stock_code);

-- ─── Group Chat Notification ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS group_chats (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id        TEXT NOT NULL UNIQUE,     -- 飞书群聊 chat_id
    group_name     TEXT NOT NULL DEFAULT '', -- 群名称（便于识别）
    enabled        INTEGER NOT NULL DEFAULT 1,  -- 是否启用群通知
    module         TEXT NOT NULL DEFAULT 'default', -- 关联模块（后续可扩展）
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_group_chats_module ON group_chats(module);

-- ─── WeChat Articles & Stock References ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS wechat_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account     TEXT NOT NULL,           -- 公众号名称
    title       TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL UNIQUE,
    content     TEXT DEFAULT '',          -- 文章全文
    posted_at   TEXT,                     -- 文章发布时间
    fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
    is_analyzed INTEGER NOT NULL DEFAULT 0,
    summary     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS article_stock_refs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      INTEGER NOT NULL REFERENCES wechat_articles(id) ON DELETE CASCADE,
    stock_code      TEXT NOT NULL,          -- 匹配到的股票代码
    stock_name      TEXT NOT NULL DEFAULT '', -- 原文中简称
    stock_full_name TEXT NOT NULL DEFAULT '', -- 全称
    section         TEXT DEFAULT '',         -- 所属板块/分类
    mention_snippet TEXT DEFAULT '',         -- 原文上下文
    mention_type    TEXT DEFAULT 'mentioned', -- mentioned/recommended/key
    confidence      REAL DEFAULT 0.8,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_wechat_account ON wechat_articles(account);
CREATE INDEX IF NOT EXISTS idx_wechat_posted ON wechat_articles(posted_at);
CREATE INDEX IF NOT EXISTS idx_article_stock_code ON article_stock_refs(stock_code);
CREATE INDEX IF NOT EXISTS idx_article_ref_article ON article_stock_refs(article_id);

-- Stock Tracking (15-day tracking from 公众号)
CREATE TABLE IF NOT EXISTS stock_tracking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code      TEXT NOT NULL,
    stock_name      TEXT NOT NULL DEFAULT '',
    source_account  TEXT NOT NULL DEFAULT '凡尘一灯',
    article_id      INTEGER REFERENCES wechat_articles(id),
    track_started_at TEXT NOT NULL DEFAULT (datetime('now')),
    day1_price      REAL,
    day1_change_pct REAL,
    day1_trend      TEXT DEFAULT '',
    day1_suggestion TEXT DEFAULT '',
    sqsm_history    TEXT DEFAULT '[]',      -- SQS Monitor 历史评分 (JSON array)
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(stock_code, source_account)
);
CREATE INDEX IF NOT EXISTS idx_tracking_account ON stock_tracking(source_account);
CREATE INDEX IF NOT EXISTS idx_tracking_started ON stock_tracking(track_started_at);
"""


class Database:
    """SQLite database manager for AI News Radar."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or get_database_path()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.executescript(SCHEMA_SQL)
            # Migration: add webhook_url column if missing
            try:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN webhook_url TEXT NOT NULL DEFAULT ''"
                )
                logger.info("Migration: added webhook_url column to users table")
            except Exception:
                pass  # Column already exists
            # Migration: add notify_enabled column if missing
            try:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN notify_enabled INTEGER NOT NULL DEFAULT 0"
                )
                logger.info("Migration: added notify_enabled column to users table")
            except Exception:
                pass  # Column already exists
            # Migration: add watched column to user_stocks if missing
            try:
                conn.execute(
                    "ALTER TABLE user_stocks ADD COLUMN watched INTEGER NOT NULL DEFAULT 1"
                )
                logger.info("Migration: added watched column to user_stocks table")
            except Exception:
                pass  # Column already exists
            # Migration: add daily_notify column (per-stock daily report toggle)
            try:
                conn.execute(
                    "ALTER TABLE user_stocks ADD COLUMN daily_notify INTEGER NOT NULL DEFAULT 0"
                )
                logger.info("Migration: added daily_notify column to user_stocks table")
            except Exception:
                pass  # Column already exists
            # Migration: add feishu_open_id column if missing
            try:
                conn.execute(
                    "ALTER TABLE users ADD COLUMN feishu_open_id TEXT NOT NULL DEFAULT ''"
                )
                logger.info("Migration: added feishu_open_id column to users table")
            except Exception:
                pass  # Column already exists
            # Migration: add sqsm_history column to stock_tracking
            try:
                conn.execute(
                    "ALTER TABLE stock_tracking ADD COLUMN sqsm_history TEXT DEFAULT '[]'"
                )
                logger.info("Migration: added sqsm_history column to stock_tracking table")
            except Exception:
                pass  # Column already exists
        logger.info("Database initialized at %s", self.db_path)

        # ─── Strategy Signal Tracking (十全十美 / 主力捉妖) ────────
        self._ensure_strategy_tables()

    def _ensure_strategy_tables(self):
        """Create strategy signal tracking tables."""
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS strategy_signals ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "strategy_type TEXT NOT NULL,"
                    "stock_code TEXT NOT NULL,"
                    "stock_name TEXT NOT NULL DEFAULT '',"
                    "price REAL NOT NULL,"
                    "score TEXT DEFAULT '',"
                    "signal_date TEXT NOT NULL,"
                    "status TEXT NOT NULL DEFAULT 'active',"
                    "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_signals_strategy ON strategy_signals(strategy_type)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_signals_date ON strategy_signals(signal_date)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_signals_status ON strategy_signals(status)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS strategy_signal_tracking ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    "signal_id INTEGER NOT NULL REFERENCES strategy_signals(id) ON DELETE CASCADE,"
                    "track_date TEXT NOT NULL DEFAULT (date('now')),"
                    "price REAL NOT NULL,"
                    "high REAL DEFAULT 0,"
                    "low REAL DEFAULT 0,"
                    "change_pct REAL DEFAULT 0,"
                    "peak_pct REAL DEFAULT 0,"
                    "drawdown_pct REAL DEFAULT 0,"
                    "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tracking_signal ON strategy_signal_tracking(signal_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tracking_date ON strategy_signal_tracking(track_date)"
                )
        except Exception as e:
            logger.warning("Strategy tables init error: %s", e)

    # --- Source Operations ---

    def ensure_source(self, source_type: str, name: str, base_url: str = ""):
        """Get or create a source record."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sources WHERE source_type = ?", (source_type,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO sources (name, source_type, base_url) VALUES (?, ?, ?)",
                    (name, source_type, base_url),
                )
                conn.commit()

    def update_source_fetch(self, source_type: str, fetch_count: int = 0) -> None:
        """Update last_fetch timestamp and increment fetch count."""
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sources SET last_fetch = datetime('now'), "
                "fetch_count = fetch_count + ? WHERE source_type = ?",
                (fetch_count, source_type),
            )
            conn.commit()

    # --- Article Operations ---

    def article_exists(self, url: str) -> bool:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM articles WHERE url = ?", (url,)
            ).fetchone()
            return row is not None

    def insert_article(self, article: Article) -> Optional[int]:
        """Insert a new article. Returns the new ID or None if duplicate."""
        if self.article_exists(article.url):
            return None
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO articles
                   (source_type, title, url, author, published_at, summary, category,
                    tags, importance, sentiment, is_analyzed, raw_content, score,
                    comment_count, metadata)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    article.source_type.value,
                    article.title,
                    article.url,
                    article.author,
                    article.published_at.isoformat() if article.published_at else None,
                    article.summary,
                    article.category.value,
                    json.dumps(article.tags, ensure_ascii=False),
                    article.importance,
                    article.sentiment.value,
                    int(article.is_analyzed),
                    article.raw_content,
                    article.score,
                    article.comment_count,
                    json.dumps(article.metadata, ensure_ascii=False),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def insert_articles_batch(self, articles: list[Article]) -> int:
        """Insert multiple articles. Returns count of newly inserted."""
        count = 0
        for article in articles:
            if self.insert_article(article) is not None:
                count += 1
        return count

    def get_unanalyzed_articles(self, limit: int = 50) -> list[Article]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM articles WHERE is_analyzed = 0 "
                "ORDER BY fetched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_article(r) for r in rows]

    def get_articles(
        self,
        source_type: Optional[str] = None,
        category: Optional[str] = None,
        min_importance: float = 0.0,
        sentiment: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        analyzed_only: bool = False,
    ) -> list[Article]:
        query = "SELECT * FROM articles WHERE 1=1"
        params: list = []

        if source_type:
            query += " AND source_type = ?"
            params.append(source_type)
        if category:
            query += " AND category = ?"
            params.append(category)
        if min_importance > 0:
            query += " AND importance >= ?"
            params.append(min_importance)
        if sentiment:
            query += " AND sentiment = ?"
            params.append(sentiment)
        if analyzed_only:
            query += " AND is_analyzed = 1"

        query += " ORDER BY importance DESC, published_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_article(r) for r in rows]

    def get_today_articles(self, limit: int = 50, source_filter: Optional[list[str]] = None) -> list[Article]:
        with self._get_conn() as conn:
            if source_filter:
                placeholders = ",".join("?" for _ in source_filter)
                query = (
                    f"SELECT * FROM articles WHERE date(fetched_at) = date('now') "
                    f"AND source_type IN ({placeholders}) "
                    f"ORDER BY importance DESC, published_at DESC LIMIT ?"
                )
                rows = conn.execute(query, (*source_filter, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM articles WHERE date(fetched_at) = date('now') "
                    "ORDER BY importance DESC, published_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_article(r) for r in rows]

    def get_articles_since(self, since: datetime, limit: int = 100) -> list[Article]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM articles WHERE fetched_at >= ? "
                "ORDER BY importance DESC, published_at DESC LIMIT ?",
                (since.isoformat(), limit),
            ).fetchall()
            return [self._row_to_article(r) for r in rows]

    def update_article_analysis(self, article: Article) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE articles SET
                    summary = ?, category = ?, tags = ?, importance = ?,
                    sentiment = ?, is_analyzed = 1
                 WHERE id = ?""",
                (
                    article.summary,
                    article.category.value,
                    json.dumps(article.tags, ensure_ascii=False),
                    article.importance,
                    article.sentiment.value,
                    article.id,
                ),
            )
            conn.commit()

    def _row_to_article(self, row: sqlite3.Row) -> Article:
        return Article(
            id=row["id"],
            source_type=SourceType(row["source_type"]),
            title=row["title"],
            url=row["url"],
            author=row["author"],
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"] else None
            ),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            summary=row["summary"],
            category=Category(row["category"] or "unknown"),
            tags=json.loads(row["tags"] or "[]"),
            importance=row["importance"] or 0.0,
            sentiment=Sentiment(row["sentiment"] or "neutral"),
            is_analyzed=bool(row["is_analyzed"]),
            raw_content=row["raw_content"],
            score=row["score"],
            comment_count=row["comment_count"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    # --- Report Operations ---

    def save_report(self, report: DailyReport) -> int:
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO reports
                   (report_type, title, content, article_count,
                    top_articles, source_summary)
                 VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    report.report_type,
                    report.title,
                    report.content,
                    report.article_count,
                    json.dumps([a.id for a in report.top_articles], ensure_ascii=False),
                    json.dumps(report.source_summary, ensure_ascii=False),
                ),
            )
            conn.commit()
            return cursor.lastrowid

    def get_latest_report(self, report_type: str = "daily") -> Optional[DailyReport]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM reports WHERE report_type = ? "
                "ORDER BY generated_at DESC LIMIT 1",
                (report_type,),
            ).fetchone()
            if row is None:
                return None
            return DailyReport(
                id=row["id"],
                report_type=row["report_type"],
                generated_at=datetime.fromisoformat(row["generated_at"]),
                title=row["title"] or "",
                content=row["content"],
                article_count=row["article_count"],
                top_articles=[],
                source_summary=json.loads(row["source_summary"] or "{}"),
            )

    # --- Stats ---

    def get_stats(self) -> dict:
        with self._get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            analyzed = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE is_analyzed = 1"
            ).fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM articles "
                "WHERE date(fetched_at) = date('now')"
            ).fetchone()[0]
            by_source = {}
            rows = conn.execute(
                "SELECT source_type, COUNT(*) as cnt "
                "FROM articles GROUP BY source_type"
            ).fetchall()
            for r in rows:
                by_source[r["source_type"]] = r["cnt"]
            return {
                "total_articles": total,
                "analyzed_articles": analyzed,
                "articles_today": today,
                "by_source": by_source,
            }

    # ─── Strategy Signal Tracking ──────────────────────────────────

    def record_strategy_signal(self, strategy_type: str, stock_code: str, stock_name: str,
                                price: float, score: str = "") -> int:
        """Record a new strategy signal. Returns signal_id."""
        with self._get_conn() as conn:
            today = datetime.now().strftime("%Y-%m-%d")
            cursor = conn.execute(
                """INSERT INTO strategy_signals
                   (strategy_type, stock_code, stock_name, price, score, signal_date)
                 VALUES (?, ?, ?, ?, ?, ?)""",
                (strategy_type, stock_code, stock_name, price, score, today),
            )
            conn.commit()
            return cursor.lastrowid

    def get_active_signals(self) -> list[dict]:
        """Get all active signals (within 60-day tracking window)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM strategy_signals
                   WHERE status = 'active'
                     AND signal_date >= date('now', '-60 days')
                   ORDER BY signal_date DESC, id DESC""",
            ).fetchall()
            return [dict(r) for r in rows]

    def get_active_signals_by_strategy(self, strategy_type: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM strategy_signals
                   WHERE strategy_type = ? AND status = 'active'
                     AND signal_date >= date('now', '-60 days')
                   ORDER BY signal_date DESC, id DESC""",
                (strategy_type,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_signal_tracking(self, signal_id: int, price: float,
                                high: float = 0, low: float = 0,
                                change_pct: float = 0,
                                peak_pct: float = 0, drawdown_pct: float = 0) -> bool:
        """Record today's tracking data for a signal."""
        with self._get_conn() as conn:
            today = datetime.now().strftime("%Y-%m-%d")
            # Upsert: update if today's record exists, insert if not
            existing = conn.execute(
                "SELECT id FROM strategy_signal_tracking WHERE signal_id = ? AND track_date = ?",
                (signal_id, today),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE strategy_signal_tracking
                       SET price = ?, high = ?, low = ?, change_pct = ?,
                           peak_pct = ?, drawdown_pct = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (price, high, low, change_pct, peak_pct, drawdown_pct, existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO strategy_signal_tracking
                       (signal_id, track_date, price, high, low, change_pct, peak_pct, drawdown_pct)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (signal_id, today, price, high, low, change_pct, peak_pct, drawdown_pct),
                )
            conn.commit()
            return True

    def get_signal_tracking(self, signal_id: int) -> list[dict]:
        """Get all tracking records for a signal."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM strategy_signal_tracking WHERE signal_id = ? ORDER BY track_date",
                (signal_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_signal_tracking(self, signal_id: int) -> Optional[dict]:
        """Get the latest tracking record for a signal."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_signal_tracking WHERE signal_id = ? ORDER BY track_date DESC LIMIT 1",
                (signal_id,),
            ).fetchone()
            return dict(row) if row else None

    def expire_old_signals(self) -> int:
        """Mark signals older than 60 days as expired. Returns count expired."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE strategy_signals SET status = 'expired' "
                "WHERE status = 'active' AND signal_date < date('now', '-60 days')"
            )
            conn.commit()
            return cursor.rowcount

    def get_signal_report(self, strategy_type: Optional[str] = None,
                           since_days: int = 7) -> dict:
        """Generate a performance summary for signals within N days."""
        with self._get_conn() as conn:
            if strategy_type:
                rows = conn.execute(
                    """SELECT s.*, t.price as last_price, t.change_pct, t.peak_pct, t.drawdown_pct
                       FROM strategy_signals s
                       LEFT JOIN strategy_signal_tracking t ON t.id = (
                           SELECT id FROM strategy_signal_tracking
                           WHERE signal_id = s.id ORDER BY track_date DESC LIMIT 1
                       )
                       WHERE s.strategy_type = ? AND s.signal_date >= date('now', ?)
                       ORDER BY s.signal_date DESC""",
                    (strategy_type, f'-{since_days} days'),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT s.*, t.price as last_price, t.change_pct, t.peak_pct, t.drawdown_pct
                       FROM strategy_signals s
                       LEFT JOIN strategy_signal_tracking t ON t.id = (
                           SELECT id FROM strategy_signal_tracking
                           WHERE signal_id = s.id ORDER BY track_date DESC LIMIT 1
                       )
                       WHERE s.signal_date >= date('now', ?)
                       ORDER BY s.signal_date DESC""",
                    (f'-{since_days} days',),
                ).fetchall()
            return [dict(r) for r in rows]

    # ─── Database Cleanup ───────────────────────────────────────

    def cleanup_old_data(self) -> dict:
        """Purge old data to keep the database lean.

        Rules:
          - Articles > 30 days old (old pipeline data)
          - Reports > 60 days old
          - Old wechat_articles and refs > 60 days
          - strategy_signal_tracking for expired signals
          - Then VACUUM to reclaim space
        """
        stats = {}
        with self._get_conn() as conn:
            # Old articles (old pipeline, > 30 days)
            stats["articles"] = conn.execute(
                "DELETE FROM articles WHERE fetched_at < datetime('now', '-30 days')"
            ).rowcount

            # Old reports (> 60 days)
            stats["reports"] = conn.execute(
                "DELETE FROM reports WHERE generated_at < datetime('now', '-60 days')"
            ).rowcount

            # Expire old strategy signals
            stats["signals_expired"] = conn.execute(
                "UPDATE strategy_signals SET status = 'expired' "
                "WHERE status = 'active' AND signal_date < date('now', '-60 days')"
            ).rowcount

            # Purge tracking details for expired signals
            stats["tracking_purged"] = conn.execute(
                "DELETE FROM strategy_signal_tracking WHERE signal_id IN "
                "(SELECT id FROM strategy_signals WHERE status = 'expired')"
            ).rowcount

            # Old wechat articles (> 60 days, keep recent for reference)
            old_ids = conn.execute(
                "SELECT id FROM wechat_articles WHERE fetched_at < datetime('now', '-60 days')"
            ).fetchall()
            old_id_list = [r["id"] for r in old_ids]
            if old_id_list:
                placeholders = ",".join("?" for _ in old_id_list)
                conn.execute(
                    f"DELETE FROM article_stock_refs WHERE article_id IN ({placeholders})",
                    old_id_list,
                )
                conn.execute(
                    f"DELETE FROM wechat_articles WHERE id IN ({placeholders})",
                    old_id_list,
                )
            stats["wechat_purged"] = len(old_id_list)

            # VACUUM to reclaim disk space (only if enough was deleted)
            total_deleted = sum(v for v in stats.values() if isinstance(v, int))
            if total_deleted > 100:
                conn.execute("VACUUM")
                stats["vacuumed"] = True
            else:
                stats["vacuumed"] = False

            conn.commit()

        logger.info("Database cleanup: %s", stats)
        return stats

    def close(self) -> None:
        pass  # SQLite connections are auto-closed via context manager

    # ─────────────────────────────────────────────
    # User & Stock Portfolio Operations
    # ─────────────────────────────────────────────

    def get_or_create_user(self, feishu_id: str, username: str = "") -> dict:
        """Get existing user or create a new one. Returns user dict."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE feishu_id = ?", (feishu_id,)
            ).fetchone()
            if row:
                if username and row["username"] != username:
                    conn.execute(
                        "UPDATE users SET username = ?, updated_at = datetime('now') WHERE id = ?",
                        (username, row["id"]),
                    )
                    conn.commit()
                return dict(row)
            cursor = conn.execute(
                "INSERT INTO users (feishu_id, username) VALUES (?, ?)",
                (feishu_id, username),
            )
            conn.commit()
            new_id = cursor.lastrowid
            row = conn.execute("SELECT * FROM users WHERE id = ?", (new_id,)).fetchone()
            return dict(row)

    def update_user_webhook(self, feishu_id: str, webhook_url: str) -> bool:
        """Set webhook URL for a user (for Feishu stock notifications)."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET webhook_url = ?, updated_at = datetime('now') WHERE feishu_id = ?",
                (webhook_url, feishu_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_user_open_id(self, feishu_id: str, open_id: str) -> bool:
        """Set Feishu open_id for a user (for private message delivery)."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET feishu_open_id = ?, updated_at = datetime('now') WHERE feishu_id = ?",
                (open_id, feishu_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_user_open_id(self, feishu_id: str) -> str:
        """Get Feishu open_id for a user."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT feishu_open_id FROM users WHERE feishu_id = ?", (feishu_id,)
            ).fetchone()
            return row["feishu_open_id"] if row and row["feishu_open_id"] else ""

    def set_notify_enabled(self, feishu_id: str, enabled: bool) -> bool:
        """Enable or disable stock notification push for a user."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE users SET notify_enabled = ?, updated_at = datetime('now') WHERE feishu_id = ?",
                (1 if enabled else 0, feishu_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def is_notify_enabled(self, feishu_id: str) -> bool:
        """Check if a user has notification enabled."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT notify_enabled FROM users WHERE feishu_id = ?", (feishu_id,)
            ).fetchone()
            return bool(row and row["notify_enabled"])

    def get_users_with_notify_enabled(self) -> list[dict]:
        """Get all users who have enabled notifications."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM users WHERE notify_enabled = 1 ORDER BY username"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_user_by_id(self, user_id: int) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return dict(row) if row else None

    def get_user_by_feishu(self, feishu_id: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE feishu_id = ?", (feishu_id,)
            ).fetchone()
            return dict(row) if row else None

    def add_user_stock(
        self,
        user_id: int,
        stock_code: str,
        stock_name: str = "",
        buy_price: Optional[float] = None,
        quantity: int = 0,
        notes: str = "",
    ) -> bool:
        """Add a stock to user's watchlist. Returns True if added, False if already exists."""
        with self._get_conn() as conn:
            try:
                conn.execute(
                    """INSERT INTO user_stocks
                       (user_id, stock_code, stock_name, buy_price, quantity, notes)
                     VALUES (?, ?, ?, ?, ?, ?)""",
                    (user_id, stock_code, stock_name, buy_price, quantity, notes),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def set_stock_watched(self, user_id: int, stock_code: str, watched: bool) -> bool:
        """Mark a stock as watched (push notifications) or unwatched."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE user_stocks SET watched = ? WHERE user_id = ? AND stock_code = ?",
                (1 if watched else 0, user_id, stock_code),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_user_watched_stocks(self, user_id: int) -> list[dict]:
        """Get only watched stocks (watched=1) for a user (for push notifications)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM user_stocks WHERE user_id = ? AND watched = 1 ORDER BY added_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stock_watch_status(self, user_id: int, stock_code: str) -> Optional[bool]:
        """Check if a specific stock is marked as watched. Returns None if not found."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT watched FROM user_stocks WHERE user_id = ? AND stock_code = ?",
                (user_id, stock_code),
            ).fetchone()
            if row is None:
                return None
            return bool(row["watched"])

    def remove_user_stock(self, user_id: int, stock_code: str) -> bool:
        """Remove a stock from user's watchlist."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM user_stocks WHERE user_id = ? AND stock_code = ?",
                (user_id, stock_code),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_user_stock(
        self,
        user_id: int,
        stock_code: str,
        buy_price: Optional[float] = None,
        quantity: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> bool:
        """Update buy price / quantity for a watched stock."""
        updates = []
        params = []
        if buy_price is not None:
            updates.append("buy_price = ?")
            params.append(buy_price)
        if quantity is not None:
            updates.append("quantity = ?")
            params.append(quantity)
        if notes is not None:
            updates.append("notes = ?")
            params.append(notes)
        if not updates:
            return False
        params.extend([user_id, stock_code])
        with self._get_conn() as conn:
            cursor = conn.execute(
                f"UPDATE user_stocks SET {', '.join(updates)} "
                "WHERE user_id = ? AND stock_code = ?",
                params,
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_user_stocks(self, user_id: int) -> list[dict]:
        """Get all stocks in user's watchlist."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM user_stocks WHERE user_id = ? ORDER BY added_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all_users_with_stocks(self) -> list[dict]:
        """Get all users and their stock counts (for admin)."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT u.*, COUNT(us.id) as stock_count "
                "FROM users u LEFT JOIN user_stocks us ON u.id = us.user_id "
                "GROUP BY u.id ORDER BY u.created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    # ─── Daily Report (Private Message) Controls ─────────────────

    def set_stock_daily_notify(self, user_id: int, stock_code: str, enabled: bool) -> bool:
        """Enable/disable daily report private notification for a specific stock."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE user_stocks SET daily_notify = ? WHERE user_id = ? AND stock_code = ?",
                (1 if enabled else 0, user_id, stock_code),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_user_daily_stocks(self, user_id: int) -> list[dict]:
        """Get stocks that have daily_notify enabled for a user."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM user_stocks WHERE user_id = ? AND daily_notify = 1 ORDER BY added_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_users_with_daily_notify(self) -> list[dict]:
        """Get all users who have at least one stock with daily_notify enabled
        AND have notify_enabled=1 (master switch) AND have a feishu_open_id."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT u.*, COUNT(us.id) as daily_stock_count "
                "FROM users u "
                "INNER JOIN user_stocks us ON u.id = us.user_id AND us.daily_notify = 1 "
                "WHERE u.notify_enabled = 1 AND u.feishu_open_id != '' "
                "GROUP BY u.id ORDER BY u.username"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_stock_daily_notify(self, user_id: int, stock_code: str) -> Optional[bool]:
        """Check if daily_notify is enabled for a specific stock. Returns None if not found."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT daily_notify FROM user_stocks WHERE user_id = ? AND stock_code = ?",
                (user_id, stock_code),
            ).fetchone()
            if row is None:
                return None
            return bool(row["daily_notify"])

    # ─── Transaction Recording ─────────────────

    def add_transaction(
        self,
        user_id: int,
        stock_code: str,
        transaction_type: str,  # "buy" or "sell"
        price: float,
        quantity: int,
        notes: str = "",
    ) -> int:
        """Record a buy/sell transaction. Returns transaction ID."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO stock_transactions
                   (user_id, stock_code, transaction_type, price, quantity, notes)
                 VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, stock_code, transaction_type, price, quantity, notes),
            )
            conn.commit()
            return cursor.lastrowid

    def get_transactions(
        self, user_id: int, stock_code: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        """Get transaction history for a user."""
        with self._get_conn() as conn:
            if stock_code:
                rows = conn.execute(
                    "SELECT * FROM stock_transactions "
                    "WHERE user_id = ? AND stock_code = ? "
                    "ORDER BY transaction_date DESC LIMIT ?",
                    (user_id, stock_code, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM stock_transactions "
                    "WHERE user_id = ? "
                    "ORDER BY transaction_date DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

    # ─── Group Chat Management ──────────────────────────────────────

    def add_group_chat(self, chat_id: str, group_name: str = "", module: str = "default") -> bool:
        """Register a Feishu group chat for notifications."""
        with self._get_conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO group_chats (chat_id, group_name, module) VALUES (?, ?, ?)",
                    (chat_id, group_name, module),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Already exists — update name
                conn.execute(
                    "UPDATE group_chats SET group_name = ?, updated_at = datetime('now') WHERE chat_id = ?",
                    (group_name, chat_id),
                )
                conn.commit()
                return True

    def remove_group_chat(self, chat_id: str) -> bool:
        """Unregister a Feishu group chat."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM group_chats WHERE chat_id = ?", (chat_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def set_group_chat_enabled(self, chat_id: str, enabled: bool) -> bool:
        """Enable or disable notifications for a group chat."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE group_chats SET enabled = ?, updated_at = datetime('now') WHERE chat_id = ?",
                (1 if enabled else 0, chat_id),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_group_chats(self, module: Optional[str] = None) -> list[dict]:
        """Get all registered group chats, optionally filtered by module."""
        with self._get_conn() as conn:
            if module:
                rows = conn.execute(
                    "SELECT * FROM group_chats WHERE module = ? ORDER BY created_at DESC",
                    (module,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM group_chats ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    def get_enabled_group_chats(self, module: Optional[str] = None) -> list[dict]:
        """Get only enabled group chats for sending notifications."""
        with self._get_conn() as conn:
            if module:
                rows = conn.execute(
                    "SELECT * FROM group_chats WHERE enabled = 1 AND module = ? ORDER BY created_at DESC",
                    (module,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM group_chats WHERE enabled = 1 ORDER BY created_at DESC"
                ).fetchall()
            return [dict(r) for r in rows]

    # ─── WeChat Article & Stock Reference ────────────────────────

    def save_wechat_article(self, account: str, title: str, url: str,
                            content: str, posted_at: Optional[str] = None) -> int:
        """Save a WeChat article. Returns article ID."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO wechat_articles
                   (account, title, url, content, posted_at)
                 VALUES (?, ?, ?, ?, ?)""",
                (account, title, url, content, posted_at or datetime.now().isoformat()),
            )
            conn.commit()
            if cursor.rowcount == 0:
                row = conn.execute("SELECT id FROM wechat_articles WHERE url = ?", (url,)).fetchone()
                return row["id"] if row else 0
            return cursor.lastrowid

    def save_article_stock_ref(self, article_id: int, stock_code: str,
                                stock_name: str = "", stock_full_name: str = "",
                                section: str = "", mention_snippet: str = "",
                                mention_type: str = "mentioned", confidence: float = 0.8) -> int:
        """Record a stock mentioned in a WeChat article. Returns ref ID."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO article_stock_refs
                   (article_id, stock_code, stock_name, stock_full_name,
                    section, mention_snippet, mention_type, confidence)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (article_id, stock_code, stock_name, stock_full_name,
                 section, mention_snippet, mention_type, confidence),
            )
            conn.commit()
            return cursor.lastrowid

    def get_wechat_articles(self, account: Optional[str] = None, limit: int = 20) -> list[dict]:
        """Get WeChat articles, optionally filtered by account."""
        with self._get_conn() as conn:
            if account:
                rows = conn.execute(
                    "SELECT * FROM wechat_articles WHERE account = ? ORDER BY fetched_at DESC LIMIT ?",
                    (account, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM wechat_articles ORDER BY fetched_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_latest_wechat_article(self, account: str) -> Optional[dict]:
        """Get the most recent article for an account (to avoid duplicates)."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM wechat_articles WHERE account = ? ORDER BY posted_at DESC LIMIT 1",
                (account,),
            ).fetchone()
            return dict(row) if row else None

    def mark_wechat_analyzed(self, article_id: int) -> bool:
        """Mark a wechat article as analyzed (卡片已推送)."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE wechat_articles SET is_analyzed = 1 WHERE id = ?",
                (article_id,),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_article_stock_refs(self, article_id: Optional[int] = None,
                                days: int = 7) -> list[dict]:
        """Get stock references, optionally by article or within N days."""
        with self._get_conn() as conn:
            if article_id:
                rows = conn.execute(
                    """SELECT r.*, a.title as article_title, a.account
                       FROM article_stock_refs r
                       JOIN wechat_articles a ON r.article_id = a.id
                       WHERE r.article_id = ?
                       ORDER BY r.created_at DESC""",
                    (article_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT r.*, a.title as article_title, a.account
                       FROM article_stock_refs r
                       JOIN wechat_articles a ON r.article_id = a.id
                       WHERE a.fetched_at >= datetime('now', ?)
                       ORDER BY a.fetched_at DESC, r.section""",
                    (f'-{days} days',),
                ).fetchall()
            return [dict(r) for r in rows]

    # ─── Stock Tracking (15-day) ──────────────────────────────────

    def ensure_tracking_stock(self, stock_code: str, stock_name: str = "",
                               source_account: str = "凡尘一灯",
                               article_id: int = 0) -> bool:
        """Register or refresh a stock in the 15-day tracking system.

        - If the stock is NEW: creates a tracking record starting today.
        - If the stock ALREADY tracked: resets the 15-day timer (updates track_started_at).

        Returns True if timer was reset (already existed), False if newly created.
        """
        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM stock_tracking WHERE stock_code = ? AND source_account = ?",
                (stock_code, source_account),
            ).fetchone()
            if existing:
                # Stock already tracked → reset 15-day timer AND clear Day1 data
                # (so fresh Day1 data gets captured for the new tracking period)
                conn.execute(
                    """UPDATE stock_tracking
                       SET track_started_at = datetime('now'),
                           article_id = ?,
                           day1_price = NULL,
                           day1_change_pct = NULL,
                           day1_trend = '',
                           day1_suggestion = '',
                           updated_at = datetime('now')
                       WHERE stock_code = ? AND source_account = ?""",
                    (article_id, stock_code, source_account),
                )
                conn.commit()
                return True  # timer reset
            else:
                # New stock → create tracking record
                conn.execute(
                    """INSERT INTO stock_tracking
                       (stock_code, stock_name, source_account, article_id,
                        track_started_at)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (stock_code, stock_name, source_account, article_id),
                )
                conn.commit()
                return False  # newly created

    def update_tracking_daily_data(self, stock_code: str, source_account: str = "凡尘一灯",
                                    day1_price: float = 0, day1_change_pct: float = 0,
                                    day1_trend: str = "", day1_suggestion: str = "") -> bool:
        """Update Day 1 data for a tracking record (only sets if not already set)."""
        with self._get_conn() as conn:
            # Only update day1_* if they are currently NULL (first time)
            cursor = conn.execute(
                """UPDATE stock_tracking
                   SET day1_price = CASE WHEN day1_price IS NULL THEN ? ELSE day1_price END,
                       day1_change_pct = CASE WHEN day1_change_pct IS NULL THEN ? ELSE day1_change_pct END,
                       day1_trend = CASE WHEN day1_trend = '' THEN ? ELSE day1_trend END,
                       day1_suggestion = CASE WHEN day1_suggestion = '' THEN ? ELSE day1_suggestion END,
                       updated_at = datetime('now')
                   WHERE stock_code = ? AND source_account = ?""",
                (day1_price, day1_change_pct, day1_trend, day1_suggestion,
                 stock_code, source_account),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_active_tracked_stocks(self, source_account: str = "凡尘一灯",
                                   track_days: int = 15) -> list[dict]:
        """Get all active (within track_days) tracked stocks with Day 1 data.

        Returns:
            List of dicts with stock info, Day 1 data, and days since tracking started.
        """
        SKIP_CODES = ("%066%", "%0034%")
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT t.*,
                          a.title as article_title,
                          CAST(julianday('now') - julianday(t.track_started_at) AS INTEGER) as track_day
                   FROM stock_tracking t
                   LEFT JOIN wechat_articles a ON t.article_id = a.id
                   WHERE t.source_account = ?
                     AND t.track_started_at >= datetime('now', ?)
                     AND t.stock_code NOT LIKE ?
                     AND t.stock_code NOT LIKE ?
                   ORDER BY t.track_started_at DESC""",
                (source_account, f'-{track_days} days') + SKIP_CODES,
            ).fetchall()
            return [dict(r) for r in rows]

    def get_tracking_summary(self, source_account: str = "凡尘一灯",
                              track_days: int = 15) -> dict:
        """Get summary stats for tracking system."""
        with self._get_conn() as conn:
            active = conn.execute(
                """SELECT COUNT(*) as cnt FROM stock_tracking
                   WHERE source_account = ? AND track_started_at >= datetime('now', ?)""",
                (source_account, f'-{track_days} days'),
            ).fetchone()[0]
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM stock_tracking WHERE source_account = ?",
                (source_account,),
            ).fetchone()[0]
            return {"active": active, "total": total}

    def update_sqsm_history(self, stock_code: str, source_account: str, score: int,
                             date_str: str) -> bool:
        """Append today's sqsm score to tracking history (keeps last 10 entries)."""
        import json
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT sqsm_history FROM stock_tracking WHERE stock_code = ? AND source_account = ?",
                (stock_code, source_account),
            ).fetchone()
            history = json.loads(row["sqsm_history"]) if row and row["sqsm_history"] else []
            # Replace if same date exists, otherwise append
            new_entry = {"date": date_str, "score": score}
            updated = False
            for i, e in enumerate(history):
                if e.get("date") == date_str:
                    history[i] = new_entry
                    updated = True
                    break
            if not updated:
                history.append(new_entry)
            # Keep last 10 entries
            history = history[-10:]
            conn.execute(
                "UPDATE stock_tracking SET sqsm_history = ?, updated_at = datetime('now') "
                "WHERE stock_code = ? AND source_account = ?",
                (json.dumps(history, ensure_ascii=False), stock_code, source_account),
            )
            conn.commit()
            return True

    def get_sqsm_history(self, stock_code: str, source_account: str) -> list:
        """Get sqsm history for a tracked stock."""
        import json
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT sqsm_history FROM stock_tracking WHERE stock_code = ? AND source_account = ?",
                (stock_code, source_account),
            ).fetchone()
            return json.loads(row["sqsm_history"]) if row and row["sqsm_history"] else []

    def get_wechat_recommended_stocks(self, days: int = 3) -> list[dict]:
        """Get aggregated stock recommendations from recent articles."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """SELECT r.stock_code, r.stock_full_name, r.stock_name,
                          GROUP_CONCAT(DISTINCT r.section) as sections,
                          COUNT(DISTINCT r.article_id) as mention_count,
                          MAX(a.title) as latest_article,
                          MAX(a.fetched_at) as last_mentioned
                   FROM article_stock_refs r
                   JOIN wechat_articles a ON r.article_id = a.id
                   WHERE a.fetched_at >= datetime('now', ?) AND r.confidence >= 0.6
                   GROUP BY r.stock_code
                   ORDER BY mention_count DESC, last_mentioned DESC""",
                (f'-{days} days',),
            ).fetchall()
            return [dict(r) for r in rows]
