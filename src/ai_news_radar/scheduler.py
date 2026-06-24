"""
Task scheduler for periodic news fetching and reporting.

Uses APScheduler for reliable cron-based scheduling.
"""

import asyncio
import logging
import signal
import sys
from datetime import datetime, time
from typing import Optional, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore

logger = logging.getLogger(__name__)


class Scheduler:
    """APScheduler-based scheduler for periodic news collection and reporting."""

    def __init__(self, engine, daily_time: str = "09:00"):
        """
        Args:
            engine: The Engine instance to use for tasks.
            daily_time: Time for daily report (HH:MM format).
        """
        self.engine = engine
        self.daily_time = daily_time
        self._scheduler: Optional[AsyncIOScheduler] = None

        # Parse daily time
        hour, minute = map(int, daily_time.split(":"))
        self._daily_hour = hour
        self._daily_minute = minute

    async def _scrape_job(self):
        """Run the full scrape pipeline."""
        logger.info("[APScheduler] Running scheduled scrape...")
        try:
            await self.engine.run_full_pipeline()
            logger.info("[APScheduler] Scrape completed")
        except Exception as e:
            logger.error("[APScheduler] Scrape failed: %s", e)

    async def _report_job(self):
        """Generate daily report."""
        logger.info("[APScheduler] Generating daily report...")
        try:
            await self.engine.report_only()
            logger.info("[APScheduler] Daily report generated")
        except Exception as e:
            logger.error("[APScheduler] Report failed: %s", e)

    def start(self, scrape_interval_hours: int = 4) -> None:
        """Start the scheduler with APScheduler cron triggers.

        Args:
            scrape_interval_hours: How often to scrape (in hours).
        """
        logger.info(
            "Scheduler starting: scrape every %dh, daily report at %s",
            scrape_interval_hours,
            self.daily_time,
        )

        jobstores = {"default": MemoryJobStore()}
        self._scheduler = AsyncIOScheduler(jobstores=jobstores, timezone="Asia/Shanghai")

        # Scrape job — runs every N hours
        self._scheduler.add_job(
            self._scrape_job,
            trigger=IntervalTrigger(hours=scrape_interval_hours),
            id="news_scrape",
            name=f"Scrape news every {scrape_interval_hours}h",
            replace_existing=True,
        )

        # Daily report job — runs at specified time
        self._scheduler.add_job(
            self._report_job,
            trigger=CronTrigger(
                hour=self._daily_hour,
                minute=self._daily_minute,
                timezone="Asia/Shanghai",
            ),
            id="daily_report",
            name=f"Daily report @ {self.daily_time}",
            replace_existing=True,
            misfire_grace_time=600,
        )

        self._scheduler.start()
        logger.info("APScheduler started with %d jobs", len(self._scheduler.get_jobs()))

    def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self._scheduler and self._scheduler.running:
            logger.info("Stopping scheduler...")
            self._scheduler.shutdown(wait=True)
            self.engine.close()
            logger.info("Scheduler stopped.")


def run_scheduler(
    backend: str = "auto",
    analyze: bool = True,
    notify: bool = False,
    daily_time: str = "09:00",
    scrape_interval_hours: int = 4,
):
    """Entry point to run the scheduler (blocking)."""
    from .engine import Engine

    engine = Engine(backend=backend, analyze=analyze, notify=notify)
    scheduler = Scheduler(engine, daily_time=daily_time)

    def _signal_handler():
        logger.info("Signal received, shutting down...")
        scheduler.stop()
        sys.exit(0)

    # Setup signal handlers (not available on Windows)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass  # Windows
    except Exception:
        pass

    try:
        scheduler.start(scrape_interval_hours=scrape_interval_hours)
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        scheduler.stop()
        loop.close()
