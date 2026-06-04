"""
CLI entry point for AI News Radar.
"""

import asyncio
import logging
import sys
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown

from . import __version__
from .engine import Engine
from .scheduler import run_scheduler
from .config import is_configured
from .utils.helpers import setup_logging

app = typer.Typer(
    name="ai-news",
    help="AI News Radar - AI-powered news aggregation and analysis",
    add_completion=False,
)

console = Console()
logger = setup_logging()


def _print_banner():
    """Print the ASCII art banner."""
    banner = r"""
    ╔══════════════════════════════════════════╗
    ║        AI News Radar v{version}            ║
    ║   AI-powered news aggregation & analysis ║
    ╚══════════════════════════════════════════╝
    """.format(version=__version__)
    console.print(banner, style="bold cyan")


@app.command()
def run(
    backend: str = typer.Option("auto", help="AI backend: deepseek, openai, anthropic, or auto"),
    analyze: bool = typer.Option(True, help="Run AI analysis on articles"),
    notify: bool = typer.Option(False, help="Send notifications after report"),
    no_report: bool = typer.Option(False, help="Skip report generation"),
):
    """Run the full pipeline: scrape + analyze + report."""
    _print_banner()

    if analyze and not is_configured():
        console.print(
            "[yellow]Warning: No API key configured. AI analysis will be disabled.[/yellow]"
        )
        console.print(
            "[dim]Set DEEPSEEK_API_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY in .env to enable analysis.[/dim]"
        )
        analyze = False

    engine = Engine(backend=backend, analyze=analyze, notify=notify)

    async def _run():
        if no_report:
            count = await engine.scrape_only()
            console.print(f"[green]Scraped {count} articles.[/green]")
        else:
            result = await engine.run_full_pipeline()
            _print_result(result)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
    finally:
        engine.close()


@app.command()
def scrape():
    """Only scrape news feeds, skip analysis and reporting."""
    engine = Engine(analyze=False, notify=False)

    async def _scrape():
        count = await engine.scrape_only()
        console.print(f"[green]Scraped and stored {count} articles.[/green]")

    try:
        asyncio.run(_scrape())
    finally:
        engine.close()


@app.command()
def analyze(
    limit: int = typer.Option(50, help="Max articles to analyze"),
    backend: str = typer.Option("auto", help="AI backend: deepseek, openai, anthropic, or auto"),
):
    """Analyze unprocessed articles using AI."""
    engine = Engine(backend=backend, analyze=True)

    async def _analyze():
        count = await engine.analyze_only(limit=limit)
        console.print(f"[green]Analyzed {count} articles.[/green]")

    try:
        asyncio.run(_analyze())
    finally:
        engine.close()


@app.command()
def digest(
    top_n: int = typer.Option(15, help="Number of top articles in digest"),
    notify: bool = typer.Option(False, help="Send notifications"),
    output: Optional[str] = typer.Option(None, help="Save report to file"),
):
    """Generate a news digest from today's articles."""
    engine = Engine(analyze=False, notify=notify)

    async def _digest():
        report = await engine.report_only(top_n=top_n)
        if report:
            # Display to terminal
            md = Markdown(report.content)
            console.print(md)

            if output:
                with open(output, "w", encoding="utf-8") as f:
                    f.write(report.content)
                console.print(f"\n[green]Report saved to {output}[/green]")
        else:
            console.print("[yellow]No articles found for today. Run 'ai-news scrape' first.[/yellow]")

    try:
        asyncio.run(_digest())
    finally:
        engine.close()


@app.command()
def list(
    source: Optional[str] = typer.Option(
        None, help="Filter by source: anthropic, github_trending, hacker_news, reddit_ml"
    ),
    category: Optional[str] = typer.Option(None, help="Filter by category"),
    min_score: float = typer.Option(0.0, help="Minimum importance score (0.0-1.0)"),
    sentiment: Optional[str] = typer.Option(None, help="Filter by sentiment: positive, neutral, negative"),
    limit: int = typer.Option(30, help="Max articles to show"),
    offset: int = typer.Option(0, help="Offset for pagination"),
):
    """List articles with optional filters."""
    from .database import Database

    db = Database()
    articles = db.get_articles(
        source_type=source,
        category=category,
        min_importance=min_score,
        sentiment=sentiment,
        limit=limit,
        offset=offset,
        analyzed_only=True,
    )

    if not articles:
        console.print("[yellow]No articles found matching the filters.[/yellow]")
        return

    table = Table(title="Articles", show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Title", style="bold", max_width=60)
    table.add_column("Source", width=14)
    table.add_column("Importance", width=10)
    table.add_column("Category", width=14)

    for i, article in enumerate(articles, 1):
        score_style = (
            "red" if article.importance >= 0.8
            else "yellow" if article.importance >= 0.5
            else "dim"
        )
        table.add_row(
            str(i),
            article.title[:60],
            article.source_type.value,
            f"[{score_style}]{article.importance:.2f}[/{score_style}]",
            article.category.value,
        )

    console.print(table)


@app.command()
def stats():
    """Show database statistics."""
    from .database import Database

    db = Database()
    stats = db.get_stats()

    console.print(Panel.fit("Database Statistics", style="bold cyan"))
    console.print(f"  Total articles:    {stats['total_articles']}")
    console.print(f"  Analyzed:          {stats['analyzed_articles']}")
    console.print(f"  Added today:       {stats['articles_today']}")
    console.print(f"  By source:")

    source_names = {
        "anthropic": "Anthropic Blog",
        "github_trending": "GitHub Trending",
        "hacker_news": "Hacker News",
        "reddit_ml": "Reddit r/ML",
    }

    for src, count in sorted(stats["by_source"].items()):
        name = source_names.get(src, src)
        bar = "█" * min(count, 30)
        console.print(f"    {name:20s} {count:4d} {bar}")


@app.command()
def schedule(
    daily_time: str = typer.Option("09:00", help="Daily report time (HH:MM)"),
    interval: int = typer.Option(4, help="Scrape interval in hours"),
    backend: str = typer.Option("auto", help="AI backend"),
    analyze: bool = typer.Option(True, help="Enable AI analysis"),
    notify: bool = typer.Option(False, help="Enable notifications"),
):
    """Start the scheduler for periodic news collection."""
    _print_banner()
    console.print(
        f"[cyan]Scheduler starting: scrape every {interval}h, daily report at {daily_time}[/cyan]"
    )
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    run_scheduler(
        backend=backend,
        analyze=analyze,
        notify=notify,
        daily_time=daily_time,
        scrape_interval_hours=interval,
    )


@app.command()
def version():
    """Show version information."""
    console.print(f"AI News Radar v{__version__}")


def _print_result(result):
    """Pretty-print pipeline results."""
    console.print()

    # Summary panel
    duration = (result.finished_at - result.started_at).total_seconds() if result.finished_at else 0
    summary = Text()
    summary.append(f"Pipeline completed in {duration:.1f}s\n", style="bold green")
    summary.append(f"  Fetched: {result.total_fetched} | ")
    summary.append(f"New: {result.total_new} | ")
    summary.append(f"Analyzed: {result.analyzed_count}")
    console.print(Panel(summary, title="Result", style="green"))

    if result.report:
        console.print()
        md = Markdown(result.report.content)
        console.print(md)

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for error in result.errors:
            console.print(f"  [red]- {error}[/red]")


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
