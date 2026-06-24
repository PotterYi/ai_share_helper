"""
Markdown report generator for daily/weekly news digests.
"""

import logging
from datetime import datetime
from typing import Optional

from ..models import Article, DailyReport, SourceType

logger = logging.getLogger(__name__)

SOURCE_EMOJI = {
    "anthropic": "Anthropic",
    "github_trending": "GitHub",
    "github_fast_growing": "🚀 GitHub",
    "hacker_news": "HN",
    "reddit_ml": "Reddit",
}

CATEGORY_LABELS = {
    "model_release": "Model Release",
    "research": "Research",
    "tool": "Tool & Framework",
    "safety": "Safety & Ethics",
    "discussion": "Discussion",
    "tutorial": "Tutorial",
    "industry": "Industry News",
    "unknown": "General",
}


class MarkdownReporter:
    """Generate formatted Markdown reports from analyzed articles."""

    def generate_daily_report(
        self,
        articles: list[Article],
        top_n: int = 15,
        title: Optional[str] = None,
    ) -> DailyReport:
        """Generate a daily news digest."""
        sorted_articles = sorted(
            articles, key=lambda a: (a.importance, a.score or 0), reverse=True
        )
        top_articles = sorted_articles[:top_n]

        source_summary = {}
        for article in articles:
            src = article.source_type.value
            source_summary[src] = source_summary.get(src, 0) + 1

        today = datetime.now().strftime("%Y-%m-%d")
        is_project_mode = (
            len(source_summary) == 1
            and "github_fast_growing" in source_summary
        )
        if not title:
            if is_project_mode:
                title = "🚀 AI News Radar - Fastest-Growing GitHub Projects (" + today + ")"
            else:
                title = "AI News Radar - Daily Digest (" + today + ")"
        report_title = title

        content = self._build_report_content(report_title, top_articles, source_summary)

        return DailyReport(
            report_type="daily",
            generated_at=datetime.now(),
            title=report_title,
            content=content,
            article_count=len(articles),
            top_articles=top_articles,
            source_summary=source_summary,
        )

    def generate_weekly_report(
        self,
        articles: list[Article],
        top_n: int = 30,
    ) -> DailyReport:
        """Generate a weekly news digest."""
        sorted_articles = sorted(
            articles, key=lambda a: (a.importance, a.score or 0), reverse=True
        )
        top_articles = sorted_articles[:top_n]

        source_summary = {}
        for article in articles:
            src = article.source_type.value
            source_summary[src] = source_summary.get(src, 0) + 1

        today = datetime.now().strftime("%Y-%m-%d")
        report_title = "AI News Radar - Weekly Digest (" + today + ")"

        content = self._build_report_content(
            report_title, top_articles, source_summary, is_weekly=True
        )

        return DailyReport(
            report_type="weekly",
            generated_at=datetime.now(),
            title=report_title,
            content=content,
            article_count=len(articles),
            top_articles=top_articles,
            source_summary=source_summary,
        )

    def _build_report_content(
        self,
        title: str,
        articles: list[Article],
        source_summary: dict[str, int],
        is_weekly: bool = False,
    ) -> str:
        """Build the full Markdown report."""
        lines = [
            "# " + title,
            "",
            "> Generated at " + datetime.now().strftime("%Y-%m-%d %H:%M"),
            "> Total articles collected: " + str(sum(source_summary.values())),
            "",
            "---",
            "",
            "## Summary by Source",
            "",
            "| Source | Articles |",
            "|--------|----------|",
        ]

        source_names = {
            "anthropic": "Anthropic Blog",
            "github_trending": "GitHub Trending",
            "github_fast_growing": "🚀 GitHub Fast-Growing",
            "hacker_news": "Hacker News",
            "reddit_ml": "Reddit r/ML",
        }

        for src, count in sorted(source_summary.items()):
            emoji = SOURCE_EMOJI.get(src, src)
            name = source_names.get(src, src)
            bar = "#" * min(count, 20)
            lines.append("| " + emoji + " " + name + " | " + str(count) + " " + bar + " |")

        lines.extend([
            "",
            "---",
            "",
            "## Headlines",
            "",
        ])

        # Fast-growing GitHub repos section
        fast_growing = [a for a in articles if a.source_type.value == "github_fast_growing"]
        if fast_growing:
            lines.append("### 🚀 Fastest-Growing GitHub Projects")
            lines.append("")
            lines.append(
                "| Repository | Stars | Age | Language | Growth | Description |"
            )
            lines.append(
                "|------------|-------|-----|----------|--------|-------------|"
            )
            for repo in fast_growing[:10]:
                stars = repo.metadata.get("total_stars", 0) or 0
                age_days = repo.metadata.get("age_days", 999) or 999
                label = repo.metadata.get("growth_label", "")
                if isinstance(age_days, int):
                    if age_days >= 999:
                        age_str = "?"
                    elif age_days == 0:
                        age_str = "today"
                    elif age_days < 30:
                        age_str = f"{age_days}d"
                    elif age_days < 365:
                        age_str = f"{age_days // 30}m"
                    else:
                        age_str = f"{age_days // 365}y"
                else:
                    age_str = "?"
                lang = repo.metadata.get("language", "") or "-"
                desc = (repo.raw_content or "")[:65]
                name = repo.title
                if len(name) > 35:
                    name = name[:32] + "…"
                lines.append(
                    f"| [{name}]({repo.url}) "
                    f"| {stars:,} "
                    f"| {age_str} "
                    f"| {lang} "
                    f"| {label} "
                    f"| {desc} |"
                )
            lines.append("")

        # Top 3 highlighted
        non_fast = [a for a in articles if a.source_type.value != "github_fast_growing"]
        highlight_target = non_fast if non_fast else articles
        if len(highlight_target) >= 3:
            lines.append("### Top Stories")
            lines.append("")
            for i, article in enumerate(highlight_target[:3]):
                lines.extend(self._format_highlighted_article(article, i + 1))

        # Rest as list (exclude fast-growing repos shown separately)
        non_fast_list = [a for a in articles if a.source_type.value != "github_fast_growing"]
        more_news = [a for a in non_fast_list if a not in highlight_target[:3]]
        if more_news:
            lines.append("")
            lines.append("### More News")
            lines.append("")
            for article in more_news:
                lines.extend(self._format_article(article))

        lines.extend([
            "",
            "---",
            "",
            "*Report generated by AI News Radar*",
        ])

        return "\n".join(lines)

    def _format_highlighted_article(self, article: Article, rank: int) -> list[str]:
        """Format a highlighted (top) article."""
        emoji = SOURCE_EMOJI.get(article.source_type.value, "")
        cat_label = CATEGORY_LABELS.get(article.category.value, "")
        stars = "*" * min(5, int(article.importance * 5))

        result = [
            "### " + str(rank) + ". " + cat_label + " " + article.title,
            "",
            "- **Source**: " + emoji + " " + article.source_type.value,
            "- **Importance**: " + stars + " (" + str(round(article.importance, 2)) + ")",
            "- **Category**: " + article.category.value,
        ]

        if article.score:
            result.append(
                "- **Score**: " + str(article.score)
                + " upvotes | " + str(article.comment_count) + " comments"
            )

        if article.summary:
            result.append("- **Summary**: " + article.summary)

        result.append("- **Link**: " + article.url)
        result.append("")

        return result

    def _format_article(self, article: Article) -> list[str]:
        """Format a regular article."""
        emoji = SOURCE_EMOJI.get(article.source_type.value, "")
        cat_label = CATEGORY_LABELS.get(article.category.value, "")

        parts = ["- " + cat_label + " **" + article.title + "**"]
        if article.summary:
            parts.append("  " + article.summary[:150])
        parts.append(
            "  " + emoji + " [" + article.source_type.value + "]"
            " | importance: " + str(round(article.importance, 2))
            + " | " + article.url
        )
        return parts
