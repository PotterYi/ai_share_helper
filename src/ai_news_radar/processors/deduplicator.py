"""
Article deduplication using URL normalization and title similarity.
"""

import logging
from urllib.parse import urlparse, urlunparse

from ..models import Article, RawArticle
from ..utils.helpers import normalize_url, similarity_score

logger = logging.getLogger(__name__)


class Deduplicator:
    """Deduplicate articles by URL and title similarity."""

    def __init__(
        self,
        title_similarity_threshold: float = 0.8,
        url_similarity_threshold: float = 0.95,
    ):
        self.title_similarity_threshold = title_similarity_threshold
        self.url_similarity_threshold = url_similarity_threshold

    def filter_new(
        self,
        incoming: list[Article],
        existing_urls: set[str],
        existing_titles: list[str],
    ) -> list[Article]:
        """Filter incoming articles, keeping only genuinely new ones."""
        new_articles = []
        for article in incoming:
            norm_url = normalize_url(article.url)

            # Check exact URL match
            if norm_url in existing_urls:
                logger.debug("Duplicate URL: %s", article.title[:50])
                continue

            # Check title similarity against existing
            is_dup = False
            for existing_title in existing_titles:
                sim = similarity_score(article.title.lower(), existing_title.lower())
                if sim >= self.title_similarity_threshold:
                    logger.debug(
                        "Duplicate title (%.2f): %s", sim, article.title[:50]
                    )
                    is_dup = True
                    break

            if not is_dup:
                new_articles.append(article)
                existing_urls.add(norm_url)

        logger.info(
            "Dedup: %d new, %d duplicates filtered",
            len(new_articles),
            len(incoming) - len(new_articles),
        )
        return new_articles

    def get_existing_urls(self, db) -> set[str]:
        """Get all existing article URLs from the database."""
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT url FROM articles").fetchall()
        conn.close()
        return {normalize_url(r["url"]) for r in rows}

    def get_existing_titles(self, db, limit: int = 500) -> list[str]:
        """Get recent article titles for similarity comparison."""
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT title FROM articles ORDER BY fetched_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [r["title"] for r in rows]
