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
        logger.info("Database initialized at %s", self.db_path)

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

    def get_today_articles(self, limit: int = 50) -> list[Article]:
        with self._get_conn() as conn:
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

    def close(self) -> None:
        pass  # SQLite connections are auto-closed via context manager
